"""
archive_to_r2 — Cron 2 (runs once per day via Render Cron Job).

Exports raw CanonicalReading rows older than ARCHIVE_AFTER_DAYS to
Cloudflare R2 as compressed JSONL (one file per sensor per day), then
deletes them from Postgres to keep the database lean.

R2 key format:
    archive/sensor_<serial>/<YYYY>/<YYYY-MM-DD>.jsonl.gz

Postgres is never the only copy: we write to R2 first, verify the upload
succeeds, then delete.  If R2 is unreachable the rows stay in Postgres.

Aggregate tables (TenMinAggregate, HourlyAggregate, DailyAggregate) are
NOT deleted — they stay in Postgres forever as the fast query layer.
"""
import gzip
import json
import logging
import tempfile
from datetime import date, datetime, timedelta, timezone as dt_tz
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)

BATCH = 50_000   # rows fetched from Postgres per iteration


class Command(BaseCommand):
    help = "Archive old CanonicalReading rows to R2 and delete them from Postgres."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int,
            default=getattr(settings, "ARCHIVE_AFTER_DAYS", 90),
            help="Archive readings older than this many days (default: ARCHIVE_AFTER_DAYS setting).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would be archived without writing to R2 or deleting.",
        )

    def handle(self, *args, **options):
        from apps.sensors.models import Sensor
        from apps.readings.models import CanonicalReading
        from apps.ingestion import r2

        cutoff   = datetime.now(dt_tz.utc) - timedelta(days=options["days"])
        dry_run  = options["dry_run"]
        sensors  = list(Sensor.objects.select_related("site").all())

        total_archived = total_deleted = 0

        for sensor in sensors:
            # Find distinct dates that have archivable rows for this sensor.
            dates = list(
                CanonicalReading.objects
                .filter(sensor=sensor, original_ts__lt=cutoff)
                .dates("original_ts", "day")
            )
            if not dates:
                continue

            self.stdout.write(f"Sensor {sensor.serial_number}: {len(dates)} day(s) to archive")

            for day in dates:
                day_start = datetime(day.year, day.month, day.day, tzinfo=dt_tz.utc)
                day_end   = day_start + timedelta(days=1)

                qs = CanonicalReading.objects.filter(
                    sensor=sensor,
                    original_ts__gte=day_start,
                    original_ts__lt=day_end,
                ).order_by("original_ts")

                rows = list(qs.values(
                    "original_ts", "pollutant", "unit", "raw_value", "cleaned_value",
                    "quality_flag", "flag_reason", "is_indoor", "source_type",
                    "interval_seconds", "is_duplicate",
                ))
                if not rows:
                    continue

                key = (
                    f"archive/sensor_{sensor.serial_number}"
                    f"/{day.year}/{day.isoformat()}.jsonl.gz"
                )

                if dry_run:
                    self.stdout.write(f"  [dry-run] would write {len(rows)} rows → {key}")
                    total_archived += len(rows)
                    continue

                # Build JSONL.GZ in memory (each row is a JSON line).
                try:
                    jsonl_gz = _rows_to_jsonl_gz(rows)
                    r2.put_bytes(key, jsonl_gz, content_type="application/gzip")
                except Exception as exc:
                    logger.error(
                        "R2 upload failed for %s/%s — skipping deletion: %s",
                        sensor.serial_number, day, exc,
                    )
                    continue

                # R2 write confirmed — safe to delete from Postgres.
                deleted, _ = qs.delete()
                total_archived += len(rows)
                total_deleted  += deleted
                logger.info("Archived %d rows → %s, deleted %d from Postgres", len(rows), key, deleted)

        action = "Would archive" if dry_run else "Archived"
        self.stdout.write(
            f"{action} {total_archived:,} reading(s) "
            f"({'dry-run' if dry_run else f'deleted {total_deleted:,} from Postgres'})"
        )


def _rows_to_jsonl_gz(rows: list[dict]) -> bytes:
    """Serialize a list of row dicts to a gzip-compressed JSONL byte string."""
    lines = []
    for row in rows:
        d = {}
        for k, v in row.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
            elif isinstance(v, date):
                d[k] = v.isoformat()
            else:
                d[k] = v
        lines.append(json.dumps(d, separators=(",", ":")))
    raw = "\n".join(lines).encode("utf-8")
    return gzip.compress(raw, compresslevel=6)
