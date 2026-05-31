"""
Aggregation tasks — compute TenMin, Hourly, and Daily aggregate tables.

Two entry points
────────────────
compute_aggregates_from_records(records, sensor_cache)
    Called immediately after every CSV upload (in-process, no DB/R2 read).
    Aggregates the long-format records that are already in memory.

compute_aggregates(sensor_id=None, since=None)
    Called by the aggregate_readings cron (Render Cron 2).
    When AQ_INGESTION["db_raw_enabled"]=True  → reads from CanonicalReading (Postgres).
    When AQ_INGESTION["db_raw_enabled"]=False → downloads R2 parquet and aggregates.

aggregate_after_upload(sensor_ids)
    Legacy fire-and-forget helper kept for backward compatibility.
    Now a no-op because converters call compute_aggregates_from_records directly.
"""
import logging
import math
from datetime import datetime, timedelta, timezone as dt_tz

import pandas as pd

from django.db import connection

logger = logging.getLogger(__name__)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _aq_cfg() -> dict:
    from django.conf import settings
    return getattr(settings, "AQ_INGESTION", {
        "db_raw_enabled": True,
        "db_aggregates": ["tenmin", "hourly", "daily"],
    })


# ── Pandas-based aggregate helpers (used by compute_aggregates_from_records) ───

def _tenmin_from_df(sensor, site, df: pd.DataFrame, levels: list[str]) -> int:
    """Upsert 10-minute aggregates from a long-format DataFrame."""
    if "tenmin" not in levels:
        return 0
    from apps.readings.models import TenMinAggregate

    df = df.copy()
    df["window_start"] = df["ts_utc"].dt.floor("10min")

    grouped = (
        df.groupby(["window_start", "pollutant", "is_indoor"])["raw_value"]
        .agg(count="count", mean="mean", lo="min", hi="max")
        .reset_index()
    )

    count = 0
    for _, row in grouped.iterrows():
        TenMinAggregate.objects.update_or_create(
            sensor=sensor,
            window_start=row["window_start"],
            pollutant=row["pollutant"],
            defaults={
                "site":      site,
                "is_indoor": bool(row["is_indoor"]),
                "count":     int(row["count"]),
                "mean":      _safe_float(row["mean"]),
                "min_value": _safe_float(row["lo"]),
                "max_value": _safe_float(row["hi"]),
            },
        )
        count += 1
    return count


def _hourly_from_df(sensor, site, df: pd.DataFrame, levels: list[str]) -> int:
    """Upsert hourly aggregates from a long-format DataFrame."""
    if "hourly" not in levels:
        return 0
    from apps.readings.models import HourlyAggregate

    df = df.copy()
    df["hour"] = df["ts_utc"].dt.floor("h")

    grouped = (
        df.groupby(["hour", "pollutant", "is_indoor"])["raw_value"]
        .agg(count="count", mean="mean", std="std", lo="min", hi="max")
        .reset_index()
    )

    interval_s        = 60
    expected_per_hour = 3600 // interval_s
    count = 0

    for _, row in grouped.iterrows():
        completeness = min(1.0, int(row["count"]) / expected_per_hour)
        HourlyAggregate.objects.update_or_create(
            sensor=sensor,
            hour=row["hour"],
            pollutant=row["pollutant"],
            defaults={
                "site":         site,
                "is_indoor":    bool(row["is_indoor"]),
                "count":        int(row["count"]),
                "mean":         _safe_float(row["mean"]),
                "std":          _safe_float(row["std"]),
                "min_value":    _safe_float(row["lo"]),
                "max_value":    _safe_float(row["hi"]),
                "completeness": completeness,
            },
        )
        count += 1
    return count


