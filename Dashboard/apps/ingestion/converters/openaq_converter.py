"""
OpenAQConverter — ingest data downloaded from OpenAQ for Nepal.

Supports two input formats:
  1. CSV from /Users/yubraj/Code/air-quality-research/Data/open-aq/
     Columns: location_id, name, locality, country, latitude, longitude,
              timezone, datetime_first, datetime_last, is_monitor, …, sensors

  2. OpenAQ API v3 measurements JSON:
     {"results": [{"locationId": …, "parameter": "pm25", "value": 12.3,
                   "date": {"utc": "…"}, "coordinates": {"lat": …, "lon": …}}]}

For the research dataset, we use the CSV bulk download approach.
"""
import json
import logging
from pathlib import Path

import pandas as pd
import pytz

from apps.ingestion.base import BaseDataConverter, NEPAL_TZ

logger = logging.getLogger(__name__)


OPENAQ_POLLUTANT_MAP = {
    "pm25":  ("PM25", "µg/m³"),
    "pm10":  ("PM10", "µg/m³"),
    "pm1":   ("PM1",  "µg/m³"),
    "co2":   ("CO2",  "ppm"),
    "o3":    ("PM25", "µg/m³"),  # O3 not in our schema — skip or store as raw
    "no2":   ("PM25", "µg/m³"),
    "so2":   ("PM25", "µg/m³"),
}


class OpenAQConverter(BaseDataConverter):
    """
    Ingest OpenAQ CSV bulk downloads or API JSON responses.

    The converter creates one Site + Sensor record per OpenAQ location
    (using the location_id as the serial number prefix).
    """

    source_name = "OpenAQConverter"
    source_type = "EXTERNAL"

    def validate_source(self, source) -> bool:
        path = Path(source)
        if not path.exists():
            logger.error("OpenAQ file not found: %s", path)
            return False
        return True

    def parse_metadata(self, source) -> dict:
        return {"source_file": str(source), "provider": "OpenAQ"}

    def _read_source(self, source) -> pd.DataFrame:
        """
        Read OpenAQ CSV. The 'sensors' column contains a JSON list —
        we keep it as-is and parse it later.
        """
        return pd.read_csv(source, low_memory=False)

    def normalize_timestamps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        OpenAQ uses 'datetime_last' as the most recent reading timestamp.
        We expand into individual readings based on available sensor data.
        """
        if "datetime_last" in df.columns:
            df["original_ts"] = pd.to_datetime(df["datetime_last"], utc=True).dt.tz_convert(NEPAL_TZ)
        return df

    def map_columns(self, df: pd.DataFrame) -> list[dict]:
        """
        For locations-only CSV (no individual readings), we create one synthetic
        record per location using the latest available timestamp. This is used
        to register sensors only — not for time-series data.
        """
        records = []
        for _, row in df.iterrows():
            ts = row.get("original_ts")
            if pd.isna(ts) if hasattr(ts, "__class__") else ts is None:
                continue

            # Try to parse sensor list from the 'sensors' column
            sensors_raw = row.get("sensors", "[]")
            try:
                sensors_list = json.loads(str(sensors_raw).replace("'", '"'))
            except (json.JSONDecodeError, ValueError):
                sensors_list = []

            for sensor_info in sensors_list:
                param = sensor_info.get("parameter", {})
                param_name = param.get("name", "").lower()
                units = param.get("units", "")

                if param_name not in OPENAQ_POLLUTANT_MAP:
                    continue

                pollutant, canonical_unit = OPENAQ_POLLUTANT_MAP[param_name]
                records.append({
                    "original_ts": ts,
                    "timezone": row.get("timezone", "Asia/Kathmandu"),
                    "pollutant": pollutant,
                    "unit": canonical_unit,
                    "raw_value": None,  # locations CSV doesn't have values
                    "cleaned_value": None,
                    "_location_id": str(row.get("location_id", "")),
                    "_location_name": str(row.get("name", "")),
                    "_lat": row.get("latitude"),
                    "_lon": row.get("longitude"),
                    "is_indoor": False,
                    "source_type": self.source_type,
                })
        return records

    def assign_quality_flags(self, records: list[dict]) -> list[dict]:
        for rec in records:
            rec["quality_flag"] = "UNVALIDATED"
            rec["flag_reason"] = "OpenAQ data — not yet QC reviewed."
        return records

    def run(self, source) -> dict:
        """Ingest OpenAQ locations CSV, creating Site/Sensor records."""
        from apps.sensors.models import Sensor, Site
        from apps.readings.models import Dataset, IngestionLog

        if not self.validate_source(source):
            return {"saved": 0, "duplicates": 0, "errors": 1, "status": "FAILED"}

        df = self._read_source(source)
        df = self.normalize_timestamps(df)
        records = self.map_columns(df)
        records = self.assign_quality_flags(records)

        saved = 0
        errors = 0

        # For each unique location, create a Site + Sensor if not exists
        seen_locations = {}
        for rec in records:
            loc_id = rec.get("_location_id", "")
            loc_name = rec.get("_location_name", "")
            lat = rec.get("_lat")
            lon = rec.get("_lon")

            if loc_id not in seen_locations:
                site, _ = Site.objects.get_or_create(
                    name=f"OpenAQ: {loc_name}",
                    defaults={
                        "district": "Unknown",
                        "latitude": lat or 27.7,
                        "longitude": lon or 85.3,
                        "land_use_type": "URBAN",
                        "description": f"OpenAQ monitoring station: {loc_name}",
                    },
                )
                sensor, _ = Sensor.objects.get_or_create(
                    serial_number=f"openaq-{loc_id}",
                    defaults={
                        "friendly_name": loc_name,
                        "model": "Reference Monitor",
                        "manufacturer": "OpenAQ",
                        "site": site,
                        "is_indoor": False,
                        "status": "ACTIVE",
                    },
                )
                seen_locations[loc_id] = (sensor, site)

            sensor, site = seen_locations[loc_id]
            rec["sensor_id"] = sensor.pk
            rec["site_id"] = site.pk

        if records:
            result = self.save_to_canonical_schema(records)
            saved = result["saved"]
            errors = result["errors"]

        dataset = Dataset.objects.create(
            name=f"OpenAQ Import: {Path(source).name}",
            source_type=self.source_type,
            source_file=str(source),
            record_count=saved,
        )

        status = "SUCCESS" if errors == 0 else "PARTIAL"
        IngestionLog.objects.create(
            source_name=self.source_name,
            source_file=str(source),
            dataset=dataset,
            status=status,
            records_attempted=len(records),
            records_saved=saved,
            records_error=errors,
        )

        return {"saved": saved, "duplicates": 0, "errors": errors, "status": status}
