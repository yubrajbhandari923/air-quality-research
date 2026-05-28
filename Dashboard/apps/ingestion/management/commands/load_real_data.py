"""
Management command: load_real_data

Ingests Data/fig7_raw.csv — the filtered minute-by-minute dataset for both
Belauri sensors covering the Figure 7 analysis window.

Safe to run on every deploy — if the file is already in IngestionLog with
status SUCCESS or PARTIAL it is skipped.

Usage:
    python manage.py load_real_data            # ingest (skip if already done)
    python manage.py load_real_data --dry-run  # show what would run, no DB writes
    python manage.py load_real_data --force    # re-ingest even if already loaded
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.ingestion.converters.csv_converter import BelauriCSVConverter


# Repo root is one level above BASE_DIR (Dashboard/)
REPO_ROOT = Path(settings.BASE_DIR).parent
DATA_FILE = REPO_ROOT / "Data" / "fig7_raw.csv"


def _collect_files() -> list[Path]:
    if DATA_FILE.exists():
        return [DATA_FILE]
    return []


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

        files = _collect_files()
        if not files:
            self.stderr.write(self.style.ERROR(
                f"File not found: {DATA_FILE}\n"
                "Run: python scripts/export_fig7_raw.py   (from the repo root)"
            ))
            return

        self.stdout.write(f"Found {len(files)} file(s) to process")

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
