"""
BaseDataConverter — abstract pipeline for all ingestion sources.

Every converter must implement the five abstract methods. The concrete
`run()` and `save_to_canonical_schema()` methods handle orchestration
and DB persistence so subclasses stay focused on parsing logic.

Pipeline:
  1. validate_source()          — sanity-check the input
  2. parse_metadata()           — extract sensor/site/dataset info
  3. normalize_timestamps()     — make all timestamps timezone-aware
  4. map_columns()              — produce list[dict] in canonical long schema
  5. assign_quality_flags()     — add quality_flag + flag_reason to each record
  6. save_to_canonical_schema() — persist according to AQ_INGESTION settings:
       • always writes to R2 parquet (wide format, partitioned by month)
       • writes to CanonicalReading only when db_raw_enabled=True
       • returns {"saved", "duplicates", "errors"}
"""
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone as dt_tz
from typing import Any

import pandas as pd
import pytz

logger = logging.getLogger(__name__)

NEPAL_TZ = pytz.timezone("Asia/Kathmandu")


def _aq_cfg() -> dict:
    """Return the AQ_INGESTION settings dict (with safe defaults)."""
    from django.conf import settings
    return getattr(settings, "AQ_INGESTION", {
        "db_raw_enabled": True,
        "db_raw_recent_days": 0,
        "db_aggregates": ["tenmin", "hourly", "daily"],
        "r2_partition_by": "month",
    })


class BaseDataConverter(ABC):
    """
    Abstract base for all data converters.

    Subclasses must set class attributes:
        source_name  (str) — human-readable identifier
        source_type  (str) — must match CanonicalReading.SourceType choices
    """

    source_name: str = ""
    source_type: str = ""

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    def validate_source(self, source: Any) -> bool: ...

    @abstractmethod
    def parse_metadata(self, source: Any) -> dict: ...

    @abstractmethod
    def normalize_timestamps(self, df: pd.DataFrame) -> pd.DataFrame: ...

    @abstractmethod
    def map_columns(self, df: pd.DataFrame) -> list[dict]: ...

    @abstractmethod
    def assign_quality_flags(self, records: list[dict]) -> list[dict]: ...

    # ── Concrete: save ────────────────────────────────────────────────────────

    def save_to_canonical_schema(self, records: list[dict]) -> dict:
        """
        Persist canonical records according to AQ_INGESTION settings.

        Always writes to R2 parquet (if R2 is configured).
        Writes to CanonicalReading only when db_raw_enabled=True.

        Returns: {"saved": int, "duplicates": int, "errors": int}
        """
        if not records:
            return {"saved": 0, "duplicates": 0, "errors": 0}

        cfg        = _aq_cfg()
        db_raw     = cfg.get("db_raw_enabled", True)
        raw_days   = cfg.get("db_raw_recent_days", 0)

        # ── R2 parquet path (always attempted when R2 is configured) ──────────
        parquet_result = self._save_to_parquet(records)

        # ── Postgres raw path (opt-in) ────────────────────────────────────────
        db_result = {"saved": 0, "duplicates": 0, "errors": 0}
        if db_raw:
            filtered = records
            if raw_days > 0:
                cutoff   = datetime.now(dt_tz.utc) - timedelta(days=raw_days)
                filtered = [r for r in records if r.get("original_ts") and r["original_ts"] > cutoff]
            if filtered:
                db_result = self._save_to_db(filtered)

        # When R2 is available, report parquet counts as the authoritative numbers.
        # When R2 is not configured (dev), fall back to DB counts.
        from apps.ingestion.parquet_store import _r2_available
        if _r2_available():
            return {
                "saved":      parquet_result.get("rows_new", 0),
                "duplicates": parquet_result.get("rows_duplicate", 0),
                "errors":     parquet_result.get("errors", 0),
            }
        return db_result

    def _save_to_parquet(self, records: list[dict]) -> dict:
        """Write records to R2 wide parquet, grouped by (sensor_id, month)."""
        from apps.ingestion.parquet_store import long_to_wide, upsert, _r2_available
        from apps.sensors.models import Sensor

        if not _r2_available():
            return {"rows_new": 0, "rows_duplicate": 0, "errors": 0}

        # Group records by sensor_id so we can get the serial for the R2 key.
        by_sensor: dict[int, list[dict]] = {}
        for rec in records:
            sid = rec.get("sensor_id")
            if sid is not None:
                by_sensor.setdefault(int(sid), []).append(rec)

        total_new = total_dupe = total_errors = 0

        # Cache sensor serial lookups
        serial_cache: dict[int, str] = {}
        for sid, sensor_records in by_sensor.items():
            try:
                if sid not in serial_cache:
                    serial_cache[sid] = Sensor.objects.values_list(
                        "serial_number", flat=True
                    ).get(pk=sid)
                serial   = serial_cache[sid]
                df_wide  = long_to_wide(sensor_records)
                if df_wide.empty:
                    continue
                result        = upsert(serial, df_wide)
                total_new    += result.get("rows_new", 0)
                total_dupe   += result.get("rows_duplicate", 0)
            except Exception as exc:
                logger.error("Parquet upsert failed for sensor %s: %s", sid, exc)
                total_errors += len(sensor_records)

        return {"rows_new": total_new, "rows_duplicate": total_dupe, "errors": total_errors}

    def _save_to_db(self, records: list[dict]) -> dict:
        """Write records to PostgreSQL CanonicalReading using bulk_create."""
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

    # ── Concrete: run (default — subclasses typically override) ───────────────

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

            dataset = Dataset.objects.create(
                name=f"{self.source_name}: {source}",
                source_type=self.source_type,
                source_file=str(source),
            )
            log_kwargs["dataset"] = dataset

            df      = self._read_source(source)
            df      = self.normalize_timestamps(df)
            records = self.map_columns(df)
            records = self.assign_quality_flags(records)

            for rec in records:
                rec["dataset_id"] = dataset.pk

            log_kwargs["records_attempted"] = len(records)

            result = self.save_to_canonical_schema(records)

            dataset.record_count = result["saved"]
            dataset.save(update_fields=["record_count"])

            log_kwargs["status"] = (
                IngestionLog.Status.SUCCESS if result["errors"] == 0
                else IngestionLog.Status.PARTIAL
            )
            log_kwargs["records_saved"]     = result["saved"]
            log_kwargs["records_duplicate"] = result["duplicates"]
            log_kwargs["records_error"]     = result["errors"]

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
                pass

    def _read_source(self, source: Any) -> pd.DataFrame:
        return pd.read_csv(source, low_memory=False)
