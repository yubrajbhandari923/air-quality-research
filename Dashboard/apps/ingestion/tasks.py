"""
Ingestion tasks: aggregation and raw Parquet export.

Two entry points:
  compute_aggregates(sensor_id=None, since=None)
      Recomputes HourlyAggregate and DailyAggregate from CanonicalReading.
      Called from the portal "Run Aggregation" button or on a Celery schedule.

  write_parquet(sensor_id, date_str)
      Dumps a single sensor's readings for a given date to a Parquet file
      under settings.RAW_DATA_DIR.  Parquet is the best format for bulk
      researcher downloads: columnar, compressed, ~5× smaller than CSV.

The Celery task wrappers at the bottom make these schedulable via django-celery-beat.
"""
import logging
from datetime import date, datetime, timedelta, timezone as dt_tz
from pathlib import Path

import pandas as pd
from django.conf import settings

logger = logging.getLogger(__name__)

RAW_DATA_DIR = getattr(settings, "RAW_DATA_DIR", None)


# ── Hourly aggregation ────────────────────────────────────────────────────────

def _compute_hourly(sensor, since_dt, until_dt):
    """Recompute HourlyAggregate rows for one sensor from the DuckDB raw store."""
    from apps.ingestion.raw_store import raw_store
    from apps.readings.models import HourlyAggregate

    df = raw_store.aggregate_hourly(sensor.pk, since_dt, until_dt)
    if df.empty:
        return 0

    interval_s = 60  # assume 1-minute data
    expected_per_hour = 3600 // interval_s

    saved_count = 0
    for _, row in df.iterrows():
        hour = row["hour"]
        # DuckDB returns tz-aware Timestamps; make sure Django gets an aware dt
        if hasattr(hour, "tzinfo") and hour.tzinfo is None:
            import pytz
            hour = pytz.utc.localize(hour)

        completeness = min(1.0, int(row["cnt"]) / expected_per_hour)
        HourlyAggregate.objects.update_or_create(
            sensor=sensor,
            hour=hour,
            pollutant=row["pollutant"],
            defaults={
                "site":        sensor.site,
                "is_indoor":   bool(row["is_indoor"]),
                "count":       int(row["cnt"]),
                "mean":        _safe_float(row["avg"]),
                "std":         _safe_float(row["std"]),
                "min_value":   _safe_float(row["lo"]),
                "max_value":   _safe_float(row["hi"]),
                "completeness": completeness,
            },
        )
        saved_count += 1

    return saved_count


# ── Daily aggregation ─────────────────────────────────────────────────────────

def _compute_daily(sensor, since_dt, until_dt):
    """Recompute DailyAggregate rows for one sensor from the DuckDB raw store."""
    from apps.ingestion.raw_store import raw_store
    from apps.readings.models import DailyAggregate
    import datetime as _datetime

    df = raw_store.aggregate_daily(sensor.pk, since_dt, until_dt)
    if df.empty:
        return 0

    interval_s = 60
    expected_per_day = 86400 // interval_s

    saved_count = 0
    for _, row in df.iterrows():
        d = row["date"]
        if isinstance(d, _datetime.datetime):
            d = d.date()
        elif hasattr(d, "item"):  # numpy date
            d = _datetime.date.fromisoformat(str(d))

        completeness = min(1.0, int(row["cnt"]) / expected_per_day)
        DailyAggregate.objects.update_or_create(
            sensor=sensor,
            date=d,
            pollutant=row["pollutant"],
            defaults={
                "site":        sensor.site,
                "is_indoor":   bool(row["is_indoor"]),
                "count":       int(row["cnt"]),
                "mean":        _safe_float(row["avg"]),
                "std":         _safe_float(row["std"]),
                "min_value":   _safe_float(row["lo"]),
                "max_value":   _safe_float(row["hi"]),
                "p25":         _safe_float(row.get("p25")),
                "median":      _safe_float(row.get("median")),
                "p75":         _safe_float(row.get("p75")),
                "p95":         _safe_float(row.get("p95")),
                "completeness": completeness,
            },
        )
        saved_count += 1

    return saved_count


def _safe_float(v):
    """Convert a potentially NaN/None value to float or None."""
    if v is None:
        return None
    try:
        import math
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ── Public entry point ────────────────────────────────────────────────────────

def compute_aggregates(sensor_id=None, since: datetime | None = None) -> dict:
    """
    Recompute HourlyAggregate and DailyAggregate from the DuckDB raw store.

    Args:
        sensor_id: Sensor PK to aggregate. None → all sensors.
        since:     Only recompute from this datetime onward.
                   Default: from the earliest reading in the raw store.

    Returns:
        dict with keys: sensors_processed, hourly_rows, daily_rows, errors.
    """
    from apps.sensors.models import Sensor

    if sensor_id:
        sensors = list(Sensor.objects.filter(pk=sensor_id).select_related("site"))
    else:
        sensors = list(Sensor.objects.select_related("site").all())

    until_dt = datetime.now(dt_tz.utc)
    total_hourly = total_daily = errors = 0

    for sensor in sensors:
        try:
            if since is None:
                from apps.ingestion.raw_store import raw_store
                first = raw_store.earliest_ts(sensor.pk)
                since_dt = first if first else until_dt - timedelta(days=365)
            else:
                since_dt = since

            h = _compute_hourly(sensor, since_dt, until_dt)
            d = _compute_daily(sensor, since_dt, until_dt)
            total_hourly += h
            total_daily += d
            logger.info("Aggregated sensor %s: %d hourly, %d daily rows", sensor.serial_number, h, d)

            # Prune CanonicalReading live-mirror rows older than 2 days.
            try:
                from apps.readings.models import CanonicalReading
                cutoff = until_dt - timedelta(days=2)
                deleted, _ = CanonicalReading.objects.filter(
                    sensor=sensor,
                    original_ts__lt=cutoff,
                    source_type="LIVE_API",
                ).delete()
                if deleted:
                    logger.info("Pruned %d stale CanonicalReading rows for sensor %s", deleted, sensor.serial_number)
            except Exception as exc2:
                logger.warning("CanonicalReading prune failed for sensor %s: %s", sensor, exc2)

        except Exception as exc:
            logger.exception("Aggregation failed for sensor %s: %s", sensor, exc)
            errors += 1

    return {
        "sensors_processed": len(sensors),
        "hourly_rows": total_hourly,
        "daily_rows": total_daily,
        "errors": errors,
    }


