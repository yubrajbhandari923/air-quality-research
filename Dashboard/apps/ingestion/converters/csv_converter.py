"""
BelauriCSVConverter — ingest Particles Plus CSV exports from Belauri, Nepal.

Two CSV formats exist in the dataset:
  1. Telemetry format (81432434001_telemetry.csv, 81442406076_telemetry.csv):
       Columns: timestamp, device_model, device_serial, NC 0.5, NC 1.0, …, PM 1.0, PM 10, PM 2.5, …
       Timestamp format: ISO 8601 UTC (2026-03-01T00:00:26Z)
       Interval: ~1 minute

  2. H1/H2 export format (e.g. 81432434001-2025-H2.csv):
       Columns: Timestamp, Device ID, Serial Number, Model, …, PM1.0, PM2.5, …, CO2, VOC tVOC, …
       Timestamp format: MM/DD/YYYY HH:MM:SS (assumed UTC per column header row)
       Interval: ~1 minute

The converter detects the format automatically from the header.

Column mapping:
  Telemetry:                 H1/H2 export:
  "PM 1.0"      → PM1       "PM1.0"        → PM1
  "PM 2.5"      → PM25      "PM2.5"        → PM25
  "PM 4.0"      → PM4       "PM4.0"        → PM4
  "PM 10"       → PM10      "PM10"         → PM10
  "NC 0.5"      → NC05      "PM0.5 NC"     → NC05
  "NC 1.0"      → NC1       "PM1.0 NC"     → NC1
  "NC 2.5"      → NC25      "PM2.5 NC"     → NC25
  "Temperature" → TEMP      "Temperature"  → TEMP
  "Relative Humidity" → RH  "Relative Humidity" → RH
  (no baro)                  "Barometric Pressure" → BARO
  (no CO2)                   "CO2"          → CO2
  (no VOC)                   "VOC tVOC measurement" → TVOC

Quality flag thresholds (based on sensor spec + field experience):
  PM2.5 > 500 µg/m³  → SUSPECT (likely saturation)
  PM2.5 < 0          → BAD
  Temperature < -10 or > 55 °C → SUSPECT
  CO2 < 300 or > 5000 ppm → SUSPECT
"""
import logging
import os
from pathlib import Path

import pandas as pd
import pytz

from apps.ingestion.base import BaseDataConverter, NEPAL_TZ

logger = logging.getLogger(__name__)

# Pollutant ranges for quality flagging
PM25_MAX_GOOD = 500.0   # µg/m³ — above this is SUSPECT (sensor saturation)
TEMP_MIN = -10.0        # °C
TEMP_MAX = 55.0         # °C
CO2_MIN = 300.0         # ppm
CO2_MAX = 5000.0        # ppm

# ── Column maps ───────────────────────────────────────────────────────────────
TELEMETRY_COLUMN_MAP = {
    "PM 1.0":           ("PM1",  "µg/m³"),
    "PM 2.5":           ("PM25", "µg/m³"),
    "PM 4.0":           ("PM4",  "µg/m³"),
    "PM 10":            ("PM10", "µg/m³"),
    "NC 0.5":           ("NC05", "#/cm³"),
    "NC 1.0":           ("NC1",  "#/cm³"),
    "NC 2.5":           ("NC25", "#/cm³"),
    "Temperature":      ("TEMP", "°C"),
    "Relative Humidity":("RH",   "%"),
}

EXPORT_COLUMN_MAP = {
    "PM1.0":                    ("PM1",  "µg/m³"),
    "PM2.5":                    ("PM25", "µg/m³"),
    "PM4.0":                    ("PM4",  "µg/m³"),
    "PM10":                     ("PM10", "µg/m³"),
    "PM0.5 NC":                 ("NC05", "#/cm³"),
    "PM1.0 NC":                 ("NC1",  "#/cm³"),
    "PM2.5 NC":                 ("NC25", "#/cm³"),
    "Temperature":              ("TEMP", "°C"),
    "Relative Humidity":        ("RH",   "%"),
    "Barometric Pressure":      ("BARO", "inHg"),
    "CO2":                      ("CO2",  "ppm"),
    "VOC tVOC measurement":     ("TVOC", "mg/m³"),
}

# Known sensor metadata
SENSOR_META = {
    "81432434001": {
        "model": "8143",
        "friendly_name": "BS-14 Outdoor Belauri",
        "is_indoor": False,
        "manufacturer": "Particles Plus",
    },
    "81442406076": {
        "model": "8144",
        "friendly_name": "AA-Indoor Belauri",
        "is_indoor": True,
        "manufacturer": "Particles Plus",
    },
    "81442326017": {
        "model": "8144",
        "friendly_name": "AA-2 Belauri",
        "is_indoor": True,
        "manufacturer": "Particles Plus",
    },
    "81442410021": {
        "model": "8144",
        "friendly_name": "AA-3 Belauri",
        "is_indoor": True,
        "manufacturer": "Particles Plus",
    },
}


