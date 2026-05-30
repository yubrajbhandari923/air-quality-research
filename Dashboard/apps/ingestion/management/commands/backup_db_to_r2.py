"""
backup_db_to_r2 — Cron 4 (runs daily at 02:30 UTC via Render Cron Job).

Creates a full Django dumpdata backup of all app tables (excluding the raw
CanonicalReading rows — those are already covered by archive_to_r2) and
uploads it to R2 as a gzip-compressed JSON fixture.

R2 key format:
    backups/YYYY-MM-DD.json.gz

Restore:
    1. Download the backup from R2
    2. gunzip backup.json.gz
    3. python manage.py migrate
    4. python manage.py loaddata backup.json
    5. Re-upload and re-process any CSVs from R2 uploads/ prefix
    6. python manage.py aggregate_readings

Flags:
    --keep-days N    Delete backups older than N days from R2 (default: 30)
    --dry-run        Show what would be backed up without uploading
"""
import gzip
import io
import logging
from datetime import datetime, timedelta, timezone as dt_tz

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

# App labels to include in the backup (excludes raw readings — too large,
# already covered by archive_to_r2).
BACKUP_APPS = [
    "users",
    "sensors",
    "readings.dataset",
    "readings.ingestionlog",
    "readings.tenminaggregate",
    "readings.hourlyaggregate",
    "readings.dailyaggregate",
    "ingestion.ingestionjob",
    "api",
    "exposure",
    "documents",
    "analysis",
    "auth.user",
    "axes",
]


class Command(BaseCommand):
    help = "Back up Django app data to Cloudflare R2 as a gzip JSON fixture."

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep-days", type=int, default=30,
            help="Delete R2 backups older than this many days (default: 30).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would be backed up without writing to R2.",
        )

    def handle(self, *args, **options):
        from apps.ingestion import r2

        dry_run   = options["dry_run"]
        keep_days = options["keep_days"]
        today     = datetime.now(dt_tz.utc).strftime("%Y-%m-%d")
        r2_key    = f"backups/{today}.json.gz"

        if dry_run:
            self.stdout.write(f"[dry-run] Would upload backup → {r2_key}")
            self.stdout.write(f"[dry-run] Would keep last {keep_days} days of backups.")
            return

        # ── Dump selected app data to an in-memory buffer ─────────────────────
        self.stdout.write("Dumping app data...")
        buf = io.StringIO()
        try:
            call_command(
                "dumpdata",
                *BACKUP_APPS,
                indent=None,          # compact JSON — smaller files
                stdout=buf,
                verbosity=0,
            )
        except Exception as exc:
            logger.error("dumpdata failed: %s", exc)
            self.stderr.write(f"dumpdata failed: {exc}")
            return

        raw_json = buf.getvalue().encode("utf-8")
        compressed = gzip.compress(raw_json, compresslevel=6)
        size_kb = len(compressed) / 1024

        # ── Upload to R2 ──────────────────────────────────────────────────────
        self.stdout.write(f"Uploading {size_kb:.1f} KB → {r2_key} ...")
        try:
            r2.put_bytes(r2_key, compressed, content_type="application/gzip")
        except Exception as exc:
            logger.error("R2 upload failed for backup %s: %s", r2_key, exc)
            self.stderr.write(f"R2 upload failed: {exc}")
            return

        self.stdout.write(self.style.SUCCESS(f"Backup uploaded: {r2_key} ({size_kb:.1f} KB)"))

        # ── Prune old backups ─────────────────────────────────────────────────
        self._prune_old_backups(keep_days)

    def _prune_old_backups(self, keep_days: int):
        from apps.ingestion import r2

        cutoff = datetime.now(dt_tz.utc) - timedelta(days=keep_days)
        deleted = 0

        try:
            client  = r2._client()
            bucket  = __import__("django.conf", fromlist=["settings"]).settings.R2_BUCKET_NAME
            paginator = client.get_paginator("list_objects_v2")

            for page in paginator.paginate(Bucket=bucket, Prefix="backups/"):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Key format: backups/YYYY-MM-DD.json.gz
                    try:
                        date_str = key.split("/")[1][:10]
                        backup_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=dt_tz.utc)
                    except (IndexError, ValueError):
                        continue

                    if backup_date < cutoff:
                        client.delete_object(Bucket=bucket, Key=key)
                        deleted += 1
                        logger.info("Deleted old backup: %s", key)

        except Exception as exc:
            logger.warning("Backup pruning failed (non-fatal): %s", exc)

        if deleted:
            self.stdout.write(f"Pruned {deleted} backup(s) older than {keep_days} days.")