def _daily_from_df(sensor, site, df: pd.DataFrame, levels: list[str]) -> int:
    """Upsert daily aggregates from a long-format DataFrame."""
    if "daily" not in levels:
        return 0
    from apps.readings.models import DailyAggregate

    df = df.copy()
    df["date"] = df["ts_utc"].dt.normalize().dt.date

    # Use DataFrameGroupBy named agg — mixes string funcs and lambdas cleanly.
    grouped = (
        df.groupby(["date", "pollutant", "is_indoor"])
        .agg(
            count=("raw_value", "count"),
            mean=("raw_value", "mean"),
            std=("raw_value", "std"),
            lo=("raw_value", "min"),
            hi=("raw_value", "max"),
            p25=("raw_value", lambda x: x.quantile(0.25)),
            p50=("raw_value", lambda x: x.quantile(0.50)),
            p75=("raw_value", lambda x: x.quantile(0.75)),
            p95=("raw_value", lambda x: x.quantile(0.95)),
        )
        .reset_index()
    )

    interval_s      = 60
    expected_per_day = 86400 // interval_s
    count = 0

    for _, row in grouped.iterrows():
        completeness = min(1.0, int(row["count"]) / expected_per_day)
        DailyAggregate.objects.update_or_create(
            sensor=sensor,
            date=row["date"],
            pollutant=row["pollutant"],
            defaults={
                "site":         site,
                "is_indoor":    bool(row["is_indoor"]),
                "count":        int(row["count"]),
                "mean":         _safe_float(row["mean"]),
                "std":          _safe_float(row["std"]),
                "min_value":    _safe_float(row["lo"]),
                "max_value":    _safe_float(row["hi"]),
                "p25":          _safe_float(row["p25"]),
                "median":       _safe_float(row["p50"]),
                "p75":          _safe_float(row["p75"]),
                "p95":          _safe_float(row["p95"]),
                "completeness": completeness,
            },
        )
        count += 1
    return count


def _long_df_for_aggregation(records: list[dict]) -> pd.DataFrame:
    """
    Build a clean long-format DataFrame from ingestion records, ready for
    pandas groupby aggregation.

    Keeps only GOOD/UNVALIDATED rows with non-null raw_value.
    Ensures ts_utc is UTC-aware.
    """
    df = pd.DataFrame(records)
    if df.empty:
        return df

    df["ts_utc"] = pd.to_datetime(df["original_ts"], utc=True)

    # Filter to quality rows and numeric values
    if "quality_flag" in df.columns:
        df = df[df["quality_flag"].isin(["GOOD", "UNVALIDATED"])].copy()
    df = df[df["raw_value"].notna()].copy()
    df["raw_value"] = pd.to_numeric(df["raw_value"], errors="coerce")
    df = df[df["raw_value"].notna()].copy()

    return df[["ts_utc", "pollutant", "raw_value", "is_indoor"]].copy()


# ── Public: post-upload aggregation ───────────────────────────────────────────

def compute_aggregates_from_dataframe(df: pd.DataFrame, sensor_cache: dict) -> dict:
    """
    Compute TenMin/Hourly/Daily aggregates from an already-built DataFrame.

    ``df`` must have columns: original_ts, pollutant, raw_value, quality_flag,
    is_indoor, sensor_id.  Created by the CSV converters to avoid keeping
    millions of Python dicts in memory.
    """
    from apps.sensors.models import Sensor

    cfg    = _aq_cfg()
    levels = cfg.get("db_aggregates", ["tenmin", "hourly", "daily"])
    if not levels or df.empty:
        return {"sensors_processed": 0}

    id_to_sensor: dict[int, object] = {}
    for key, sensor in sensor_cache.items():
        if isinstance(key, int):
            id_to_sensor[key] = sensor
        else:
            id_to_sensor[sensor.pk] = sensor

    total_tenmin = total_hourly = total_daily = errors = 0

    for sid, group in df.groupby("sensor_id"):
        try:
            sensor = id_to_sensor.get(int(sid)) or Sensor.objects.select_related("site").get(pk=sid)
            site   = sensor.site

            # Reuse _long_df_for_aggregation logic directly on the group
            agg = group.copy()
            agg["ts_utc"] = pd.to_datetime(agg["original_ts"], utc=True)
            if "quality_flag" in agg.columns:
                agg = agg[agg["quality_flag"].isin(["GOOD", "UNVALIDATED"])].copy()
            agg = agg[agg["raw_value"].notna()].copy()
            agg["raw_value"] = pd.to_numeric(agg["raw_value"], errors="coerce")
            agg = agg[agg["raw_value"].notna()][["ts_utc", "pollutant", "raw_value", "is_indoor"]].copy()

            if agg.empty:
                continue

            total_tenmin += _tenmin_from_df(sensor, site, agg, levels)
            total_hourly += _hourly_from_df(sensor, site, agg, levels)
            total_daily  += _daily_from_df(sensor, site, agg, levels)

        except Exception as exc:
            logger.exception("Aggregation (from df) failed for sensor %s: %s", sid, exc)
            errors += 1

    return {
        "sensors_processed": len(df["sensor_id"].unique()),
        "tenmin_rows": total_tenmin,
        "hourly_rows": total_hourly,
        "daily_rows":  total_daily,
        "errors":      errors,
    }


