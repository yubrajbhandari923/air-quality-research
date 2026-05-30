"""
Management command: run_ingestion

Useful for CLI-based testing: ingest a local CSV file directly,
bypassing the upload portal and IngestionJob queue.

For production use, upload CSVs via the portal and let the
process_ingestion_jobs cron handle the import.

Usage:
    python manage.py run_ingestion --file /path/to/file.csv
    python manage.py run_ingestion --file /path/to/file.csv --source belauri_csv
    python manage.py run_ingestion --file /path/to/file.csv --dry-run
"""
import importlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.ingestion.converters.csv_converter import BelauriCSVConverter
from apps.ingestion.converters.generic_csv_converter import GenericCSVConverter
from apps.ingestion.converters.openaq_converter import OpenAQConverter

BUILTIN_SOURCES = {
    "generic":     GenericCSVConverter,
    "belauri_csv": BelauriCSVConverter,
    "openaq":      OpenAQConverter,
}


def _load_converter(source: str):
    if source in BUILTIN_SOURCES:
        return BUILTIN_SOURCES[source]()
    try:
        module_path, class_name = source.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        return cls()
    except (ValueError, ImportError, AttributeError) as exc:
        raise CommandError(
            f"Could not load converter '{source}': {exc}\n"
            f"Built-in names: {', '.join(BUILTIN_SOURCES)}"
        )


def _detect_converter(csv_path: Path):
    """Peek at CSV header to choose the right converter."""
    try:
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
            header = f.readline().lower()
    except OSError:
        return None

    if "device_serial" in header and "pm 1.0" in header:
        return BelauriCSVConverter()
    if "serial number" in header and "pm1.0" in header:
        return BelauriCSVConverter()
    if "location_id" in header or "locationid" in header:
        return OpenAQConverter()
    return GenericCSVConverter()


class Command(BaseCommand):
    help = "Ingest a CSV file directly (bypasses the upload portal). Format auto-detected."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", required=True,
            help="Path to the CSV file to ingest.",
        )
        parser.add_argument(
            "--source", default=None,
            help=(
                "Override auto-detection. Built-in names: "
                + ", ".join(BUILTIN_SOURCES)
            ),
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Validate without saving to the database.",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")
        if path.suffix.lower() != ".csv":
            raise CommandError(f"Not a CSV file: {path}")

        if options["source"]:
            converter = _load_converter(options["source"])
            self.stdout.write(f"Using: {converter.__class__.__name__} — {path.name}")
        else:
            converter = _detect_converter(path)
            self.stdout.write(f"Detected: {converter.__class__.__name__} — {path.name}")

        if options["dry_run"]:
            ok = converter.validate_source(path)
            self.stdout.write(
                self.style.SUCCESS("VALID") if ok else self.style.ERROR("INVALID")
            )
            return

        result = converter.run(str(path))
        saved  = result.get("saved", 0)
        dupes  = result.get("duplicates", 0)
        errors = result.get("errors", 0)
        status = result.get("status", "UNKNOWN")
        msg = f"saved={saved:,}, duplicates={dupes:,}, errors={errors} [{status}]"

        if status == "SUCCESS":
            self.stdout.write(self.style.SUCCESS(msg))
        elif status == "PARTIAL":
            self.stdout.write(self.style.WARNING(msg))
        else:
            self.stdout.write(self.style.ERROR(msg))
            if result.get("error"):
                self.stderr.write(f"  {result['error']}")
