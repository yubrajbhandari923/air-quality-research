"""
GenericCSVConverter — ingest any Particles Plus / standard export CSV.

Supported column sets
─────────────────────
1. Telemetry format (device export, low-bandwidth):
   Columns: timestamp, device_model, device_serial, NC 0.5, NC 1.0, PM 1.0, PM 2.5, ...
   Timestamp: ISO 8601 UTC  (e.g. 2026-03-01T00:00:26Z)

2. Full export format (Particles Plus cloud / desktop export):
   Columns: Timestamp, Device ID, Serial Number, Model, ..., PM1.0, PM2.5, ..., CO2, ...
   Timestamp: MM/DD/YYYY HH:MM:SS (UTC per the second header row)

Auto-detection picks the format from the header.

Sensor lookup
─────────────
The converter reads `Serial Number` (or `device_serial`) from each CSV row
and looks up the matching Sensor in the database.  Sensors MUST be registered
in the portal before uploading.  Unknown serials are skipped with an error log.

Quality flags
─────────────
Same thresholds as BelauriCSVConverter — applied vectorially for performance.

Calibration columns
───────────────────
Columns like "Applied PM2.5 Custom Calibration Setting - Multiplication Factor"
are metadata (not sensor readings) and are ignored.

AQI columns
───────────
PM2.5 AQI / PM10 AQI are computed values — ignored; raw mass concentrations
are stored instead.
"""
import logging
from io import IOBase
from pathlib import Path
from typing import Union

import pandas as pd

from apps.ingestion.base import BaseDataConverter, NEPAL_TZ

logger = logging.getLogger(__name__)

# ── Quality thresholds ────────────────────────────────────────────────────────
PM25_MAX_GOOD = 500.0
TEMP_MIN, TEMP_MAX = -10.0, 55.0
CO2_MIN, CO2_MAX = 300.0, 5000.0

# ── Column maps  (source column → (canonical pollutant, unit)) ─────────────────
TELEMETRY_COLUMN_MAP = {
    "PM 1.0":            ("PM1",  "µg/m³"),
    "PM 2.5":            ("PM25", "µg/m³"),
    "PM 4.0":            ("PM4",  "µg/m³"),
    "PM 10":             ("PM10", "µg/m³"),
    "NC 0.5":            ("NC05", "#/cm³"),
    "NC 1.0":            ("NC1",  "#/cm³"),
    "NC 2.5":            ("NC25", "#/cm³"),
    "NC 4.0":            ("NC4",  "#/cm³"),
    "NC 10":             ("NC10", "#/cm³"),
    "Temperature":       ("TEMP", "°C"),
    "Relative Humidity": ("RH",   "%"),
}

EXPORT_COLUMN_MAP = {
    "PM1.0":                    ("PM1",  "µg/m³"),
    "PM2.5":                    ("PM25", "µg/m³"),
    "PM4.0":                    ("PM4",  "µg/m³"),
    "PM10":                     ("PM10", "µg/m³"),
    "PM0.5 NC":                 ("NC05", "#/cm³"),
    "PM1.0 NC":                 ("NC1",  "#/cm³"),
    "PM2.5 NC":                 ("NC25", "#/cm³"),
    "PM4.0 NC":                 ("NC4",  "#/cm³"),
    "PM10 NC":                  ("NC10", "#/cm³"),
    "CO2":                      ("CO2",  "ppm"),
    "CH2O":                     ("CH2O", "µg/m³"),
    "CO":                       ("CO",   "ppm"),
    "SO2":                      ("SO2",  "µg/m³"),
    "O3":                       ("O3",   "µg/m³"),
    "NO2":                      ("NO2",  "µg/m³"),
    "VOC tVOC measurement":     ("TVOC", "mg/m³"),
    "Barometric Pressure":      ("BARO", "inHg"),
    "Temperature":              ("TEMP", "°C"),
    "Relative Humidity":        ("RH",   "%"),
}

# Columns in the export format that are calibration metadata or computed values
# — skip them entirely.
_SKIP_PATTERN_FRAGMENTS = [
    "Applied ", " AQI", "Sensor Status", "System Status",
    "Sub Model", "Device ID", "Friendly Name", "Latitude",
    "Longitude", "Is Indoor", "Is Public", "Model",
]


def _is_telemetry_format(df: pd.DataFrame) -> bool:
    cols_lower = [c.lower() for c in df.columns]
    return "timestamp" in cols_lower and "PM 1.0" in df.columns


