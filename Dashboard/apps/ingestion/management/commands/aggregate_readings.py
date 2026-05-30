"""
aggregate_readings — Cron 1b (runs every 10 minutes via Render Cron Job).

Recomputes TenMinAggregate, HourlyAggregate, and DailyAggregate from
CanonicalReading (Postgres).  Idempotent — safe to run as often as needed.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Recompute 10-min / hourly / daily aggregate tables from raw CanonicalReading rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sensor-id", type=int, default=None,
            help="Restrict aggregation to a single sensor PK (default: all sensors).",
        )
        parser.add_argument(
            "--since", type=str, default=None,
            help="Only aggregate from this ISO-8601 datetime onward (e.g. 2026-01-01T00:00:00Z).",
        )

    def handle(self, *args, **options):
        from apps.ingestion.tasks import compute_aggregates
        from django.utils.dateparse import parse_datetime

        since = None
        if options["since"]:
            since = parse_datetime(options["since"])
            if since is None:
                self.stderr.write(f"Invalid --since value: {options['since']}")
                return

        result = compute_aggregates(sensor_id=options["sensor_id"], since=since)

        self.stdout.write(
            f"Done — {result['sensors_processed']} sensor(s): "
            f"{result['tenmin_rows']} 10-min rows, "
            f"{result['hourly_rows']} hourly rows, "
            f"{result['daily_rows']} daily rows, "
            f"{result['errors']} error(s)."
        )
