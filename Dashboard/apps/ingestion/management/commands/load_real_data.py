"""
Management command: load_real_data

Ingests all Belauri CSV files that have not yet been successfully loaded.
Safe to run on every deploy — files already in IngestionLog with status SUCCESS
or PARTIAL are skipped.

Data directory is resolved relative to the repo root (one level above BASE_DIR),
so it works both locally and on Render (where the full repo is cloned).

Usage:
    python manage.py load_real_data            # ingest new files
    python manage.py load_real_data --dry-run  # show what would run, no DB writes
    python manage.py load_real_data --force    # re-ingest even already-loaded files
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.ingestion.converters.csv_converter import BelauriCSVConverter


# Repo root is one level above BASE_DIR (Dashboard/)
REPO_ROOT = Path(settings.BASE_DIR).parent
DATA_DIR  = REPO_ROOT / "Data" / "Belauri"

# Glob patterns that find all Belauri H1/H2 export CSVs, including the
# sensor-group subdirectory.  Telemetry files are excluded — they duplicate
# the H1/H2 data and have a different timestamp format.
CSV_GLOBS = [
    DATA_DIR.glob("81432434001-20*.csv"),
    (DATA_DIR / "81442326017-81442406076-81442410021").glob("8144*-20*.csv"),
]


def _collect_files() -> list[Path]:
    files = []
    for glob in CSV_GLOBS:
        files.extend(sorted(glob))
    return files


def _already_ingested(path: Path) -> bool:
    """Return True if this file was previously ingested with SUCCESS or PARTIAL."""
    from apps.readings.models import IngestionLog
    return IngestionLog.objects.filter(
        source_file=str(path),
        status__in=[IngestionLog.Status.SUCCESS, IngestionLog.Status.PARTIAL],
    ).exists()


class Command(BaseCommand):
    help = "Idempotently ingest all Belauri CSV files (skips already-loaded files)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show which files would be ingested without writing to the DB.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-ingest files even if they are already in IngestionLog.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force   = options["force"]

        if not DATA_DIR.exists():
            self.stderr.write(self.style.ERROR(
                f"Data directory not found: {DATA_DIR}\n"
                "Make sure the repo was cloned with the Data/ directory present."
            ))
            return

        files = _collect_files()
        if not files:
            self.stdout.write(self.style.WARNING("No CSV files found."))
            return

        self.stdout.write(f"Found {len(files)} CSV file(s) in {DATA_DIR}")

        converter = BelauriCSVConverter()
        total_saved = total_dupes = total_errors = 0

        for path in files:
            if not force and _already_ingested(path):
                self.stdout.write(f"  SKIP  {path.name} (already ingested)")
                continue

            if dry_run:
                self.stdout.write(f"  WOULD INGEST  {path.name}")
                continue

            self.stdout.write(f"  Ingesting {path.name} …", ending=" ")
            self.stdout.flush()

            result = converter.run(str(path))
            saved  = result.get("saved", 0)
            dupes  = result.get("duplicates", 0)
            errors = result.get("errors", 0)
            status = result.get("status", "UNKNOWN")

            total_saved  += saved
            total_dupes  += dupes
            total_errors += errors

            msg = f"saved={saved:,}, dupes={dupes:,}, errors={errors} [{status}]"
            if status == "SUCCESS":
                self.stdout.write(self.style.SUCCESS(msg))
            elif status == "PARTIAL":
                self.stdout.write(self.style.WARNING(msg))
            else:
                self.stdout.write(self.style.ERROR(msg))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone: saved={total_saved:,}, duplicates={total_dupes:,}, errors={total_errors}"
            ))
