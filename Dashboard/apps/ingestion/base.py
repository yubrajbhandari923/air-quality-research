"""
BaseDataConverter — abstract pipeline for all ingestion sources.

Every converter must implement the five abstract methods. The concrete
`run()` and `save_to_canonical_schema()` methods handle orchestration
and DB persistence so subclasses stay focused on parsing logic.

Pipeline:
  1. validate_source()       — sanity-check the input (file exists, header ok, …)
  2. parse_metadata()        — extract sensor/site/dataset info
  3. normalize_timestamps()  — make all timestamps timezone-aware (Asia/Kathmandu)
  4. map_columns()           — produce list[dict] in canonical schema
  5. assign_quality_flags()  — add quality_flag + flag_reason to each record
  6. save_to_canonical_schema() — persist to DB, detect duplicates, log
"""
import logging
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd
import pytz

logger = logging.getLogger(__name__)

NEPAL_TZ = pytz.timezone("Asia/Kathmandu")


class BaseDataConverter(ABC):
    """
    Abstract base for all data converters.

    Subclasses must set class attributes:
        source_name  (str) — human-readable identifier, e.g. "BelauriCSVConverter"
        source_type  (str) — must match CanonicalReading.SourceType choices
    """

    source_name: str = ""
    source_type: str = ""  # matches CanonicalReading.SourceType choices

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def validate_source(self, source: Any) -> bool:
        """
        Verify the input is usable.

        Args:
            source: file path, URL, or raw data depending on converter type.

        Returns:
            True if source is valid and processing can proceed.
        """
        ...

    @abstractmethod
    def parse_metadata(self, source: Any) -> dict:
        """
        Extract provenance metadata from the source.

        Returns a dict with at least:
            sensor_serial  (str)
            site_name      (str, optional)
            is_indoor      (bool)
        """
        ...

    @abstractmethod
    def normalize_timestamps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Convert all timestamp columns to timezone-aware datetimes (Asia/Kathmandu).

        Timestamps without explicit timezone info are assumed to be UTC unless the
        source documentation says otherwise.

        Returns the DataFrame with a 'original_ts' column that is tz-aware.
        """
        ...

    @abstractmethod
    def map_columns(self, df: pd.DataFrame) -> list[dict]:
        """
        Transform source-specific columns into canonical reading dicts.

        Each dict in the returned list must contain at minimum:
            original_ts   (datetime, tz-aware)
            pollutant     (str — CanonicalReading.Pollutant choice)
            unit          (str)
            raw_value     (float or None)
            sensor_id     (int — Sensor pk)
            site_id       (int — Site pk)
            is_indoor     (bool)
            source_type   (str)

        Optional fields:
            interval_seconds, dataset_id, timezone
        """
        ...

    @abstractmethod
    def assign_quality_flags(self, records: list[dict]) -> list[dict]:
        """
        Evaluate each record and set 'quality_flag' and 'flag_reason'.

        Default flag is UNVALIDATED.  Apply domain knowledge:
          - PM2.5 > 500 µg/m³  → SUSPECT  (sensor saturation likely)
          - PM2.5 < 0          → BAD
          - Temperature < -10 or > 60 °C → SUSPECT (sensor range)
          - Exact zero for all channels simultaneously → SUSPECT (sensor off?)

        Never delete records — mark them appropriately.
        """
        ...

    # ── Concrete methods ──────────────────────────────────────────────────────

    def save_to_canonical_schema(self, records: list[dict]) -> dict:
        """
        Persist canonical records to the Postgres CanonicalReading table.

        Uses bulk_create with ignore_conflicts=True so duplicate
        (sensor, pollutant, original_ts) rows are silently skipped.
        The before/after count gives exact saved vs duplicate counts.

        Returns:
            {"saved": int, "duplicates": int, "errors": int}
        """
        if not records:
            return {"saved": 0, "duplicates": 0, "errors": 0}

        try:
            from apps.readings.models import CanonicalReading

            objs = [
                CanonicalReading(
                    sensor_id        = rec["sensor_id"],
                    site_id          = rec.get("site_id"),
                    original_ts      = rec["original_ts"],
                    pollutant        = rec["pollutant"],
                    unit             = rec.get("unit", ""),
                    raw_value        = rec.get("raw_value"),
                    cleaned_value    = rec.get("cleaned_value"),
                    quality_flag     = rec.get("quality_flag", "UNVALIDATED"),
                    flag_reason      = rec.get("flag_reason", ""),
                    is_indoor        = bool(rec.get("is_indoor", False)),
                    source_type      = rec.get("source_type", "CSV_UPLOAD"),
                    dataset_id       = rec.get("dataset_id"),
                    timezone         = rec.get("timezone", "Asia/Kathmandu"),
                    interval_seconds = rec.get("interval_seconds"),
                    is_duplicate     = False,
                )
                for rec in records
            ]

            # Count existing rows in this batch's time window before inserting,
            # so we can report accurate saved vs duplicate counts.
            sensor_ids = list({o.sensor_id for o in objs})
            ts_list    = [o.original_ts for o in objs]
            ts_min, ts_max = min(ts_list), max(ts_list)

            before = CanonicalReading.objects.filter(
                sensor_id__in=sensor_ids,
                original_ts__range=(ts_min, ts_max),
                is_duplicate=False,
            ).count()

            for i in range(0, len(objs), 1000):
                CanonicalReading.objects.bulk_create(objs[i:i + 1000], ignore_conflicts=True)

            after = CanonicalReading.objects.filter(
                sensor_id__in=sensor_ids,
                original_ts__range=(ts_min, ts_max),
                is_duplicate=False,
            ).count()

            saved      = after - before
            duplicates = len(records) - saved
            return {"saved": saved, "duplicates": duplicates, "errors": 0}

        except Exception as exc:
            logger.error("[%s] Postgres write failed: %s", self.source_name, exc)
            return {"saved": 0, "duplicates": 0, "errors": len(records)}

    def run(self, source: Any) -> dict:
        """
        Orchestrate the full ingestion pipeline.

        Returns:
            dict with keys: saved, duplicates, errors, status ("SUCCESS"|"PARTIAL"|"FAILED")
        """
        from apps.readings.models import Dataset, IngestionLog

        log_kwargs = {
            "source_name": self.source_name,
            "source_file": str(source),
            "status": IngestionLog.Status.FAILED,
            "records_attempted": 0,
            "records_saved": 0,
            "records_duplicate": 0,
            "records_error": 0,
        }

        try:
            logger.info("[%s] Starting ingestion of: %s", self.source_name, source)

            if not self.validate_source(source):
                raise ValueError(f"Source validation failed: {source}")

            metadata = self.parse_metadata(source)
            logger.debug("[%s] Metadata: %s", self.source_name, metadata)

            # Create a Dataset record for provenance
            dataset = Dataset.objects.create(
                name=f"{self.source_name}: {source}",
                source_type=self.source_type,
                source_file=str(source),
            )
            log_kwargs["dataset"] = dataset

            # Read raw data into DataFrame
            df = self._read_source(source)
            df = self.normalize_timestamps(df)
            records = self.map_columns(df)
            records = self.assign_quality_flags(records)

            # Inject dataset FK into every record
            for rec in records:
                rec["dataset_id"] = dataset.pk

            log_kwargs["records_attempted"] = len(records)

            result = self.save_to_canonical_schema(records)

            # Update dataset record count
            dataset.record_count = result["saved"]
            dataset.save(update_fields=["record_count"])

            log_kwargs["status"] = (
                IngestionLog.Status.SUCCESS
                if result["errors"] == 0
                else IngestionLog.Status.PARTIAL
            )
            log_kwargs["records_saved"] = result["saved"]
            log_kwargs["records_duplicate"] = result["duplicates"]
            log_kwargs["records_error"] = result["errors"]

            logger.info(
                "[%s] Complete — saved=%d, duplicates=%d, errors=%d",
                self.source_name, result["saved"], result["duplicates"], result["errors"],
            )
            result["status"] = log_kwargs["status"]
            return result

        except Exception as exc:
            log_kwargs["error_detail"] = str(exc)
            logger.exception("[%s] Ingestion failed: %s", self.source_name, exc)
            return {"saved": 0, "duplicates": 0, "errors": 1, "status": "FAILED", "error": str(exc)}

        finally:
            try:
                IngestionLog.objects.create(**log_kwargs)
            except Exception:
                pass  # don't let logging failure mask the real error

    def _read_source(self, source: Any) -> pd.DataFrame:
        """
        Default implementation reads a CSV file.
        Override in subclasses that use other sources (API responses, etc.).
        """
        return pd.read_csv(source, low_memory=False)
