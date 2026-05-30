"""
Aggregation tasks: compute 10-min, hourly, and daily aggregate tables from
CanonicalReading (Postgres).  These replace the old DuckDB-based aggregation.

Entry points
────────────
compute_aggregates(sensor_id=None, since=None)
    Recomputes TenMinAggregate, HourlyAggregate, and DailyAggregate.
    Called by the aggregate_readings management command (Cron 1).

aggregate_after_upload(sensor_ids)
    Fire-and-forget: re-aggregates a list of sensors after a CSV upload.
"""
import logging
import math
from datetime import datetime, timedelta, timezone as dt_tz

from django.db import connection

logger = logging.getLogger(__name__)


# ── SQL helpers ───────────────────────────────────────────────────────────────

def _safe_float(v):
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


# ── 10-minute aggregation ─────────────────────────────────────────────────────

def _compute_ten_min(sensor, since_dt, until_dt) -> int:
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
            sensor=sensor,
            window_start=window_start,
            pollutant=pollutant,
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


# ── Hourly aggregation ────────────────────────────────────────────────────────

def _compute_hourly(sensor, since_dt, until_dt) -> int:
    from apps.readings.models import HourlyAggregate

    sql = """
        SELECT
            date_trunc('hour', original_ts) AS hour,
            pollutant,
            is_indoor,
            COUNT(*)              AS cnt,
            AVG(raw_value)        AS avg,
            STDDEV_SAMP(raw_value) AS std,
            MIN(raw_value)        AS lo,
            MAX(raw_value)        AS hi
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

    interval_s       = 60
    expected_per_hour = 3600 // interval_s
    count = 0

    for row in rows:
        hour, pollutant, is_indoor, cnt, avg, std, lo, hi = row
        completeness = min(1.0, int(cnt) / expected_per_hour)
        HourlyAggregate.objects.update_or_create(
            sensor=sensor,
            hour=hour,
            pollutant=pollutant,
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


# ── Daily aggregation ─────────────────────────────────────────────────────────

def _compute_daily(sensor, since_dt, until_dt) -> int:
    from apps.readings.models import DailyAggregate

    sql = """
        SELECT
            (original_ts AT TIME ZONE 'UTC')::date   AS date,
            pollutant,
            is_indoor,
            COUNT(*)                                                AS cnt,
            AVG(raw_value)                                         AS avg,
            STDDEV_SAMP(raw_value)                                 AS std,
            MIN(raw_value)                                         AS lo,
            MAX(raw_value)                                         AS hi,
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY raw_value) AS p25,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY raw_value) AS p50,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY raw_value) AS p75,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY raw_value) AS p95
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

    interval_s      = 60
    expected_per_day = 86400 // interval_s
    count = 0

    for row in rows:
        date, pollutant, is_indoor, cnt, avg, std, lo, hi, p25, p50, p75, p95 = row
        completeness = min(1.0, int(cnt) / expected_per_day)
        DailyAggregate.objects.update_or_create(
            sensor=sensor,
            date=date,
            pollutant=pollutant,
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


# ── Public entry point ────────────────────────────────────────────────────────

def compute_aggregates(sensor_id=None, since: datetime | None = None) -> dict:
    """
    Recompute TenMinAggregate, HourlyAggregate, and DailyAggregate from
    CanonicalReading (Postgres).

    Args:
        sensor_id: Sensor PK to aggregate; None → all sensors.
        since:     Recompute from this datetime onward.
                   Default: earliest reading for each sensor.
    """
    from apps.sensors.models import Sensor
    from apps.readings.models import CanonicalReading

    sensors    = (
        list(Sensor.objects.filter(pk=sensor_id).select_related("site"))
        if sensor_id
        else list(Sensor.objects.select_related("site").all())
    )
    until_dt   = datetime.now(dt_tz.utc)
    total_tenmin = total_hourly = total_daily = errors = 0

    for sensor in sensors:
        try:
            if since is None:
                first = (
                    CanonicalReading.objects
                    .filter(sensor=sensor)
                    .order_by("original_ts")
                    .values_list("original_ts", flat=True)
                    .first()
                )
                since_dt = first if first else until_dt - timedelta(days=365)
            else:
                since_dt = since

            total_tenmin += _compute_ten_min(sensor, since_dt, until_dt)
            total_hourly += _compute_hourly(sensor, since_dt, until_dt)
            total_daily  += _compute_daily(sensor, since_dt, until_dt)

            logger.info(
                "Aggregated sensor %s: tenmin=%d hourly=%d daily=%d",
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


# ── Background dispatch helper ────────────────────────────────────────────────

def aggregate_after_upload(sensor_ids: list) -> str:
    """
    Fire-and-forget aggregation after a successful CSV upload.
    Runs in a daemon thread so the upload response returns immediately.
    Periodic aggregation is also handled by the Render Cron Job (aggregate_readings).
    Returns 'thread' or 'skipped'.
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

    t = threading.Thread(target=_run_sync, daemon=True, name=f"agg-{sid_list}")
    t.start()
    return "thread"
