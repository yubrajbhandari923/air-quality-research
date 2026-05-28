"""
Management command: run_ingestion

Usage:
    # Single file — format auto-detected from CSV headers
    python manage.py run_ingestion --file /path/to/file.csv

    # Override auto-detection with a built-in source name
    python manage.py run_ingestion --file /path/to/file.csv --source belauri_csv

    # Override with a custom converter class (dotted import path)
    python manage.py run_ingestion --file /path/to/file.csv --source myapp.converters.MyConverter

    # Bulk scan default data directories (all known formats)
    python manage.py run_ingestion

    # Dry run — validate without saving
    python manage.py run_ingestion --file /path/to/file.csv --dry-run
"""
import importlib
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.ingestion.converters.csv_converter import BelauriCSVConverter
from apps.ingestion.converters.openaq_converter import OpenAQConverter

# Built-in short names → converter classes
BUILTIN_SOURCES = {
    "belauri_csv": BelauriCSVConverter,
    "openaq":      OpenAQConverter,
}


def _load_converter(source: str):
    """Resolve --source to a converter instance.

    Accepts either a built-in short name ('belauri_csv', 'openaq') or a
    dotted import path to any BaseDataConverter subclass
    ('myapp.converters.MyConverter').
    """
    if source in BUILTIN_SOURCES:
        return BUILTIN_SOURCES[source]()

    # Treat as dotted import path: 'package.module.ClassName'
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
    """Return the right converter instance by peeking at the CSV header row."""
    try:
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
            header = f.readline().lower()
    except OSError:
        return None

    # Belauri telemetry: device_serial column + space-separated PM names
    if "device_serial" in header and "pm 1.0" in header:
        return BelauriCSVConverter()

    # Belauri H1/H2 export: serial number column + pm1.0 (no space)
    if "serial number" in header and "pm1.0" in header:
        return BelauriCSVConverter()

    # OpenAQ bulk CSV
    if "location_id" in header or "locationid" in header:
        return OpenAQConverter()

    return None


class Command(BaseCommand):
    help = "Ingest air quality data. Format is auto-detected from CSV headers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default=None,
            help="CSV file to ingest. If omitted, scans default data directories.",
        )
        parser.add_argument(
            "--source",
            default=None,
            help=(
                "Override auto-detection. Built-in names: belauri_csv, openaq. "
                "Or a dotted import path to a custom converter: 'myapp.converters.MyConverter'."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate without saving to the database.",
        )

    def handle(self, *args, **options):
        specific_file = options.get("file")
        source        = options.get("source")
        dry_run       = options["dry_run"]

        # Resolve explicit --source to a converter (raises CommandError on bad input)
        forced_converter = _load_converter(source) if source else None

        if specific_file:
            self._ingest_file(Path(specific_file), dry_run, forced_converter)
        else:
            self._ingest_directories(dry_run, forced_converter)

    # ── single file ───────────────────────────────────────────────────────────

    def _ingest_file(self, path: Path, dry_run: bool, forced_converter=None):
        if not path.exists():
            raise CommandError(f"File not found: {path}")
        if path.suffix.lower() != ".csv":
            raise CommandError(f"Not a CSV file: {path}")

        if forced_converter:
            converter = forced_converter
            self.stdout.write(f"Using: {converter.__class__.__name__} — {path.name}")
        else:
            converter = _detect_converter(path)
            if converter is None:
                raise CommandError(
                    f"Could not detect format for {path.name}. "
                    f"Pass --source to specify one. Built-in names: {', '.join(BUILTIN_SOURCES)}"
                )
            self.stdout.write(f"Detected: {converter.__class__.__name__} — {path.name}")

        if dry_run:
            ok = converter.validate_source(path)
            self.stdout.write(
                self.style.SUCCESS("VALID") if ok else self.style.ERROR("INVALID")
            )
            return

        result = converter.run(str(path))
        self._print_result(path.name, result)

    # ── bulk directory scan ───────────────────────────────────────────────────

    def _ingest_directories(self, dry_run: bool, forced_converter=None):
        belauri_dir = Path(settings.BELAURI_DATA_DIR)
        openaq_dir  = Path(settings.OPENAQ_DATA_DIR)

        files = []
        for d in [belauri_dir, openaq_dir]:
            if d.exists():
                files.extend(d.glob("*.csv"))
                for sub in d.iterdir():
                    if sub.is_dir():
                        files.extend(sub.glob("*.csv"))

        if not files:
            raise CommandError("No CSV files found in configured data directories.")

        self.stdout.write(f"Found {len(files)} CSV file(s).")

        total_saved = total_dupes = total_errors = 0

        for csv_path in sorted(files):
            converter = forced_converter or _detect_converter(csv_path)
            if converter is None:
                self.stdout.write(
                    self.style.WARNING(f"  Skipping {csv_path.name} — unrecognised format")
                )
                continue

            self.stdout.write(f"  {csv_path.name} …", ending=" ")

            if dry_run:
                ok = converter.validate_source(csv_path)
                self.stdout.write(self.style.SUCCESS("VALID") if ok else self.style.ERROR("INVALID"))
                continue

            result = converter.run(str(csv_path))
            total_saved  += result.get("saved", 0)
            total_dupes  += result.get("duplicates", 0)
            total_errors += result.get("errors", 0)
            self._print_result(csv_path.name, result, short=True)

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone: saved={total_saved:,}, duplicates={total_dupes:,}, errors={total_errors}"
            ))

    # ── helpers ───────────────────────────────────────────────────────────────

    def _print_result(self, name: str, result: dict, short: bool = False):
        saved  = result.get("saved", 0)
        dupes  = result.get("duplicates", 0)
        errors = result.get("errors", 0)
        status = result.get("status", "UNKNOWN")
        msg    = f"saved={saved:,}, dupes={dupes:,}, errors={errors} [{status}]"
        if not short:
            msg = f"  {name} — {msg}"
        if status == "SUCCESS":
            self.stdout.write(self.style.SUCCESS(msg))
        elif status == "PARTIAL":
            self.stdout.write(self.style.WARNING(msg))
        else:
            self.stdout.write(self.style.ERROR(msg))
            if "error" in result:
                self.stderr.write(f"    {result['error']}")