def compute_aggregates_from_records(records: list[dict], sensor_cache: dict) -> dict:
    """
    Compute TenMin/Hourly/Daily aggregates from in-memory long-format records.

    Called by converters immediately after ingestion — no DB or R2 read required.

    Args:
        records:       Long-format list[dict] with original_ts, pollutant, raw_value,
                       quality_flag, is_indoor, sensor_id.
        sensor_cache:  dict mapping serial_number → Sensor ORM object, OR
                       sensor_id (int) → Sensor ORM object.  Used to resolve site FK.
    """
    from apps.sensors.models import Sensor

    cfg    = _aq_cfg()
    levels = cfg.get("db_aggregates", ["tenmin", "hourly", "daily"])
    if not levels:
        return {"sensors_processed": 0}

    # Build sensor_id → Sensor object mapping
    id_to_sensor: dict[int, object] = {}
    for key, sensor in sensor_cache.items():
        if isinstance(key, int):
            id_to_sensor[key] = sensor
        else:
            id_to_sensor[sensor.pk] = sensor

    # Group records by sensor_id
    by_sensor: dict[int, list[dict]] = {}
    for rec in records:
        sid = rec.get("sensor_id")
        if sid is not None:
            by_sensor.setdefault(int(sid), []).append(rec)

    total_tenmin = total_hourly = total_daily = errors = 0

    for sid, sensor_records in by_sensor.items():
        try:
            sensor = id_to_sensor.get(sid) or Sensor.objects.select_related("site").get(pk=sid)
            site   = sensor.site

            df = _long_df_for_aggregation(sensor_records)
            if df.empty:
                continue

            total_tenmin += _tenmin_from_df(sensor, site, df, levels)
            total_hourly += _hourly_from_df(sensor, site, df, levels)
            total_daily  += _daily_from_df(sensor, site, df, levels)

            logger.info(
                "Aggregated sensor %s (in-memory): tenmin=%d hourly=%d daily=%d",
                sensor.serial_number, total_tenmin, total_hourly, total_daily,
            )
        except Exception as exc:
            logger.exception("Aggregation failed for sensor %s: %s", sid, exc)
            errors += 1

    return {
        "sensors_processed": len(by_sensor),
        "tenmin_rows": total_tenmin,
        "hourly_rows": total_hourly,
        "daily_rows":  total_daily,
        "errors":      errors,
    }


# ── SQL-based helpers (CanonicalReading path — kept for db_raw_enabled=True) ──

def _compute_ten_min_sql(sensor, since_dt, until_dt) -> int:
    from apps.readings.models import TenMinAggregate

    sql = """
        SELECT
            date_trunc('hour', original_ts)
              + (EXTRACT(MINUTE FROM original_ts)::int / 10) * interval '10 minutes'
                AS window_start,
            pollutant,
            is_indoor,
            COUNT(*)      AS cnt,
            AVG(raw_value)   AS avg,
            MIN(raw_value)   AS lo,
            MAX(raw_value)   AS hi
        FROM readings_canonicalreading
        WHERE sensor_id    = %s
          AND quality_flag IN ('GOOD', 'UNVALIDATED')
          AND is_duplicate = FALSE
          AND original_ts >= %s
          AND original_ts <  %s
          AND raw_value IS NOT NULL
        GROUP BY 1, 2, 3
        ORDER BY 1, 2
    """
    with connection.cursor() as cur:
        cur.execute(sql, [sensor.pk, since_dt, until_dt])
        rows = cur.fetchall()

    count = 0
    for row in rows:
        window_start, pollutant, is_indoor, cnt, avg, lo, hi = row
        TenMinAggregate.objects.update_or_create(
            sensor=sensor, window_start=window_start, pollutant=pollutant,
            defaults={
                "site":      sensor.site,
                "is_indoor": bool(is_indoor),
                "count":     int(cnt),
                "mean":      _safe_float(avg),
                "min_value": _safe_float(lo),
                "max_value": _safe_float(hi),
            },
        )
        count += 1
    return count


