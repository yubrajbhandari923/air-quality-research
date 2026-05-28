"""
Management command: run_ingestion

Ingest actual data from supported sources.

Usage:
    # Ingest all Belauri CSV files
    python manage.py run_ingestion --source belauri_csv

    # Ingest specific file
    python manage.py run_ingestion --source belauri_csv --file /path/to/file.csv

    # Ingest OpenAQ Nepal CSV
    python manage.py run_ingestion --source openaq

    # Dry run (validate only, don't save)
    python manage.py run_ingestion --source belauri_csv --dry-run
"""
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.ingestion.converters.csv_converter import BelauriCSVConverter
from apps.ingestion.converters.openaq_converter import OpenAQConverter


class Command(BaseCommand):
    help = "Ingest air quality data from CSV files or APIs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            required=True,
            choices=["belauri_csv", "openaq"],
            help="Which data source to ingest.",
        )
        parser.add_argument(
            "--file",
            default=None,
            help="Specific file to ingest (overrides default directory scan).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate source files but do not save to DB.",
        )

    def handle(self, *args, **options):
        source = options["source"]
        specific_file = options.get("file")
        dry_run = options["dry_run"]

        if source == "belauri_csv":
            self._ingest_belauri(specific_file, dry_run)
        elif source == "openaq":
            self._ingest_openaq(specific_file, dry_run)

    def _ingest_belauri(self, specific_file, dry_run):
        """Ingest Belauri CSV files."""
        converter = BelauriCSVConverter()
        data_dir = Path(settings.BELAURI_DATA_DIR)

        if specific_file:
            files = [Path(specific_file)]
        else:
            # Auto-discover all CSV files in the Belauri data directory
            files = list(data_dir.glob("*.csv"))
            # Also check subdirectory for multi-sensor exports
            for subdir in data_dir.iterdir():
                if subdir.is_dir():
                    files.extend(subdir.glob("*.csv"))

        if not files:
            raise CommandError(f"No CSV files found in {data_dir}")

        self.stdout.write(f"Found {len(files)} CSV file(s) to ingest.")

        total_saved = 0
        total_dupes = 0
        total_errors = 0

        for csv_path in sorted(files):
            self.stdout.write(f"  Processing: {csv_path.name} …", ending=" ")

            if dry_run:
                valid = converter.validate_source(csv_path)
                status = "VALID" if valid else "INVALID"
                self.stdout.write(self.style.SUCCESS(status) if valid else self.style.ERROR(status))
                continue

            result = converter.run(str(csv_path))
            saved = result.get("saved", 0)
            dupes = result.get("duplicates", 0)
            errors = result.get("errors", 0)
            status_str = result.get("status", "UNKNOWN")

            total_saved += saved
            total_dupes += dupes
            total_errors += errors

            msg = f"saved={saved}, dupes={dupes}, errors={errors} [{status_str}]"
            if status_str == "SUCCESS":
                self.stdout.write(self.style.SUCCESS(msg))
            elif status_str == "PARTIAL":
                self.stdout.write(self.style.WARNING(msg))
            else:
                self.stdout.write(self.style.ERROR(msg))
                if "error" in result:
                    self.stderr.write(f"    Error: {result['error']}")

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"\nBelauri CSV ingestion complete: "
                f"saved={total_saved:,}, duplicates={total_dupes:,}, errors={total_errors}"
            ))

    def _ingest_openaq(self, specific_file, dry_run):
        """Ingest OpenAQ CSV data."""
        converter = OpenAQConverter()
        data_dir = Path(settings.OPENAQ_DATA_DIR)

        if specific_file:
            files = [Path(specific_file)]
        else:
            files = list(data_dir.glob("*.csv"))

        if not files:
            raise CommandError(f"No CSV files found in {data_dir}")

        self.stdout.write(f"Found {len(files)} OpenAQ CSV file(s).")

        for csv_path in sorted(files):
            self.stdout.write(f"  Processing: {csv_path.name} …", ending=" ")

            if dry_run:
                valid = converter.validate_source(csv_path)
                self.stdout.write("VALID" if valid else "INVALID")
                continue

            result = converter.run(str(csv_path))
            self.stdout.write(
                f"saved={result.get('saved', 0)}, errors={result.get('errors', 0)} "
                f"[{result.get('status', 'UNKNOWN')}]"
            )
