"""
Management command: clear_data

Remove all readings, aggregates, datasets, sensors, and sites from the database.
Superuser accounts are preserved.

Usage:
    python manage.py clear_data            # prompts for confirmation
    python manage.py clear_data --yes      # skip confirmation (for scripts)
"""
from django.core.management.base import BaseCommand

from apps.readings.models import CanonicalReading, DailyAggregate, Dataset
from apps.sensors.models import Sensor, Site


class Command(BaseCommand):
    help = "Remove all sensor readings, aggregates, sensors, and sites. Preserves user accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip confirmation prompt.",
        )

    def handle(self, *args, **options):
        if not options["yes"]:
            confirm = input(
                "This will permanently delete ALL readings, sensors, and sites. "
                "Type 'yes' to continue: "
            )
            if confirm.strip().lower() != "yes":
                self.stdout.write(self.style.WARNING("Aborted."))
                return

        self.stdout.write("Deleting readings…")
        n_readings = CanonicalReading.objects.all().delete()[0]

        self.stdout.write("Deleting daily aggregates…")
        n_agg = DailyAggregate.objects.all().delete()[0]

        self.stdout.write("Deleting datasets…")
        Dataset.objects.all().delete()

        self.stdout.write("Deleting sensors…")
        Sensor.objects.all().delete()

        self.stdout.write("Deleting sites…")
        Site.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(
            f"Done. Removed {n_readings:,} readings, {n_agg:,} daily aggregates, "
            "all sensors and sites. User accounts preserved."
        ))