def _compute_hourly_sql(sensor, since_dt, until_dt) -> int:
    from apps.readings.models import HourlyAggregate

    sql = """
        SELECT
            date_trunc('hour', original_ts) AS hour,
            pollutant, is_indoor,
            COUNT(*) AS cnt, AVG(raw_value) AS avg,
            STDDEV_SAMP(raw_value) AS std,
            MIN(raw_value) AS lo, MAX(raw_value) AS hi
        FROM readings_canonicalreading
        WHERE sensor_id    = %s
          AND quality_flag IN ('GOOD', 'UNVALIDATED')
          AND is_duplicate = FALSE
          AND original_ts >= %s AND original_ts < %s
          AND raw_value IS NOT NULL
        GROUP BY 1, 2, 3 ORDER BY 1, 2
    """
    with connection.cursor() as cur:
        cur.execute(sql, [sensor.pk, since_dt, until_dt])
        rows = cur.fetchall()

    interval_s        = 60
    expected_per_hour = 3600 // interval_s
    count = 0

    for row in rows:
        hour, pollutant, is_indoor, cnt, avg, std, lo, hi = row
        completeness = min(1.0, int(cnt) / expected_per_hour)
        HourlyAggregate.objects.update_or_create(
            sensor=sensor, hour=hour, pollutant=pollutant,
            defaults={
                "site":         sensor.site,
                "is_indoor":    bool(is_indoor),
                "count":        int(cnt),
                "mean":         _safe_float(avg),
                "std":          _safe_float(std),
                "min_value":    _safe_float(lo),
                "max_value":    _safe_float(hi),
                "completeness": completeness,
            },
        )
        count += 1
    return count


def _compute_daily_sql(sensor, since_dt, until_dt) -> int:
    from apps.readings.models import DailyAggregate

    sql = """
        SELECT
            (original_ts AT TIME ZONE 'UTC')::date AS date,
            pollutant, is_indoor,
            COUNT(*) AS cnt, AVG(raw_value) AS avg, STDDEV_SAMP(raw_value) AS std,
            MIN(raw_value) AS lo, MAX(raw_value) AS hi,
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY raw_value) AS p25,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY raw_value) AS p50,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY raw_value) AS p75,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY raw_value) AS p95
        FROM readings_canonicalreading
        WHERE sensor_id    = %s
          AND quality_flag IN ('GOOD', 'UNVALIDATED')
          AND is_duplicate = FALSE
          AND original_ts >= %s AND original_ts < %s
          AND raw_value IS NOT NULL
        GROUP BY 1, 2, 3 ORDER BY 1, 2
    """
    with connection.cursor() as cur:
        cur.execute(sql, [sensor.pk, since_dt, until_dt])
        rows = cur.fetchall()

    interval_s       = 60
    expected_per_day = 86400 // interval_s
    count = 0

    for row in rows:
        date, pollutant, is_indoor, cnt, avg, std, lo, hi, p25, p50, p75, p95 = row
        completeness = min(1.0, int(cnt) / expected_per_day)
        DailyAggregate.objects.update_or_create(
            sensor=sensor, date=date, pollutant=pollutant,
            defaults={
                "site":         sensor.site,
                "is_indoor":    bool(is_indoor),
                "count":        int(cnt),
                "mean":         _safe_float(avg),
                "std":          _safe_float(std),
                "min_value":    _safe_float(lo),
                "max_value":    _safe_float(hi),
                "p25":          _safe_float(p25),
                "median":       _safe_float(p50),
                "p75":          _safe_float(p75),
                "p95":          _safe_float(p95),
                "completeness": completeness,
            },
        )
        count += 1
    return count


# ── R2 parquet aggregation path (used by cron when db_raw_enabled=False) ───────

