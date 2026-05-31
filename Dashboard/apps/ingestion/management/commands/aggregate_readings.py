"""
aggregate_readings — Cron 2 (runs every 10 minutes via Render Cron Job).

Recomputes TenMinAggregate, HourlyAggregate, and DailyAggregate.

Source of raw data depends on AQ_INGESTION settings:
  db_raw_enabled=True  → reads from CanonicalReading (Postgres)
  db_raw_enabled=False → downloads R2 parquet for each sensor and aggregates

The --since flag limits how far back to recompute, which keeps each cron run
fast.  Default look-back is 60 days (covers any gap from a missed run).

Use --since 2025-01-01T00:00:00Z to do a full historical backfill from R2.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Recompute 10-min / hourly / daily aggregate tables."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sensor-id", type=int, default=None,
            help="Restrict aggregation to a single sensor PK (default: all sensors).",
        )
        parser.add_argument(
            "--since", type=str, default=None,
            help=(
                "Only aggregate from this ISO-8601 datetime onward "
                "(e.g. 2026-01-01T00:00:00Z).  Default: 60 days ago."
            ),
        )

    def handle(self, *args, **options):
        from apps.ingestion.tasks import compute_aggregates
        from apps.ingestion.base import _aq_cfg
        from django.utils.dateparse import parse_datetime

        cfg    = _aq_cfg()
        db_raw = cfg.get("db_raw_enabled", True)
        source = "CanonicalReading (Postgres)" if db_raw else "R2 parquet"
        self.stdout.write(f"Source: {source}")

        since = None
        if options["since"]:
            since = parse_datetime(options["since"])
            if since is None:
                self.stderr.write(f"Invalid --since value: {options['since']}")
                return
            self.stdout.write(f"Since: {since.isoformat()}")

        result = compute_aggregates(sensor_id=options["sensor_id"], since=since)

        self.stdout.write(
            f"Done — {result['sensors_processed']} sensor(s): "
            f"{result['tenmin_rows']} 10-min rows, "
            f"{result['hourly_rows']} hourly rows, "
            f"{result['daily_rows']} daily rows, "
            f"{result['errors']} error(s)."
        )
