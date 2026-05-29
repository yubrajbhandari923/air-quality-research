"""
APIConverter — ingest readings submitted via the REST API.

Sensors send JSON payloads like:
{
  "serial_number": "81432434001",
  "timestamp": "2026-01-15T08:30:00Z",
  "readings": [
    {"pollutant": "PM25", "value": 45.2, "unit": "µg/m³"},
    {"pollutant": "PM10", "value": 62.1, "unit": "µg/m³"},
    {"pollutant": "TEMP", "value": 18.5, "unit": "°C"},
    {"pollutant": "RH",   "value": 72.0, "unit": "%"}
  ]
}

This converter is used by the API view to normalise and persist live readings.
"""
import logging
from datetime import timezone as dt_timezone
from typing import Any

import pandas as pd
import pytz

from apps.ingestion.base import BaseDataConverter, NEPAL_TZ

logger = logging.getLogger(__name__)


class APIConverter(BaseDataConverter):
    """
    Converts a single API JSON payload into canonical readings.

    Unlike file-based converters, `source` is a dict (the validated POST data).
    """

    source_name = "APIConverter"
    source_type = "LIVE_API"

    def validate_source(self, source: dict) -> bool:
        required = {"serial_number", "timestamp", "readings"}
        missing = required - set(source.keys())
        if missing:
            logger.warning("APIConverter: missing keys %s", missing)
            return False
        if not isinstance(source["readings"], list) or len(source["readings"]) == 0:
            logger.warning("APIConverter: 'readings' must be a non-empty list")
            return False
        return True

    def parse_metadata(self, source: dict) -> dict:
        return {
            "serial_number": source["serial_number"],
            "is_indoor": source.get("is_indoor", False),
        }

    def normalize_timestamps(self, df: pd.DataFrame) -> pd.DataFrame:
        # Not used for API converter — timestamps are handled inline in map_columns
        return df

    def _read_source(self, source: dict) -> pd.DataFrame:
        # For API payloads, build a DataFrame from the readings list
        rows = []
        ts_str = source["timestamp"]
        for r in source["readings"]:
            rows.append({
                "timestamp_raw": ts_str,
                "pollutant": r.get("pollutant", ""),
                "raw_value": r.get("value"),
                "unit": r.get("unit", ""),
            })
        return pd.DataFrame(rows)

    def map_columns(self, df: pd.DataFrame) -> list[dict]:
        records = []
        for _, row in df.iterrows():
            try:
                ts = pd.to_datetime(row["timestamp_raw"], utc=True).tz_convert(NEPAL_TZ)
            except Exception:
                logger.warning("Could not parse timestamp: %s", row["timestamp_raw"])
                continue

            records.append({
                "original_ts": ts,
                "timezone": "Asia/Kathmandu",
                "interval_seconds": None,
                "pollutant": row["pollutant"],
                "unit": row["unit"],
                "raw_value": row.get("raw_value"),
                "cleaned_value": None,
                "source_type": self.source_type,
            })
        return records

    def assign_quality_flags(self, records: list[dict]) -> list[dict]:
        for rec in records:
            val = rec.get("raw_value")
            flag = "UNVALIDATED"
            reason = ""

            if val is None:
                flag = "MISSING"
            elif rec["pollutant"] == "PM25":
                if val < 0:
                    flag, reason = "BAD", "Negative PM2.5"
                elif val > 500:
                    flag, reason = "SUSPECT", f"PM2.5={val} exceeds 500 µg/m³"
                else:
                    flag = "GOOD"
            elif rec["pollutant"] in ("PM1", "PM4", "PM10"):
                flag = "GOOD" if val >= 0 else "BAD"
            elif rec["pollutant"] == "TEMP":
                flag = "GOOD" if -10 <= val <= 55 else "SUSPECT"
            elif rec["pollutant"] == "RH":
                flag = "GOOD" if 0 <= val <= 100 else "SUSPECT"
            elif rec["pollutant"] in ("CO2", "TVOC", "BARO"):
                flag = "UNVALIDATED"
            else:
                flag = "UNVALIDATED"

            rec["quality_flag"] = flag
            rec["flag_reason"] = reason
        return records

    def run(self, source: dict) -> dict:
        """
        Ingest a live API payload.

        The sensor and site must already exist in the database (registered via API).
        """
        from apps.sensors.models import Sensor
        from apps.readings.models import IngestionLog

        if not self.validate_source(source):
            return {"saved": 0, "duplicates": 0, "errors": 1, "status": "FAILED"}

        serial = source["serial_number"]
        try:
            sensor = Sensor.objects.select_related("site").get(serial_number=serial)
        except Sensor.DoesNotExist:
            logger.error("APIConverter: sensor %s not registered", serial)
            return {
                "saved": 0, "duplicates": 0, "errors": 1,
                "status": "FAILED",
                "error": f"Sensor {serial} not registered. Use /api/v1/sensors/register/ first.",
            }

        df = self._read_source(source)
        records = self.map_columns(df)
        records = self.assign_quality_flags(records)

        for rec in records:
            rec["sensor_id"] = sensor.pk
            rec["site_id"] = sensor.site_id
            rec["is_indoor"] = sensor.is_indoor

        # Write to DuckDB raw store (analytical store for historical queries)
        from apps.ingestion.raw_store import raw_store
        try:
            saved, duplicates = raw_store.append_batch(records)
            result = {"saved": saved, "duplicates": duplicates, "errors": 0}
        except Exception as exc:
            logger.error("APIConverter: raw_store write failed: %s", exc)
            result = {"saved": 0, "duplicates": 0, "errors": len(records)}

        # Also write to CanonicalReading for live 1-day chart queries (fast index scan).
        # Rows older than 2 days are purged during aggregation runs, keeping this table small.
        try:
            from apps.readings.models import CanonicalReading
            to_create = []
            for rec in records:
                to_create.append(CanonicalReading(
                    sensor_id=rec["sensor_id"],
                    site_id=rec.get("site_id"),
                    original_ts=rec["original_ts"],
                    pollutant=rec["pollutant"],
                    unit=rec.get("unit", ""),
                    raw_value=rec.get("raw_value"),
                    quality_flag=rec.get("quality_flag", "UNVALIDATED"),
                    flag_reason=rec.get("flag_reason", ""),
                    is_indoor=rec.get("is_indoor", False),
                    source_type=rec.get("source_type", "LIVE_API"),
                    is_duplicate=False,
                ))
            CanonicalReading.objects.bulk_create(to_create, ignore_conflicts=True)
        except Exception as exc:
            logger.warning("APIConverter: CanonicalReading mirror write failed: %s", exc)

        result["status"] = "SUCCESS" if result["errors"] == 0 else "PARTIAL"

        IngestionLog.objects.create(
            source_name=self.source_name,
            status=result["status"],
            records_attempted=len(records),
            records_saved=result["saved"],
            records_duplicate=result["duplicates"],
            records_error=result["errors"],
        )

        return result