# ── Raw Parquet export ────────────────────────────────────────────────────────

def write_parquet(sensor_id: int, date_str: str | None = None) -> dict:
    """
    Write raw CanonicalReadings for one sensor to Parquet files.

    Files are written to:
        RAW_DATA_DIR/sensor_{serial}/{YYYY}/{YYYY-MM-DD}.parquet

    Args:
        sensor_id: Sensor PK.
        date_str:  'YYYY-MM-DD' to export a single day.
                   None → export all days that have readings.

    Returns:
        dict with keys: files_written, rows_written, error.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        logger.error("pyarrow not installed — cannot write Parquet files. Run: pip install pyarrow")
        return {"files_written": 0, "rows_written": 0, "error": "pyarrow not installed"}

    if not RAW_DATA_DIR:
        return {"files_written": 0, "rows_written": 0, "error": "RAW_DATA_DIR not configured in settings"}

    from apps.sensors.models import Sensor
    from apps.readings.models import CanonicalReading

    try:
        sensor = Sensor.objects.get(pk=sensor_id)
    except Sensor.DoesNotExist:
        return {"files_written": 0, "rows_written": 0, "error": f"Sensor {sensor_id} not found"}

    qs = CanonicalReading.objects.filter(sensor=sensor, is_duplicate=False).order_by("original_ts")

    if date_str:
        day = date.fromisoformat(date_str)
        start = datetime(day.year, day.month, day.day, tzinfo=dt_tz.utc)
        qs = qs.filter(original_ts__gte=start, original_ts__lt=start + timedelta(days=1))

    df = pd.DataFrame.from_records(
        qs.values("original_ts", "pollutant", "unit", "raw_value", "cleaned_value",
                  "quality_flag", "flag_reason", "is_indoor", "source_type", "interval_seconds")
    )

    if df.empty:
        return {"files_written": 0, "rows_written": 0, "error": None}

    df["original_ts"] = pd.to_datetime(df["original_ts"], utc=True)
    df["date"] = df["original_ts"].dt.date

    base_dir = Path(RAW_DATA_DIR) / f"sensor_{sensor.serial_number}"
    files_written = rows_written = 0

    for day_date, day_df in df.groupby("date"):
        out_dir = base_dir / str(day_date.year)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{day_date}.parquet"

        day_df = day_df.drop(columns=["date"])
        table = pa.Table.from_pandas(day_df, preserve_index=False)
        pq.write_table(table, out_path, compression="snappy")

        files_written += 1
        rows_written += len(day_df)
        logger.debug("Wrote %s (%d rows)", out_path, len(day_df))

    return {"files_written": files_written, "rows_written": rows_written, "error": None}


# ── Background dispatch helper ────────────────────────────────────────────────

def aggregate_after_upload(sensor_ids: list) -> str:
    """
    Fire-and-forget aggregation triggered after a successful CSV upload or batch push.

    Tries Celery first so the task runs in a proper worker process.
    If the broker is unreachable (e.g. development without Redis), falls back to
    a daemon thread so the server process does the work without blocking the HTTP
    response.

    Returns the dispatch method used: 'celery', 'thread', or 'skipped'.
    """
    import threading

    sid_list = list({int(s) for s in sensor_ids if s})
    if not sid_list:
        return "skipped"

    def _run_sync():
        for sid in sid_list:
            try:
                compute_aggregates(sensor_id=sid)
                logger.info("Post-upload aggregation complete for sensor %d", sid)
            except Exception as exc:
                logger.exception("Post-upload aggregation failed for sensor %d: %s", sid, exc)

    # Attempt Celery dispatch (sends to broker without importing the task locally)
    try:
        from celery import current_app as _celery
        for sid in sid_list:
            _celery.send_task("ingestion.compute_aggregates", kwargs={"sensor_id": sid})
        logger.info("Post-upload aggregation queued via Celery for sensors %s", sid_list)
        return "celery"
    except Exception:
        pass

    # Fallback: daemon thread — works without Redis, dies if process restarts
    t = threading.Thread(target=_run_sync, daemon=True, name=f"agg-{sid_list}")
    t.start()
    logger.info("Post-upload aggregation running in background thread for sensors %s", sid_list)
    return "thread"


# ── Celery task wrappers ──────────────────────────────────────────────────────

try:
    from nepal_aq.celery import app as celery_app

    @celery_app.task(name="ingestion.compute_aggregates")
    def celery_compute_aggregates(sensor_id=None):
        return compute_aggregates(sensor_id=sensor_id)

    @celery_app.task(name="ingestion.write_parquet")
    def celery_write_parquet(sensor_id: int, date_str: str | None = None):
        return write_parquet(sensor_id=sensor_id, date_str=date_str)

except Exception:
    # Celery not configured (e.g. during management commands without broker)
    pass