def _is_telemetry_format(df: pd.DataFrame) -> bool:
    """Return True if the DataFrame looks like the telemetry export format."""
    return "timestamp" in [c.lower() for c in df.columns] and "PM 1.0" in df.columns


class BelauriCSVConverter(BaseDataConverter):
    """
    Ingest a single Belauri CSV file (telemetry or H1/H2 export format).

    Usage:
        converter = BelauriCSVConverter()
        result = converter.run("/path/to/81432434001_telemetry.csv")
    """

    source_name = "BelauriCSVConverter"
    source_type = "CSV_UPLOAD"

    def validate_source(self, source) -> bool:
        path = Path(source)
        if not path.exists():
            logger.error("File not found: %s", path)
            return False
        if path.suffix.lower() != ".csv":
            logger.error("Not a CSV file: %s", path)
            return False
        if path.stat().st_size == 0:
            logger.error("Empty file: %s", path)
            return False
        return True

    def parse_metadata(self, source) -> dict:
        """Extract serial number, model, and indoor flag from filename or file contents."""
        path = Path(source)
        # Try to read the serial from the filename first (most reliable)
        # Files are named like: 81432434001_telemetry.csv or 81432434001-2025-H2.csv
        stem = path.stem.split("_")[0].split("-")[0]
        meta = SENSOR_META.get(stem, {})
        return {
            "serial_number": stem,
            "model": meta.get("model", "unknown"),
            "friendly_name": meta.get("friendly_name", stem),
            "is_indoor": meta.get("is_indoor", False),
            "manufacturer": meta.get("manufacturer", "Unknown"),
        }

    def normalize_timestamps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Parse timestamps to UTC-aware datetimes, then convert to Asia/Kathmandu.

        Telemetry format: ISO 8601 with 'Z' suffix → UTC.
        Export format: MM/DD/YYYY HH:MM:SS, header says UTC.
        """
        if _is_telemetry_format(df):
            ts_col = next(c for c in df.columns if c.lower() == "timestamp")
            df["original_ts"] = pd.to_datetime(df[ts_col], utc=True).dt.tz_convert(NEPAL_TZ)
        else:
            # Export format: first row after header is a units row — skip rows where
            # Timestamp equals "UTC" or is NaN
            df = df[~df["Timestamp"].isin(["UTC", ""])].copy()
            df = df.dropna(subset=["Timestamp"])
            df["original_ts"] = pd.to_datetime(
                df["Timestamp"], format="%m/%d/%Y %H:%M:%S", utc=True
            ).dt.tz_convert(NEPAL_TZ)
        return df

    def _read_source(self, source) -> pd.DataFrame:
        """
        Read CSV, skipping the units row present in H1/H2 export files.

        The export format has:
          Row 0: column headers
          Row 1: units (e.g. "UTC", "ug/m3", …)
          Row 2+: data
        We read normally and let normalize_timestamps() filter out the units row.
        """
        return pd.read_csv(source, low_memory=False)

    def map_columns(self, df: pd.DataFrame) -> list[dict]:
        """
        Produce one canonical dict per (timestamp, pollutant) pair.

        Each row in the source CSV typically contains multiple pollutants;
        we explode these into one record per pollutant.
        """
        is_telemetry = _is_telemetry_format(df)
        col_map = TELEMETRY_COLUMN_MAP if is_telemetry else EXPORT_COLUMN_MAP

        # Extract serial number to look up sensor
        if is_telemetry:
            serial_col = next(c for c in df.columns if c.lower() == "device_serial")
            df["_serial"] = df[serial_col].astype(str)
        else:
            df["_serial"] = df["Serial Number"].astype(str)

        records = []
        for _, row in df.iterrows():
            serial = str(row["_serial"]).strip()
            meta = SENSOR_META.get(serial, {})
            is_indoor = meta.get("is_indoor", False)

            ts = row["original_ts"]
            if pd.isna(ts):
                continue

            for src_col, (pollutant, unit) in col_map.items():
                if src_col not in df.columns:
                    continue
                raw = row.get(src_col)
                try:
                    val = None if pd.isna(raw) else float(raw)
                except (TypeError, ValueError):
                    val = None

                records.append({
                    "original_ts": ts,
                    "timezone": "Asia/Kathmandu",
                    "interval_seconds": 60,
                    "pollutant": pollutant,
                    "unit": unit,
                    "raw_value": val,
                    "cleaned_value": None,
                    "_serial": serial,
                    "is_indoor": is_indoor,
                    "source_type": self.source_type,
                })

        return records

    def assign_quality_flags(self, records: list[dict]) -> list[dict]:
        """Apply domain-knowledge quality flags to each record."""
        for rec in records:
            flag = "UNVALIDATED"
            reason = ""

            val = rec.get("raw_value")
            if val is None:
                flag = "MISSING"
                reason = "No value reported by sensor."
            elif rec["pollutant"] == "PM25":
                if val < 0:
                    flag = "BAD"
                    reason = "PM2.5 is negative — sensor error."
                elif val > PM25_MAX_GOOD:
                    flag = "SUSPECT"
                    reason = f"PM2.5 = {val:.1f} µg/m³ exceeds saturation threshold {PM25_MAX_GOOD}."
                else:
                    flag = "GOOD"
            elif rec["pollutant"] in ("PM1", "PM4", "PM10"):
                if val < 0:
                    flag = "BAD"
                    reason = "Negative PM value — sensor error."
                else:
                    flag = "GOOD"
            elif rec["pollutant"] == "TEMP":
                if val < TEMP_MIN or val > TEMP_MAX:
                    flag = "SUSPECT"
                    reason = f"Temperature {val:.1f} °C outside expected range [{TEMP_MIN}, {TEMP_MAX}]."
                else:
                    flag = "GOOD"
            elif rec["pollutant"] == "RH":
                if val < 0 or val > 100:
                    flag = "SUSPECT"
                    reason = f"Relative humidity {val:.1f}% is out of range [0, 100]."
                else:
                    flag = "GOOD"
            elif rec["pollutant"] == "CO2":
                if val < CO2_MIN or val > CO2_MAX:
                    flag = "SUSPECT"
                    reason = f"CO2 {val:.0f} ppm outside expected range [{CO2_MIN}, {CO2_MAX}]."
                else:
                    flag = "GOOD"
            else:
                flag = "UNVALIDATED"

            rec["quality_flag"] = flag
            rec["flag_reason"] = reason

        return records

    def run(self, source) -> dict:
        """
        Override run() to resolve sensor/site FKs before saving.

        Looks up or creates the Sensor and Site in the database based on the
        serial number in the CSV.  This allows the management command to call
        run() without pre-creating sensors.
        """
        from django.db import transaction
        from apps.sensors.models import Sensor, Site
        from apps.readings.models import Dataset, IngestionLog

        path = Path(source)

        if not self.validate_source(path):
            return {"saved": 0, "duplicates": 0, "errors": 1, "status": "FAILED"}

        metadata = self.parse_metadata(path)
        serial = metadata["serial_number"]

        # Ensure site exists
        site, _ = Site.objects.get_or_create(
            name="Belauri",
            defaults={
                "district": "Kanchanpur",
                "municipality": "Belauri Municipality",
                "province": "Sudurpashchim Pradesh",
                "latitude": 28.6844,
                "longitude": 80.3646,
                "elevation_m": 200,
                "land_use_type": "RESIDENTIAL",
                "description": (
                    "Belauri municipality in Kanchanpur district, far-western Nepal. "
                    "Agricultural/residential setting. Study site for indoor/outdoor PM2.5 research."
                ),
            },
        )

        # Ensure sensor exists
        sensor, created = Sensor.objects.get_or_create(
            serial_number=serial,
            defaults={
                "friendly_name": metadata["friendly_name"],
                "model": metadata["model"],
                "manufacturer": metadata["manufacturer"],
                "site": site,
                "is_indoor": metadata["is_indoor"],
                "status": "ACTIVE",
                "power_type": "GRID",
                "connectivity_type": "WIFI",
            },
        )
        if created:
            logger.info("Created sensor %s (%s)", serial, metadata["friendly_name"])

        # Create dataset
        dataset = Dataset.objects.create(
            name=f"Belauri CSV: {path.name}",
            source_type=self.source_type,
            source_file=str(path),
        )

        try:
            df = self._read_source(path)
            df = self.normalize_timestamps(df)
            records = self.map_columns(df)
            records = self.assign_quality_flags(records)

            # Inject FK ids
            for rec in records:
                rec["sensor_id"] = sensor.pk
                rec["site_id"] = site.pk
                rec["dataset_id"] = dataset.pk

            result = self.save_to_canonical_schema(records)

            dataset.record_count = result["saved"]
            dataset.save(update_fields=["record_count"])

            status = (
                IngestionLog.Status.SUCCESS if result["errors"] == 0
                else IngestionLog.Status.PARTIAL
            )

            IngestionLog.objects.create(
                source_name=self.source_name,
                source_file=str(path),
                dataset=dataset,
                status=status,
                records_attempted=len(records),
                records_saved=result["saved"],
                records_duplicate=result["duplicates"],
                records_error=result["errors"],
            )

            logger.info(
                "[%s] %s — saved=%d, duplicates=%d, errors=%d",
                self.source_name, path.name, result["saved"], result["duplicates"], result["errors"],
            )
            result["status"] = status
            return result

        except Exception as exc:
            logger.exception("[%s] Failed on %s: %s", self.source_name, path, exc)
            IngestionLog.objects.create(
                source_name=self.source_name,
                source_file=str(path),
                dataset=dataset,
                status=IngestionLog.Status.FAILED,
                records_error=1,
                error_detail=str(exc),
            )
            return {"saved": 0, "duplicates": 0, "errors": 1, "status": "FAILED", "error": str(exc)}