def _detect_serial_col(df: pd.DataFrame, is_telemetry: bool) -> str:
    if is_telemetry:
        for c in df.columns:
            if c.lower() in ("device_serial", "serial", "serial_number"):
                return c
        raise ValueError("Telemetry CSV: cannot find serial-number column.")
    # Export format
    if "Serial Number" in df.columns:
        return "Serial Number"
    raise ValueError("Export CSV: 'Serial Number' column missing.")


class GenericCSVConverter(BaseDataConverter):
    """
    Ingest a Particles Plus CSV (telemetry or full export) for any registered sensor.

    Usage from the portal view::

        converter = GenericCSVConverter()
        result = converter.run(uploaded_file_or_path, triggered_by=request.user)

    ``source`` may be:
    - a filesystem path (str or Path)
    - a Django InMemoryUploadedFile / TemporaryUploadedFile (file-like)
    """

    source_name = "GenericCSVConverter"
    source_type = "CSV_UPLOAD"

    def __init__(self):
        self._sensor_cache: dict[str, object] = {}   # serial → Sensor
        self._skipped_serials: set[str] = set()       # serials not in DB

    def validate_source(self, source) -> bool:
        if isinstance(source, (str, Path)):
            p = Path(source)
            if not p.exists():
                logger.error("File not found: %s", p)
                return False
            if p.stat().st_size == 0:
                logger.error("Empty file: %s", p)
                return False
        # File-like objects: trust the caller
        return True

    def parse_metadata(self, source) -> dict:
        return {"source_name": str(source)}

    def _read_source(self, source) -> pd.DataFrame:
        return pd.read_csv(source, low_memory=False)

    def normalize_timestamps(self, df: pd.DataFrame) -> pd.DataFrame:
        if _is_telemetry_format(df):
            ts_col = next(c for c in df.columns if c.lower() == "timestamp")
            df["original_ts"] = pd.to_datetime(df[ts_col], utc=True).dt.tz_convert(NEPAL_TZ)
        else:
            # Export format: skip rows where Timestamp is "UTC" or blank
            df = df[~df["Timestamp"].isin(["UTC", ""])].copy()
            df = df.dropna(subset=["Timestamp"])
            df["original_ts"] = pd.to_datetime(
                df["Timestamp"], format="%m/%d/%Y %H:%M:%S", utc=True,
            ).dt.tz_convert(NEPAL_TZ)
        return df

    def _resolve_sensors(self, serials: list[str]) -> dict[str, object]:
        """Look up Sensor objects by serial number. Warn and skip unknowns."""
        from apps.sensors.models import Sensor

        found = {}
        for serial in set(serials):
            serial = str(serial).strip()
            if serial in self._sensor_cache:
                found[serial] = self._sensor_cache[serial]
                continue
            try:
                sensor = Sensor.objects.select_related("site").get(serial_number=serial)
                found[serial] = sensor
                self._sensor_cache[serial] = sensor
            except Sensor.DoesNotExist:
                if serial not in self._skipped_serials:
                    logger.warning(
                        "GenericCSVConverter: serial '%s' not registered — rows skipped. "
                        "Register the sensor in the portal first.", serial
                    )
                    self._skipped_serials.add(serial)
        return found

    def map_columns(self, df: pd.DataFrame) -> list[dict]:
        """Melt wide → long and produce canonical records."""
        is_telemetry = _is_telemetry_format(df)
        col_map = TELEMETRY_COLUMN_MAP if is_telemetry else EXPORT_COLUMN_MAP

        serial_col = _detect_serial_col(df, is_telemetry)
        df = df.rename(columns={serial_col: "_serial"})
        df["_serial"] = df["_serial"].astype(str).str.strip()
        df = df.dropna(subset=["original_ts"])

        # Resolve serials → sensor objects
        sensor_map = self._resolve_sensors(df["_serial"].unique().tolist())
        if not sensor_map:
            logger.error("No registered sensors found in this CSV.")
            return []

        # Keep only rows with known serials
        df = df[df["_serial"].isin(sensor_map)].copy()
        if df.empty:
            return []

        # Only use columns that exist in this CSV and are in the column map
        value_cols = [c for c in col_map if c in df.columns]
        if not value_cols:
            detected = list(df.columns[:20])
            fmt = "telemetry" if is_telemetry else "export"
            logger.error(
                "GenericCSVConverter: no recognized pollutant columns in %s-format CSV. "
                "Expected one of %s. Detected columns: %s",
                fmt, list(col_map.keys()), detected,
            )
            return []

        id_cols = ["original_ts", "_serial"]
        melted = df[id_cols + value_cols].melt(
            id_vars=id_cols,
            value_vars=value_cols,
            var_name="_src_col",
            value_name="raw_value",
        )

        melted["pollutant"] = melted["_src_col"].map(lambda c: col_map[c][0])
        melted["unit"]      = melted["_src_col"].map(lambda c: col_map[c][1])
        melted["raw_value"] = pd.to_numeric(melted["raw_value"], errors="coerce")

        # Attach sensor/site FK ids from lookup
        melted["sensor_id"]  = melted["_serial"].map(lambda s: sensor_map[s].pk)
        melted["site_id"]    = melted["_serial"].map(lambda s: sensor_map[s].site_id)
        melted["is_indoor"]  = melted["_serial"].map(lambda s: sensor_map[s].is_indoor)

        melted["timezone"]         = "Asia/Kathmandu"
        melted["interval_seconds"] = 60
        melted["cleaned_value"]    = None
        melted["source_type"]      = self.source_type

        # Vectorised quality flags
        v = melted["raw_value"]
        flag   = pd.Series("UNVALIDATED", index=melted.index, dtype=object)
        reason = pd.Series("",            index=melted.index, dtype=object)

        missing = v.isna()
        flag[missing]   = "MISSING"
        reason[missing] = "No value reported by sensor."

        pm25 = (melted["pollutant"] == "PM25") & ~missing
        flag[pm25] = "GOOD"
        flag[pm25 & (v < 0)]             = "BAD";    reason[pm25 & (v < 0)]             = "Negative PM2.5."
        flag[pm25 & (v > PM25_MAX_GOOD)] = "SUSPECT"; reason[pm25 & (v > PM25_MAX_GOOD)] = f"PM2.5 > {PM25_MAX_GOOD} µg/m³."

        other_pm = melted["pollutant"].isin(("PM1", "PM4", "PM10")) & ~missing
        flag[other_pm] = "GOOD"
        flag[other_pm & (v < 0)] = "BAD"; reason[other_pm & (v < 0)] = "Negative PM value."

        temp = (melted["pollutant"] == "TEMP") & ~missing
        flag[temp] = "GOOD"
        out_of_range = temp & ((v < TEMP_MIN) | (v > TEMP_MAX))
        flag[out_of_range]   = "SUSPECT"
        reason[out_of_range] = f"Temperature outside [{TEMP_MIN}, {TEMP_MAX}] °C."

        rh = (melted["pollutant"] == "RH") & ~missing
        flag[rh] = "GOOD"
        rh_bad = rh & ((v < 0) | (v > 100))
        flag[rh_bad] = "SUSPECT"; reason[rh_bad] = "RH out of range [0, 100]."

        co2 = (melted["pollutant"] == "CO2") & ~missing
        flag[co2] = "GOOD"
        co2_bad = co2 & ((v < CO2_MIN) | (v > CO2_MAX))
        flag[co2_bad] = "SUSPECT"; reason[co2_bad] = f"CO2 outside [{CO2_MIN}, {CO2_MAX}] ppm."

        melted["quality_flag"] = flag
        melted["flag_reason"]  = reason
        melted["raw_value"]    = melted["raw_value"].astype(object).where(v.notna(), other=None)
        melted = melted.drop(columns=["_src_col"])
        return melted.to_dict("records")

    def assign_quality_flags(self, records: list[dict]) -> list[dict]:
        # Flags assigned inside map_columns() — pass through.
        return records

    def run(self, source, triggered_by=None, dataset_name: str = "") -> dict:
        """
        Full ingestion pipeline for a CSV upload.

        Pipeline:
          1. Parse CSV → long-format records (in memory)
          2. Write to R2 wide parquet (deduplicated, partitioned by month)
          3. If db_raw_enabled=True → write to CanonicalReading
          4. Aggregate from in-memory DataFrame → upsert TenMin/Hourly/Daily in DB
          5. Log Dataset + IngestionLog rows

        Args:
            source:       File path or file-like object.
            triggered_by: CustomUser instance (for audit log).
            dataset_name: Override the Dataset name shown in logs.
        """
        from apps.readings.models import Dataset, IngestionLog
        from apps.ingestion.tasks import compute_aggregates_from_records

        source_label = dataset_name or (
            source.name if hasattr(source, "name") else str(source)
        )

        if not self.validate_source(source):
            return {"saved": 0, "duplicates": 0, "errors": 1, "status": "FAILED",
                    "error": "Invalid or empty file."}

        dataset = Dataset.objects.create(
            name=dataset_name or f"CSV Upload: {source_label}",
            source_type=self.source_type,
            source_file=source_label,
            uploaded_by=triggered_by,
        )

        try:
            total_saved = total_dupes = total_errors = total_attempted = 0
            # Accumulate all records across chunks for a single in-memory aggregate pass
            all_records: list[dict] = []

            for chunk in pd.read_csv(source, low_memory=False, chunksize=5000):
                chunk = self.normalize_timestamps(chunk)
                chunk_records = self.map_columns(chunk)
                chunk_records = self.assign_quality_flags(chunk_records)

                if not chunk_records:
                    continue

                for rec in chunk_records:
                    rec["dataset_id"] = dataset.pk

                total_attempted += len(chunk_records)
                result = self.save_to_canonical_schema(chunk_records)
                total_saved  += result["saved"]
                total_dupes  += result["duplicates"]
                total_errors += result["errors"]
                all_records.extend(chunk_records)

            if total_attempted == 0 and not self._skipped_serials:
                IngestionLog.objects.create(
                    source_name=self.source_name,
                    source_file=source_label,
                    dataset=dataset,
                    status=IngestionLog.Status.FAILED,
                    records_error=1,
                    error_detail="No data rows produced. CSV columns did not match any recognised format.",
                    triggered_by=triggered_by,
                )
                return {
                    "saved": 0, "duplicates": 0, "errors": 1, "status": "FAILED",
                    "error": (
                        "No data rows were produced. "
                        "Check that the CSV uses a recognised column format "
                        "(see the upload page for supported column names) "
                        "and that the file is not empty."
                    ),
                }

            if total_attempted == 0 and self._skipped_serials:
                dataset.notes = "No records matched registered sensors."
                dataset.save(update_fields=["notes"])
                IngestionLog.objects.create(
                    source_name=self.source_name,
                    source_file=source_label,
                    dataset=dataset,
                    status=IngestionLog.Status.FAILED,
                    records_error=1,
                    error_detail=(
                        f"No registered sensors found. Skipped serials: "
                        f"{', '.join(self._skipped_serials) or 'none detected'}"
                    ),
                    triggered_by=triggered_by,
                )
                return {
                    "saved": 0, "duplicates": 0, "errors": 1, "status": "FAILED",
                    "error": (
                        "No rows matched a registered sensor. "
                        f"Unrecognised serials: {', '.join(self._skipped_serials)}. "
                        "Register the sensor(s) in the portal first."
                    ),
                    "skipped_serials": list(self._skipped_serials),
                }

            # ── Aggregate from in-memory records (no R2 download needed) ──────
            if all_records:
                try:
                    compute_aggregates_from_records(all_records, self._sensor_cache)
                except Exception as exc:
                    logger.warning("[GenericCSVConverter] Aggregation failed: %s", exc)

            dataset.record_count = total_saved
            dataset.save(update_fields=["record_count"])

            log_status = (
                IngestionLog.Status.SUCCESS if total_errors == 0
                else IngestionLog.Status.PARTIAL
            )
            IngestionLog.objects.create(
                source_name=self.source_name,
                source_file=source_label,
                dataset=dataset,
                status=log_status,
                records_attempted=total_attempted,
                records_saved=total_saved,
                records_duplicate=total_dupes,
                records_error=total_errors,
                triggered_by=triggered_by,
            )

            return {
                "saved": total_saved,
                "duplicates": total_dupes,
                "errors": total_errors,
                "status": log_status,
                "skipped_serials": list(self._skipped_serials),
                "sensor_ids": [s.pk for s in self._sensor_cache.values()],
            }

        except Exception as exc:
            logger.exception("[GenericCSVConverter] Failed: %s", exc)
            IngestionLog.objects.create(
                source_name=self.source_name,
                source_file=source_label,
                dataset=dataset,
                status=IngestionLog.Status.FAILED,
                records_error=1,
                error_detail=str(exc),
                triggered_by=triggered_by,
            )
            return {"saved": 0, "duplicates": 0, "errors": 1, "status": "FAILED",
                    "error": str(exc), "sensor_ids": []}