def _compute_aggregates_from_r2(sensor, since: datetime, until: datetime) -> dict:
    """
    Download R2 parquet for [since, until], convert to long format, aggregate.
    Used by the cron job when raw readings are not stored in Postgres.
    """
    from apps.ingestion.parquet_store import read as parquet_read, wide_to_long

    cfg    = _aq_cfg()
    levels = cfg.get("db_aggregates", ["tenmin", "hourly", "daily"])

    df_wide = parquet_read(sensor.serial_number, since, until)
    if df_wide.empty:
        logger.info("No R2 parquet data for sensor %s [%s, %s]", sensor.serial_number, since, until)
        return {"tenmin_rows": 0, "hourly_rows": 0, "daily_rows": 0}

    df_long = wide_to_long(df_wide)
    if df_long.empty:
        return {"tenmin_rows": 0, "hourly_rows": 0, "daily_rows": 0}

    # Rename to match _*_from_df signature
    df_long = df_long.rename(columns={"ts_utc": "ts_utc"})

    # Filter to good quality + numeric
    df_agg = df_long[
        df_long["quality_flag"].isin(["GOOD", "UNVALIDATED"])
        & df_long["raw_value"].notna()
    ].copy()
    df_agg["raw_value"] = pd.to_numeric(df_agg["raw_value"], errors="coerce")
    df_agg = df_agg[df_agg["raw_value"].notna()].copy()

    if df_agg.empty:
        return {"tenmin_rows": 0, "hourly_rows": 0, "daily_rows": 0}

    # _*_from_df expects "ts_utc" column
    site = sensor.site
    t = _tenmin_from_df(sensor, site, df_agg, levels)
    h = _hourly_from_df(sensor, site, df_agg, levels)
    d = _daily_from_df(sensor, site, df_agg, levels)
    return {"tenmin_rows": t, "hourly_rows": h, "daily_rows": d}


# ── Public: cron aggregation ───────────────────────────────────────────────────

def compute_aggregates(sensor_id=None, since: datetime | None = None) -> dict:
    """
    Recompute aggregate tables for all (or one) sensor(s).

    Reads from CanonicalReading when db_raw_enabled=True,
    reads from R2 parquet when db_raw_enabled=False.

    Args:
        sensor_id: Sensor PK to aggregate; None → all sensors.
        since:     Recompute from this datetime onward (default: 60 days ago).
    """
    from apps.sensors.models import Sensor
    from apps.readings.models import CanonicalReading

    cfg        = _aq_cfg()
    db_raw     = cfg.get("db_raw_enabled", True)
    levels     = cfg.get("db_aggregates", ["tenmin", "hourly", "daily"])
    until_dt   = datetime.now(dt_tz.utc)
    default_since = until_dt - timedelta(days=60)

    sensors = (
        list(Sensor.objects.filter(pk=sensor_id).select_related("site"))
        if sensor_id
        else list(Sensor.objects.select_related("site").all())
    )

    total_tenmin = total_hourly = total_daily = errors = 0

    for sensor in sensors:
        try:
            if db_raw:
                # ── Postgres / CanonicalReading path ──────────────────────────
                if since is None:
                    first = (
                        CanonicalReading.objects
                        .filter(sensor=sensor)
                        .order_by("original_ts")
                        .values_list("original_ts", flat=True)
                        .first()
                    )
                    since_dt = first if first else default_since
                else:
                    since_dt = since

                if "tenmin" in levels:
                    total_tenmin += _compute_ten_min_sql(sensor, since_dt, until_dt)
                if "hourly" in levels:
                    total_hourly += _compute_hourly_sql(sensor, since_dt, until_dt)
                if "daily" in levels:
                    total_daily  += _compute_daily_sql(sensor, since_dt, until_dt)

            else:
                # ── R2 parquet path ───────────────────────────────────────────
                # Default cron lookback is 2 days — enough to catch any missed
                # aggregations without downloading months of parquet every run.
                # Pass --since YYYY-MM-DDT00:00:00Z for a full historical backfill.
                cron_since = until_dt - timedelta(days=2)
                since_dt = since if since else cron_since
                result = _compute_aggregates_from_r2(sensor, since_dt, until_dt)
                total_tenmin += result["tenmin_rows"]
                total_hourly += result["hourly_rows"]
                total_daily  += result["daily_rows"]

            logger.info(
                "Aggregated sensor %s (cron): tenmin=%d hourly=%d daily=%d",
                sensor.serial_number, total_tenmin, total_hourly, total_daily,
            )

        except Exception as exc:
            logger.exception("Aggregation failed for sensor %s: %s", sensor, exc)
            errors += 1

    return {
        "sensors_processed": len(sensors),
        "tenmin_rows":  total_tenmin,
        "hourly_rows":  total_hourly,
        "daily_rows":   total_daily,
        "errors":       errors,
    }


# ── Legacy helper — now a no-op ────────────────────────────────────────────────

def aggregate_after_upload(sensor_ids: list) -> str:
    """
    Kept for backward compatibility (called by process_ingestion_jobs).
    Aggregation now happens synchronously inside each converter's run().
    This function is a no-op.
    """
    return "skipped"
