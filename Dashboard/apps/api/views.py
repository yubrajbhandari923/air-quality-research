"""
API views for the Nepal Air Quality Dashboard.

Endpoints:
  POST /api/v1/readings/             — submit single-timestamp readings (sensor API key)
  POST /api/v1/readings/batch/       — submit multiple timestamps (offline catch-up)
  GET  /api/v1/readings/             — query readings (researcher+ auth)
  POST /api/v1/aggregate/            — trigger hourly/daily aggregation (maintainer/admin)
  GET  /api/v1/export/               — CSV/JSON export with access-level controls
  GET  /api/v1/sensors/              — list sensors (public)
  GET  /api/v1/sensors/{id}/         — sensor detail (public)
  POST /api/v1/sensors/register/     — register new sensor (sensor API key)
  GET  /api/v1/sites/                — list sites (public)
  GET  /api/v1/charts/sensor/{id}/timeseries/   — chart data
  GET  /api/v1/charts/sensor/{id}/completeness/ — completeness chart data
  GET  /api/v1/charts/national/summary/         — national summary chart
"""
import csv
import io
import logging
from datetime import timedelta

import pytz
from django.db.models import Avg, Count, Max, Min
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ingestion.converters.api_converter import APIConverter
from apps.readings.models import CanonicalReading
from apps.sensors.models import Sensor, Site

from .permissions import HasResearcherPermission, HasSensorWritePermission, IsMaintainerOrAdmin
from .serializers import (
    BatchReadingSubmitSerializer,
    CanonicalReadingSerializer,
    ReadingSubmitSerializer,
    SensorRegisterSerializer,
    SensorSerializer,
    SiteSerializer,
)

logger = logging.getLogger(__name__)

NEPAL_TZ = pytz.timezone("Asia/Kathmandu")

# ── Wide-format export constants ───────────────────────────────────────────────

# Maps internal pollutant codes → CSV column names (matching native sensor format)
_WIDE_POLLUTANT_MAP = {
    "PM1":   "PM1.0",
    "PM25":  "PM2.5",
    "PM4":   "PM4.0",
    "PM10":  "PM10",
    "CO2":   "CO2",
    "CH2O":  "CH2O",
    "BARO":  "Barometric Pressure",
    "CO":    "CO",
    "SO2":   "SO2",
    "O3":    "O3",
    "NO2":   "NO2",
    "TVOC":  "VOC tVOC measurement",
    "TEMP":  "Temperature",
    "RH":    "Relative Humidity",
    "NC05":  "PM0.5 NC",
    "NC1":   "PM1.0 NC",
    "NC25":  "PM2.5 NC",
    "NC4":   "PM4.0 NC",
    "NC10":  "PM10 NC",
    "TYPSZ": "Typical Particle Size",
}

# Canonical column order for wide-format raw export
_WIDE_COLUMN_ORDER = [
    "Timestamp", "Device ID", "Serial Number", "Model", "Sub Model",
    "Friendly Name", "Latitude", "Longitude", "Is Indoor", "Is Public",
    "PM2.5 AQI", "PM10 AQI",
    "PM1.0", "PM2.5",
    "Applied PM2.5 Custom Calibration Setting - Multiplication Factor",
    "Applied PM2.5 Custom Calibration Setting - Offset",
    "PM4.0", "PM10",
    "Applied PM10 Custom Calibration Setting - Multiplication Factor",
    "Applied PM10 Custom Calibration Setting - Offset",
    "PM0.5 NC", "PM1.0 NC", "PM2.5 NC", "PM4.0 NC", "PM10 NC",
    "Typical Particle Size", "PM Sensor Status",
    "CO2",
    "Applied CO2 Custom Calibration Setting - Multiplication Factor",
    "Applied CO2 Custom Calibration Setting - Offset",
    "CO2 Sensor Status",
    "CH2O",
    "Applied CH2O Custom Calibration Setting - Multiplication Factor",
    "Applied CH2O Custom Calibration Setting - Offset",
    "CH2O Sensor Status",
    "Barometric Pressure",
    "Applied Barometric Pressure Custom Calibration Setting - Offset",
    "Barometric Sensor Status",
    "CO",
    "Applied CO Custom Calibration Setting - Multiplication Factor",
    "Applied CO Custom Calibration Setting - Offset",
    "CO Sensor Status",
    "SO2",
    "Applied SO2 Custom Calibration Setting - Multiplication Factor",
    "Applied SO2 Custom Calibration Setting - Offset",
    "SO2 Sensor Status",
    "O3",
    "Applied O3 Custom Calibration Setting - Multiplication Factor",
    "Applied O3 Custom Calibration Setting - Offset",
    "O3 Sensor Status",
    "NO2",
    "Applied NO2 Custom Calibration Setting - Multiplication Factor",
    "Applied NO2 Custom Calibration Setting - Offset",
    "NO2 Sensor Status",
    "VOC tVOC measurement",
    "Applied TVOC Custom Calibration Setting - Multiplication Factor",
    "Applied TVOC Custom Calibration Setting - Offset",
    "VOC Sensor Status",
    "Temperature",
    "Applied Temperature Custom Calibration Setting - Offset",
    "Relative Humidity",
    "Applied Relative Humidity Custom Calibration Setting - Offset",
    "Temperature/Humidity Sensor Status",
    "System Status",
]

# Calibration offset columns use <nil> as their null placeholder (to match native format)
_OFFSET_COLS = {c for c in _WIDE_COLUMN_ORDER if "Offset" in c}


def _compute_pm25_aqi(v):
    """US EPA PM2.5 AQI from µg/m³ concentration."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    breakpoints = [
        (0.0,   12.0,   0,   50),
        (12.1,  35.4,  51,  100),
        (35.5,  55.4, 101,  150),
        (55.5, 150.4, 151,  200),
        (150.5, 250.4, 201, 300),
        (250.5, 350.4, 301, 400),
        (350.5, 500.4, 401, 500),
    ]
    for lo_c, hi_c, lo_i, hi_i in breakpoints:
        if lo_c <= v <= hi_c:
            return round((hi_i - lo_i) / (hi_c - lo_c) * (v - lo_c) + lo_i)
    return None


def _compute_pm10_aqi(v):
    """US EPA PM10 AQI from µg/m³ concentration."""
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    breakpoints = [
        (0,    54,   0,   50),
        (55,  154,  51,  100),
        (155, 254, 101,  150),
        (255, 354, 151,  200),
        (355, 424, 201,  300),
        (425, 504, 301,  400),
        (505, 604, 401,  500),
    ]
    for lo_c, hi_c, lo_i, hi_i in breakpoints:
        if lo_c <= v <= hi_c:
            return round((hi_i - lo_i) / (hi_c - lo_c) * (v - lo_c) + lo_i)
    return None


# Physical plausibility bounds per pollutant (lo, hi) — values outside are treated as sensor errors
_PHYSICAL_BOUNDS: dict[str, tuple[float, float]] = {
    "PM1":  (0, 500),  "PM25": (0, 500),  "PM4": (0, 600),  "PM10": (0, 700),
    "TEMP": (-20, 60), "RH":   (0, 100),  "BARO": (25, 32),
    "CO2":  (200, 6000), "TVOC": (0, 15), "CH2O": (0, 5),
    "CO":   (0, 100),  "SO2":  (0, 2000), "O3":   (0, 600), "NO2":  (0, 2000),
}


def _filter_chart_outliers(values: list, pollutant: str) -> list:
    """
    Remove physically impossible values then apply Tukey outer fence (k=3·IQR).
    Outliers are replaced with None so Chart.js renders a gap rather than a spike.
    Raw data in the database is never modified.
    """
    lo, hi = _PHYSICAL_BOUNDS.get(pollutant, (None, None))

    # Physical bounds pass
    cleaned = []
    for v in values:
        if v is None:
            cleaned.append(None)
        elif (lo is not None and v < lo) or (hi is not None and v > hi):
            cleaned.append(None)
        else:
            cleaned.append(v)

    # IQR-based spike removal (needs ≥ 8 valid points to be meaningful)
    valid = sorted(v for v in cleaned if v is not None)
    if len(valid) < 8:
        return cleaned

    n = len(valid)
    q1 = valid[n // 4]
    q3 = valid[(3 * n) // 4]
    iqr = q3 - q1
    if iqr <= 0:
        return cleaned

    fence_lo = q1 - 3.0 * iqr
    fence_hi = q3 + 3.0 * iqr
    return [v if (v is None or fence_lo <= v <= fence_hi) else None for v in cleaned]


# ── Readings ──────────────────────────────────────────────────────────────────

class ReadingListView(APIView):
    """
    GET  /api/v1/readings/ — query readings
    POST /api/v1/readings/ — submit readings from sensor (single timestamp)
    """

    def get_permissions(self):
        if self.request.method == "POST":
            return [HasSensorWritePermission()]
        return [HasResearcherPermission()]

    def get(self, request):
        qs = CanonicalReading.objects.select_related("sensor", "site").order_by("-original_ts")

        # Filters
        sensor_id = request.query_params.get("sensor")
        pollutant = request.query_params.get("pollutant")
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        quality = request.query_params.get("quality")
        is_indoor = request.query_params.get("indoor")

        if sensor_id:
            qs = qs.filter(sensor_id=sensor_id)
        if pollutant:
            qs = qs.filter(pollutant=pollutant.upper())
        if start:
            try:
                qs = qs.filter(original_ts__gte=start)
            except ValueError:
                return Response({"error": "Invalid start date."}, status=400)
        if end:
            try:
                qs = qs.filter(original_ts__lte=end)
            except ValueError:
                return Response({"error": "Invalid end date."}, status=400)
        if quality:
            qs = qs.filter(quality_flag=quality.upper())
        if is_indoor in ("true", "false"):
            qs = qs.filter(is_indoor=(is_indoor == "true"))

        # Exclude duplicates by default
        qs = qs.filter(is_duplicate=False)

        # Pagination
        page_size = min(int(request.query_params.get("page_size", 100)), 10000)
        page = int(request.query_params.get("page", 1))
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        total = qs.count()
        readings = qs[start_idx:end_idx]
        serializer = CanonicalReadingSerializer(readings, many=True)

        return Response({
            "count": total,
            "page": page,
            "page_size": page_size,
            "results": serializer.data,
        })

    def post(self, request):
        serializer = ReadingSubmitSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Resolve serial_number: from payload OR from the bound sensor on the API key
        data = serializer.validated_data
        serial = data.get("serial_number", "").strip()
        api_key = getattr(request, "api_key", None)
        if not serial and api_key and api_key.sensor:
            serial = api_key.sensor.serial_number
            data = dict(data)
            data["serial_number"] = serial

        converter = APIConverter()
        result = converter.run(data)

        if result["status"] == "FAILED":
            return Response(
                {"error": result.get("error", "Ingestion failed.")},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(result, status=status.HTTP_201_CREATED)


# ── Batch readings submission ─────────────────────────────────────────────────

class BatchReadingView(APIView):
    """
    POST /api/v1/readings/batch/

    Submit multiple timestamped snapshots in one request.  Designed for:
    - Offline sensors catching up after a power/connectivity outage
    - Scrapers pushing historical measurements
    - Testing / data migration

    The sensor is identified either by the ``serial_number`` field in the
    payload or — if the API key is bound to a specific sensor — inferred
    automatically.

    Duplicate timestamps are handled gracefully: the DB unique constraint
    silently skips exact duplicates; conflicts (same timestamp, different value)
    are flagged and counted in the response.

    Request body (JSON):
    {
        "serial_number": "81432434001",   // optional when key has linked sensor
        "readings": [
            {
                "timestamp": "2026-05-29T08:00:00Z",
                "measurements": [
                    {"pollutant": "PM25", "value": 45.2, "unit": "µg/m³"},
                    {"pollutant": "TEMP", "value": 18.5, "unit": "°C"}
                ]
            },
            ...  // up to 10 000 snapshots per request
        ]
    }
    """

    permission_classes = [HasSensorWritePermission]

    def post(self, request):
        serializer = BatchReadingSubmitSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # Resolve sensor: from payload or from the API key
        serial = data.get("serial_number", "").strip()
        api_key = getattr(request, "api_key", None)

        if not serial and api_key and api_key.sensor:
            serial = api_key.sensor.serial_number

        if not serial:
            return Response(
                {"error": "serial_number is required when the API key is not bound to a sensor."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            sensor = Sensor.objects.select_related("site").get(serial_number=serial)
        except Sensor.DoesNotExist:
            return Response(
                {"error": f"Sensor '{serial}' not registered. Register it via the portal first."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # If key has a linked sensor, enforce it matches
        if api_key and api_key.sensor and api_key.sensor.serial_number != serial:
            return Response(
                {"error": "API key is scoped to a different sensor."},
                status=status.HTTP_403_FORBIDDEN,
            )

        from apps.readings.models import IngestionLog

        converter = APIConverter()
        all_records = []

        for snapshot in data["readings"]:
            ts_str = snapshot["timestamp"].isoformat()
            single_payload = {
                "serial_number": serial,
                "timestamp": ts_str,
                "readings": [
                    {"pollutant": m["pollutant"], "value": m["value"], "unit": m["unit"]}
                    for m in snapshot["measurements"]
                ],
            }
            df = converter._read_source(single_payload)
            records = converter.map_columns(df)
            records = converter.assign_quality_flags(records)
            for rec in records:
                rec["sensor_id"] = sensor.pk
                rec["site_id"] = sensor.site_id
                rec["is_indoor"] = sensor.is_indoor
            all_records.extend(records)

        if not all_records:
            return Response({"saved": 0, "duplicates": 0, "errors": 0, "snapshots": 0},
                            status=status.HTTP_200_OK)

        result = converter.save_to_canonical_schema(all_records)

        # Update aggregate tables from the in-memory batch (keeps dashboard live)
        try:
            from apps.ingestion.tasks import compute_aggregates_from_records
            compute_aggregates_from_records(all_records, {serial: sensor})
        except Exception as exc:
            logger.warning("BatchReadingView: aggregation failed for %s: %s", serial, exc)

        IngestionLog.objects.create(
            source_name="BatchAPIConverter",
            status=IngestionLog.Status.SUCCESS if result["errors"] == 0 else IngestionLog.Status.PARTIAL,
            records_attempted=len(all_records),
            records_saved=result["saved"],
            records_duplicate=result["duplicates"],
            records_error=result["errors"],
        )

        return Response(
            {
                "snapshots": len(data["readings"]),
                "measurements_attempted": len(all_records),
                "saved": result["saved"],
                "duplicates": result["duplicates"],
                "errors": result["errors"],
            },
            status=status.HTTP_201_CREATED,
        )


# ── Aggregation trigger ───────────────────────────────────────────────────────

class AggregationTriggerView(APIView):
    """
    POST /api/v1/aggregate/

    Trigger a synchronous recompute of HourlyAggregate and DailyAggregate.
    Requires MAINTAINER or ADMIN API key.

    Body (JSON, all optional):
    {
        "sensor_id": 1,           // omit to aggregate all sensors
        "since": "2026-01-01"     // ISO date; omit to recompute everything
    }

    Response: {"sensors_processed":N, "hourly_rows":N, "daily_rows":N, "errors":N}
    """

    permission_classes = [IsMaintainerOrAdmin]

    def post(self, request):
        from apps.ingestion.tasks import compute_aggregates
        from datetime import datetime, timezone as dt_tz

        sensor_id = request.data.get("sensor_id")
        since_str = request.data.get("since")
        since = None
        if since_str:
            try:
                since = datetime.fromisoformat(since_str).replace(tzinfo=dt_tz.utc)
            except ValueError:
                return Response({"error": f"Invalid 'since' date: {since_str}"}, status=400)

        result = compute_aggregates(sensor_id=sensor_id, since=since)
        return Response(result)


# ── Sensors ───────────────────────────────────────────────────────────────────

class SensorListView(APIView):
    """GET /api/v1/sensors/ — list all sensors (public)."""

    permission_classes = [AllowAny]

    def get(self, request):
        qs = Sensor.objects.select_related("site").order_by("site__name", "serial_number")

        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter.upper())

        site_id = request.query_params.get("site")
        if site_id:
            qs = qs.filter(site_id=site_id)

        serializer = SensorSerializer(qs, many=True)
        return Response(serializer.data)


class SensorDetailView(APIView):
    """GET /api/v1/sensors/{id}/ — sensor detail + recent stats."""

    permission_classes = [AllowAny]

    def get(self, request, pk):
        try:
            sensor = Sensor.objects.select_related("site").get(pk=pk)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        data = SensorSerializer(sensor).data

        # Append recent PM2.5 stats
        recent = CanonicalReading.objects.filter(
            sensor=sensor,
            pollutant="PM25",
            is_duplicate=False,
            original_ts__gte=timezone.now() - timedelta(days=7),
        ).aggregate(
            avg=Avg("raw_value"),
            min_val=Min("raw_value"),
            max_val=Max("raw_value"),
            count=Count("id"),
        )
        data["recent_pm25_7d"] = recent

        return Response(data)


class SensorRegisterView(APIView):
    """POST /api/v1/sensors/register/ — register a new sensor."""

    permission_classes = [HasSensorWritePermission]

    def post(self, request):
        serializer = SensorRegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        sensor, created = Sensor.objects.get_or_create(
            serial_number=serializer.validated_data["serial_number"],
            defaults=serializer.validated_data,
        )

        if not created:
            return Response(
                {"message": "Sensor already registered.", "sensor": SensorSerializer(sensor).data},
                status=200,
            )

        return Response(
            {"message": "Sensor registered.", "sensor": SensorSerializer(sensor).data},
            status=201,
        )


# ── Sites ─────────────────────────────────────────────────────────────────────

class SiteListView(APIView):
    """GET /api/v1/sites/ — list all monitoring sites."""

    permission_classes = [AllowAny]

    def get(self, request):
        sites = Site.objects.all().order_by("name")
        serializer = SiteSerializer(sites, many=True)
        return Response(serializer.data)


# ── Chart data ────────────────────────────────────────────────────────────────

class TimeSeriesChartView(APIView):
    """
    GET /api/v1/charts/sensor/{id}/timeseries/

    Query params:
      pollutant  (required) — e.g. PM25
      start      (optional) — ISO 8601 datetime
      end        (optional) — ISO 8601 datetime
      window     (optional) — 1d|7d|30d|90d (used when anchor=latest)
      anchor     (optional) — "now" (default) | "latest" (anchor window to last reading)

    Resolution is chosen automatically from the requested span:
      ≤ 2 days   → raw minute data (outlier-filtered, max 2 000 points)
      ≤ 60 days  → HourlyAggregate (falls back to raw if aggregates absent)
      > 60 days  → DailyAggregate  (falls back to raw if aggregates absent)

    Returns extra fields: resolution ("raw"|"hourly"|"daily"), time_unit (Chart.js hint).
    """

    permission_classes = [AllowAny]

    # Resolution thresholds (days)
    _HOURLY_THRESHOLD = 2
    _DAILY_THRESHOLD  = 60

    def get(self, request, sensor_id):
        from datetime import datetime as _dt
        import dateutil.parser as _dp

        pollutant = request.query_params.get("pollutant", "PM25").upper()
        start_raw = request.query_params.get("start")
        end_raw   = request.query_params.get("end")
        anchor    = request.query_params.get("anchor", "now")
        window    = request.query_params.get("window", "7d")

        try:
            sensor = Sensor.objects.get(pk=sensor_id)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        # ── Determine time bounds ────────────────────────────────────────────
        base_qs = CanonicalReading.objects.filter(
            sensor=sensor, pollutant=pollutant, is_duplicate=False,
        )

        if start_raw or end_raw:
            start_ts = _dp.parse(start_raw).replace(tzinfo=timezone.utc) if start_raw else None
            end_ts   = _dp.parse(end_raw).replace(tzinfo=timezone.utc)   if end_raw   else None
            if start_ts and not timezone.is_aware(start_ts):
                start_ts = timezone.make_aware(start_ts)
            if end_ts and not timezone.is_aware(end_ts):
                end_ts = timezone.make_aware(end_ts)
        else:
            window_days = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}.get(window, 7)
            if anchor == "latest":
                latest_ts = base_qs.aggregate(max_ts=Max("original_ts"))["max_ts"]
                end_ts = latest_ts or timezone.now()
            else:
                end_ts = timezone.now()
            start_ts = end_ts - timedelta(days=window_days)

        # span used for resolution selection
        if start_ts and end_ts:
            span_days = max(1, (end_ts - start_ts).total_seconds() / 86400)
        else:
            span_days = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}.get(window, 7)

        # ── Choose resolution ────────────────────────────────────────────────
        if span_days <= self._HOURLY_THRESHOLD:
            resolution = "raw"
            time_unit  = "hour"
        elif span_days <= self._DAILY_THRESHOLD:
            resolution = "hourly"
            time_unit  = "day"
        else:
            resolution = "daily"
            time_unit  = "week"

        # ── Build chart data by resolution ───────────────────────────────────
        data = self._fetch_data(
            resolution, sensor, pollutant, start_ts, end_ts, base_qs,
        )

        # Return total_points as count of original raw rows for the badge label
        total = base_qs.filter(
            original_ts__gte=start_ts, original_ts__lte=end_ts,
        ).count() if start_ts and end_ts else len(data)

        return Response({
            "sensor_id":    sensor.pk,
            "sensor_name":  sensor.display_name,
            "pollutant":    pollutant,
            "resolution":   resolution,
            "time_unit":    time_unit,
            "total_points": total,
            "points":       data,
            "data_start":   data[0]["ts"]  if data else None,
            "data_end":     data[-1]["ts"] if data else None,
        })

    # ── Private helpers ───────────────────────────────────────────────────────

    def _fetch_data(self, resolution, sensor, pollutant, start_ts, end_ts, base_qs):
        if resolution == "hourly":
            data = self._fetch_hourly(sensor, pollutant, start_ts, end_ts)
            if data:
                return data
            # Fall back to raw if no hourly aggregates exist yet
        if resolution == "daily":
            data = self._fetch_daily(sensor, pollutant, start_ts, end_ts)
            if data:
                return data

        # Raw path (also fallback for hourly/daily when aggregates are empty)
        return self._fetch_raw(base_qs, pollutant, start_ts, end_ts)

    def _fetch_raw(self, base_qs, pollutant, start_ts, end_ts):
        qs = base_qs.order_by("original_ts")
        if start_ts:
            qs = qs.filter(original_ts__gte=start_ts)
        if end_ts:
            qs = qs.filter(original_ts__lte=end_ts)

        if qs.exists():
            MAX_POINTS = 2000
            total = qs.count()
            if total > MAX_POINTS:
                step = max(1, total // MAX_POINTS)
                ids = list(qs.values_list("id", flat=True)[::step])
                qs = CanonicalReading.objects.filter(id__in=ids).order_by("original_ts")
            rows = list(qs.values("original_ts", "raw_value"))
            values = [r["raw_value"] for r in rows]
            timestamps = [r["original_ts"].isoformat() for r in rows]
            filtered = _filter_chart_outliers(values, pollutant)
            return [{"ts": ts, "value": v} for ts, v in zip(timestamps, filtered)]

        return []

    def _fetch_hourly(self, sensor, pollutant, start_ts, end_ts):
        from apps.readings.models import HourlyAggregate
        qs = (
            HourlyAggregate.objects
            .filter(sensor=sensor, pollutant=pollutant)
            .order_by("hour")
        )
        if start_ts:
            qs = qs.filter(hour__gte=start_ts)
        if end_ts:
            qs = qs.filter(hour__lte=end_ts)
        rows = list(qs.values("hour", "mean"))
        if not rows:
            return []
        values = [r["mean"] for r in rows]
        timestamps = [r["hour"].isoformat() for r in rows]
        filtered = _filter_chart_outliers(values, pollutant)
        return [{"ts": ts, "value": v} for ts, v in zip(timestamps, filtered)]

    def _fetch_daily(self, sensor, pollutant, start_ts, end_ts):
        import datetime as _datetime
        from apps.readings.models import DailyAggregate
        qs = (
            DailyAggregate.objects
            .filter(sensor=sensor, pollutant=pollutant)
            .order_by("date")
        )
        if start_ts:
            qs = qs.filter(date__gte=start_ts.date())
        if end_ts:
            qs = qs.filter(date__lte=end_ts.date())
        rows = list(qs.values("date", "mean"))
        if not rows:
            return []
        values = [r["mean"] for r in rows]
        timestamps = [
            _datetime.datetime(r["date"].year, r["date"].month, r["date"].day,
                               tzinfo=_datetime.timezone.utc).isoformat()
            for r in rows
        ]
        filtered = _filter_chart_outliers(values, pollutant)
        return [{"ts": ts, "value": v} for ts, v in zip(timestamps, filtered)]


class CompletenessChartView(APIView):
    """
    GET /api/v1/charts/sensor/{id}/completeness/

    Returns daily completeness fractions for the last 30 days.
    Source: DailyAggregate (falls back to CanonicalReading raw counts if not yet aggregated).
    """

    permission_classes = [AllowAny]

    def get(self, request, sensor_id):
        try:
            sensor = Sensor.objects.get(pk=sensor_id)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        expected_per_day = int(request.query_params.get("expected", 1440))  # 1-min data
        days = int(request.query_params.get("days", 30))
        cutoff = timezone.now() - timedelta(days=days)

        from apps.readings.models import DailyAggregate

        agg_qs = (
            DailyAggregate.objects
            .filter(sensor=sensor, pollutant="PM25", date__gte=cutoff.date())
            .order_by("date")
            .values("date", "count", "completeness")
        )
        rows = list(agg_qs)

        if rows:
            data = [
                {
                    "date": str(r["date"]),
                    "expected": expected_per_day,
                    "actual": r["count"],
                    "completeness_pct": round(
                        float(r["completeness"]) * 100, 1
                    ) if r["completeness"] is not None else round(
                        min(100, r["count"] / expected_per_day * 100), 1
                    ),
                }
                for r in rows
            ]
        else:
            # Fallback: count raw CanonicalReading rows by day if aggregates not yet computed.
            from django.db.models import Count
            from django.db.models.functions import TruncDate
            try:
                rows = (
                    CanonicalReading.objects
                    .filter(sensor=sensor, pollutant="PM25", is_duplicate=False,
                            original_ts__gte=cutoff)
                    .annotate(date=TruncDate("original_ts"))
                    .values("date")
                    .annotate(cnt=Count("id"))
                    .order_by("date")
                )
                data = [
                    {
                        "date": str(r["date"]),
                        "expected": expected_per_day,
                        "actual": r["cnt"],
                        "completeness_pct": round(
                            min(100, r["cnt"] / expected_per_day * 100), 1
                        ),
                    }
                    for r in rows
                ]
            except Exception:
                data = []

        return Response({
            "sensor_id": sensor.pk,
            "sensor_name": sensor.display_name,
            "days": days,
            "data": data,
        })


class NationalSummaryChartView(APIView):
    """
    GET /api/v1/charts/national/summary/

    Returns latest PM2.5 reading per sensor plus WHO guideline comparison.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        WHO_ANNUAL = 5.0    # WHO 2021 annual mean guideline µg/m³
        WHO_24H = 15.0      # WHO 2021 24-hour guideline µg/m³
        NEPAL_24H = 40.0    # Nepal NAAQS 24-hour standard µg/m³

        from apps.readings.models import DailyAggregate, HourlyAggregate

        sensors = Sensor.objects.filter(status="ACTIVE").select_related("site")
        cutoff_24h = timezone.now() - timedelta(hours=24)
        summary = []

        for sensor in sensors:
            # Latest daily aggregate for recent PM2.5
            latest_daily = (
                DailyAggregate.objects
                .filter(sensor=sensor, pollutant="PM25")
                .order_by("-date")
                .first()
            )

            # 24-hour average from hourly aggregates
            avg_24h_agg = (
                HourlyAggregate.objects
                .filter(sensor=sensor, pollutant="PM25", hour__gte=cutoff_24h)
                .aggregate(avg=Avg("mean"))["avg"]
            )
            # Fall back to daily aggregate if no hourly data in last 24h
            if avg_24h_agg is None and latest_daily:
                avg_24h_agg = latest_daily.mean

            latest_val = latest_daily.mean if latest_daily else None
            latest_ts  = None
            if latest_daily:
                import datetime as _dt
                latest_ts = _dt.datetime(
                    latest_daily.date.year, latest_daily.date.month, latest_daily.date.day,
                    tzinfo=_dt.timezone.utc,
                ).isoformat()

            summary.append({
                "sensor_id":       sensor.pk,
                "sensor_name":     sensor.display_name,
                "site_name":       sensor.site.name,
                "site_district":   sensor.site.district,
                "latitude":        sensor.site.latitude,
                "longitude":       sensor.site.longitude,
                "is_indoor":       sensor.is_indoor,
                "status":          sensor.status,
                "latest_pm25":     round(latest_val, 1) if latest_val is not None else None,
                "latest_ts":       latest_ts,
                "avg_pm25_24h":    round(avg_24h_agg, 1) if avg_24h_agg else None,
                "exceeds_who_24h":   (avg_24h_agg > WHO_24H) if avg_24h_agg else None,
                "exceeds_nepal_24h": (avg_24h_agg > NEPAL_24H) if avg_24h_agg else None,
            })

        return Response({
            "guidelines": {
                "who_annual_ugm3": WHO_ANNUAL,
                "who_24h_ugm3": WHO_24H,
                "nepal_naaqs_24h_ugm3": NEPAL_24H,
            },
            "sensors": summary,
        })


# ── Diurnal, monthly, I/O, date-range chart endpoints ────────────────────────

class SensorDateRangeView(APIView):
    """
    GET /api/v1/charts/sensor/{id}/date-range/?pollutant=PM25

    Returns min/max dates from DailyAggregate (falls back to CanonicalReading
    if aggregates have not yet been computed for a sensor).
    """
    permission_classes = [AllowAny]

    def get(self, request, sensor_id):
        try:
            sensor = Sensor.objects.get(pk=sensor_id)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        pollutant = request.query_params.get("pollutant", "PM25").upper()

        from apps.readings.models import DailyAggregate
        agg = DailyAggregate.objects.filter(
            sensor=sensor, pollutant=pollutant,
        ).aggregate(min_date=Min("date"), max_date=Max("date"))

        min_date = agg["min_date"]
        max_date = agg["max_date"]

        if not min_date:
            cr_agg = CanonicalReading.objects.filter(
                sensor=sensor, pollutant=pollutant, is_duplicate=False,
            ).aggregate(min_ts=Min("original_ts"), max_ts=Max("original_ts"))
            if cr_agg["min_ts"]:
                min_date = cr_agg["min_ts"].date()
                max_date = cr_agg["max_ts"].date()

        import datetime as _dt
        def _to_iso_ts(d):
            if d is None:
                return None
            return _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc).isoformat()

        return Response({
            "sensor_id": sensor.pk,
            "pollutant": pollutant,
            "min_ts":   _to_iso_ts(min_date),
            "max_ts":   _to_iso_ts(max_date),
            "min_date": min_date.isoformat() if min_date else None,
            "max_date": max_date.isoformat() if max_date else None,
        })


class DiurnalChartView(APIView):
    """
    GET /api/v1/charts/sensor/{id}/diurnal/

    Hour-of-day profile (0–23 NST) computed from HourlyAggregate.
    NST = UTC + 5h 45m, so UTC hour H → NST hour (H + 5) % 24.

    Query params:
      pollutant  — PM25 (default)
      start      — ISO date
      end        — ISO date
    """
    permission_classes = [AllowAny]

    def get(self, request, sensor_id):
        from django.db.models.functions import ExtractHour
        try:
            sensor = Sensor.objects.get(pk=sensor_id)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        pollutant = request.query_params.get("pollutant", "PM25").upper()
        start = request.query_params.get("start")
        end = request.query_params.get("end")

        from apps.readings.models import HourlyAggregate

        qs = HourlyAggregate.objects.filter(sensor=sensor, pollutant=pollutant)
        if start:
            qs = qs.filter(hour__date__gte=start)
        if end:
            qs = qs.filter(hour__date__lte=end)
        else:
            qs = qs.filter(hour__gte=timezone.now() - timedelta(days=90))

        # Pull utc_hour + mean + count; group into NST hours in Python
        raw = (
            qs.annotate(utc_h=ExtractHour("hour"))
            .values("utc_h", "mean", "count")
        )

        by_nst: dict = {}
        for row in raw:
            nst_h = (row["utc_h"] + 5) % 24  # UTC+5:45; 45-min offset rounds to +5h at boundary
            if nst_h not in by_nst:
                by_nst[nst_h] = {"wsum": 0.0, "cnt": 0}
            if row["mean"] is not None and row["count"]:
                by_nst[nst_h]["wsum"] += float(row["mean"]) * int(row["count"])
                by_nst[nst_h]["cnt"]  += int(row["count"])

        hours = list(range(24))
        values = []
        counts = []
        for h in hours:
            entry = by_nst.get(h)
            if entry and entry["cnt"] > 0:
                values.append(round(entry["wsum"] / entry["cnt"], 2))
                counts.append(entry["cnt"])
            else:
                values.append(None)
                counts.append(0)

        return Response({
            "sensor_id":   sensor.pk,
            "sensor_name": sensor.display_name,
            "pollutant":   pollutant,
            "hours":       hours,
            "labels":      [f"{h:02d}:00" for h in hours],
            "values":      values,
            "counts":      counts,
            "note": "Hours in Nepal Standard Time (UTC+5:45). Values are count-weighted hourly means.",
        })


class MonthlyChartView(APIView):
    """
    GET /api/v1/charts/sensor/{id}/monthly/

    Returns monthly average + max per pollutant, sourced from DailyAggregate.

    Query params:
      pollutant  — PM25 (default)
    """
    permission_classes = [AllowAny]

    def get(self, request, sensor_id):
        from django.db.models import Avg, Max, Sum
        from django.db.models.functions import TruncMonth
        from apps.readings.models import DailyAggregate

        try:
            sensor = Sensor.objects.get(pk=sensor_id)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        pollutant = request.query_params.get("pollutant", "PM25").upper()

        # Group DailyAggregate rows by calendar month using count-weighted average
        rows = list(
            DailyAggregate.objects
            .filter(sensor=sensor, pollutant=pollutant)
            .annotate(month=TruncMonth("date"))
            .values("month")
            .annotate(
                avg=Avg("mean"),
                max_val=Max("max_value"),
                total_count=Sum("count"),
            )
            .order_by("month")
        )

        months = [str(r["month"])[:7] for r in rows]
        avgs   = [round(float(r["avg"]), 1) if r["avg"] is not None else None for r in rows]
        maxs   = [round(float(r["max_val"]), 1) if r["max_val"] is not None else None for r in rows]
        counts = [int(r["total_count"]) if r["total_count"] else 0 for r in rows]

        return Response({
            "sensor_id":   sensor.pk,
            "sensor_name": sensor.display_name,
            "pollutant":   pollutant,
            "months":      months,
            "avg":         avgs,
            "max":         maxs,
            "counts":      counts,
        })


class IOComparisonView(APIView):
    """
    GET /api/v1/charts/site/{site_id}/io-comparison/

    Returns paired indoor/outdoor diurnal profiles for a site.

    Query params:
      pollutant  — PM25 (default)
      start      — ISO date
      end        — ISO date
    """
    permission_classes = [AllowAny]

    def get(self, request, site_id):
        from django.db.models import Avg
        try:
            site = Site.objects.get(pk=site_id)
        except Site.DoesNotExist:
            return Response({"error": "Site not found."}, status=404)

        indoor = Sensor.objects.filter(site=site, is_indoor=True, status="ACTIVE").first()
        outdoor = Sensor.objects.filter(site=site, is_indoor=False, status="ACTIVE").first()

        if not indoor or not outdoor:
            return Response({"error": "Site does not have an active indoor+outdoor pair."}, status=404)

        pollutant = request.query_params.get("pollutant", "PM25").upper()
        start = request.query_params.get("start")
        end = request.query_params.get("end")

        def diurnal(sensor):
            from django.db.models.functions import ExtractHour
            from apps.readings.models import HourlyAggregate

            qs = HourlyAggregate.objects.filter(sensor=sensor, pollutant=pollutant)
            if start:
                qs = qs.filter(hour__date__gte=start)
            if end:
                qs = qs.filter(hour__date__lte=end)
            else:
                qs = qs.filter(hour__gte=timezone.now() - timedelta(days=90))

            raw = qs.annotate(utc_h=ExtractHour("hour")).values("utc_h", "mean", "count")

            # Count-weighted mean per NST hour (UTC + 5h; 45-min remainder rounds to +5)
            by_nst: dict = {}
            for row in raw:
                nst_h = (row["utc_h"] + 5) % 24
                if nst_h not in by_nst:
                    by_nst[nst_h] = {"wsum": 0.0, "cnt": 0}
                if row["mean"] is not None and row["count"]:
                    by_nst[nst_h]["wsum"] += float(row["mean"]) * int(row["count"])
                    by_nst[nst_h]["cnt"]  += int(row["count"])

            result = []
            for h in range(24):
                entry = by_nst.get(h)
                if entry and entry["cnt"] > 0:
                    result.append(round(entry["wsum"] / entry["cnt"], 2))
                else:
                    result.append(None)
            return result

        hours = list(range(24))
        labels = [f"{h:02d}:00" for h in hours]
        outdoor_vals = diurnal(outdoor)
        indoor_vals = diurnal(indoor)

        # I/O ratio per hour
        io_ratio = []
        for i_val, o_val in zip(indoor_vals, outdoor_vals):
            if i_val and o_val and o_val > 0:
                io_ratio.append(round(i_val / o_val, 3))
            else:
                io_ratio.append(None)

        return Response({
            "site_id": site.pk,
            "site_name": site.name,
            "indoor_sensor": {"id": indoor.pk, "name": indoor.display_name},
            "outdoor_sensor": {"id": outdoor.pk, "name": outdoor.display_name},
            "pollutant": pollutant,
            "labels": labels,
            "indoor": indoor_vals,
            "outdoor": outdoor_vals,
            "io_ratio": io_ratio,
            "who_24h": 15.0,
        })


# ── CSV / JSON export ─────────────────────────────────────────────────────────

def _get_download_limits(user):
    """Return (max_rows, days_limit) based on the user's role."""
    try:
        from apps.analysis.models import DownloadConfig
        cfg = DownloadConfig.get()
    except Exception:
        # Sensible fallbacks if model not yet migrated
        class _Cfg:
            max_rows_public = 500
            max_rows_researcher = 50000
            max_rows_admin = 500000
            public_days_limit = 7
        cfg = _Cfg()

    if not user or not user.is_authenticated:
        return cfg.max_rows_public, cfg.public_days_limit
    role = getattr(user, "role", "PUBLIC")
    if role in ("ADMIN", "MAINTAINER"):
        return cfg.max_rows_admin, None
    if role == "RESEARCHER":
        return cfg.max_rows_researcher, None
    return cfg.max_rows_public, cfg.public_days_limit


class ExportReadingsView(APIView):
    """
    GET /api/v1/export/

    Unified CSV/JSON export endpoint with access-level controls.

    Query params:
      sensors    — comma-separated sensor IDs, e.g. 1,2,3  (also accepts single 'sensor')
      pollutants — comma-separated pollutant codes, e.g. PM25,PM10  (empty = all)
      data_type  — raw (default) | hourly | daily
      start      — ISO 8601 start (optional; ignored for public users)
      end        — ISO 8601 end   (optional)
      quality    — GOOD (default) | ALL | SUSPECT | BAD | UNVALIDATED
      columns    — comma-separated column names to include (empty = all)
      output     — csv (default) | json

    Access levels:
      Unauthenticated / PUBLIC  → max 500 rows, last 7 days only, GOOD quality
      RESEARCHER                → max 50 000 rows, any date range
      MAINTAINER / ADMIN        → max 500 000 rows, any date range
    """

    permission_classes = [AllowAny]

    def perform_content_negotiation(self, request, force=False):
        # We return a raw Django HttpResponse/StreamingHttpResponse, not a DRF
        # rendered response.  Bypass DRF's renderer-format filter so that
        # ?output=csv (or the legacy ?format=csv) doesn't cause Http404.
        renderers = self.get_renderers()
        return (renderers[0], renderers[0].media_type)

    def get(self, request):
        import pandas as pd
        import dateutil.parser as _dp

        user = request.user
        max_rows, days_limit = _get_download_limits(user)

        # Accept ?sensors=1,2,3  OR legacy ?sensor=1
        sensors_raw = (
            request.query_params.get("sensors") or request.query_params.get("sensor", "")
        ).strip()
        if not sensors_raw:
            return HttpResponse(
                "sensor or sensors parameter is required.", status=400, content_type="text/plain"
            )

        try:
            sensor_ids = [int(s.strip()) for s in sensors_raw.split(",") if s.strip()]
        except ValueError:
            return HttpResponse("Invalid sensor ID.", status=400, content_type="text/plain")

        sensors_qs = list(Sensor.objects.select_related("site").filter(pk__in=sensor_ids))
        found_ids = {s.pk for s in sensors_qs}
        missing = set(sensor_ids) - found_ids
        if missing:
            return HttpResponse(
                f"Sensor(s) not found: {sorted(missing)}", status=404, content_type="text/plain"
            )
        sensor_map = {s.pk: s for s in sensors_qs}

        # Parse parameters
        pollutant_raw = request.query_params.get("pollutants", request.query_params.get("pollutant", ""))
        pollutants = [p.strip().upper() for p in pollutant_raw.split(",") if p.strip()] or None

        quality  = request.query_params.get("quality", "GOOD").upper()
        data_type = request.query_params.get("data_type", "raw").lower()
        # Support both ?output= (new) and ?format= (legacy, but now safe since we override negotiation)
        output   = (request.query_params.get("output") or request.query_params.get("format", "csv")).lower()
        columns_raw = request.query_params.get("columns", "")
        requested_cols = [c.strip() for c in columns_raw.split(",") if c.strip()]

        quality_flags: tuple | None = None
        if quality != "ALL":
            quality_flags = (quality,)

        # Date bounds
        if days_limit is not None:
            start_ts = timezone.now() - timedelta(days=days_limit)
            end_ts = None
        else:
            start_str = request.query_params.get("start")
            end_str   = request.query_params.get("end")
            try:
                start_ts = _dp.parse(start_str).replace(tzinfo=timezone.utc) if start_str else None
                end_ts   = _dp.parse(end_str).replace(tzinfo=timezone.utc)   if end_str   else None
            except (ValueError, TypeError):
                return HttpResponse("Invalid start or end date.", status=400, content_type="text/plain")

        # ── Build DataFrame by data type ───────────────────────────────────────
        df = pd.DataFrame()

        if data_type == "hourly":
            df = self._export_hourly(sensor_ids, sensor_map, pollutants, start_ts, end_ts,
                                     quality_flags, max_rows)
            if requested_cols and not df.empty:
                keep = [c for c in requested_cols if c in df.columns]
                if keep:
                    df = df[keep]
        elif data_type == "daily":
            df = self._export_daily(sensor_ids, sensor_map, pollutants, start_ts, end_ts, max_rows)
            if requested_cols and not df.empty:
                keep = [c for c in requested_cols if c in df.columns]
                if keep:
                    df = df[keep]
        else:
            # Raw — wide format (one row per timestamp, all pollutants as columns)
            df = self._export_raw_wide(
                sensor_ids, sensor_map, pollutants,
                start_ts, end_ts, quality_flags, max_rows, quality, requested_cols,
            )

        # ── Render output ──────────────────────────────────────────────────────
        sensor_tag = "_".join(str(s) for s in sensor_ids[:3])
        if len(sensor_ids) > 3:
            sensor_tag += f"_and{len(sensor_ids)-3}more"
        filename = f"nepal_aq_{data_type}_{sensor_tag}.csv"

        if output == "json":
            records = df.to_dict(orient="records") if not df.empty else []
            for rec in records:
                for k, v in rec.items():
                    if hasattr(v, "isoformat"):
                        rec[k] = v.isoformat()
            return HttpResponse(
                __import__("json").dumps({"returned": len(records), "data": records}),
                content_type="application/json",
            )

        def _stream_csv(dataframe, fname):
            buf = io.StringIO()
            if not dataframe.empty:
                dataframe.to_csv(buf, index=False)
            else:
                buf.write("# No data found for the selected filters.\n")
            yield buf.getvalue()

        response = StreamingHttpResponse(
            _stream_csv(df, filename), content_type="text/csv; charset=utf-8"
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    # ── Private export helpers ─────────────────────────────────────────────────

    def _export_raw_wide(self, sensor_ids, sensor_map, pollutants, start_ts, end_ts,
                         quality_flags, max_rows, quality, requested_cols):
        """
        Return a wide-format DataFrame: one row per (sensor, timestamp),
        pollutant values spread across columns.  Matches native sensor export format.

        Data source routing (based on AQ_INGESTION settings):
          Recent window (≤ db_raw_recent_days)  → CanonicalReading (Postgres)
          Historical window (older)              → R2 parquet
          db_raw_enabled=True                    → everything from Postgres

        Both sources are merged and deduplicated on (sensor_id, ts) before formatting.
        """
        import pandas as pd
        from apps.ingestion.base import _aq_cfg
        from apps.ingestion.parquet_store import _r2_available

        cfg      = _aq_cfg()
        db_raw   = cfg.get("db_raw_enabled", True)
        raw_days = cfg.get("db_raw_recent_days", 0)

        # ── Determine which time window each source covers ─────────────────────
        #
        #   db_raw=True               → all data in DB, no R2 needed
        #   db_raw=False, days>0      → DB has last `days` days; R2 has everything older
        #   db_raw=False, days=0      → nothing in DB; R2 has everything
        #
        now = timezone.now()
        if db_raw:
            db_cutoff = None          # no lower bound — DB has everything
        elif raw_days > 0:
            db_cutoff = now - timedelta(days=raw_days)
        else:
            db_cutoff = now           # effectively nothing in DB

        frames = []

        # ── Fetch from Postgres (recent / live window) ────────────────────────
        # Only bother if the requested range overlaps with what's in DB.
        db_fetch_start = (
            max(start_ts, db_cutoff) if (start_ts and db_cutoff) else (start_ts or db_cutoff)
        )
        db_fetch_end = end_ts
        if db_cutoff is None or (end_ts is None or end_ts >= db_cutoff):
            db_frame = self._pivot_from_db(
                sensor_ids, pollutants,
                start_ts=db_fetch_start,
                end_ts=db_fetch_end,
                quality=quality,
                max_rows=max_rows,
            )
            if not db_frame.empty:
                frames.append(db_frame)

        # ── Fetch from R2 parquet (historical window) ─────────────────────────
        # Only when: raw mode is disabled, R2 is configured, and the request
        # reaches back further than the DB live-buffer boundary.
        needs_r2 = (
            not db_raw
            and _r2_available()
            and start_ts is not None
            and (db_cutoff is None or start_ts < db_cutoff)
        )
        if needs_r2:
            r2_remaining = max(0, max_rows - sum(len(f) for f in frames))
            r2_frame = self._pivot_from_r2(
                sensor_ids, sensor_map, pollutants,
                start_ts=start_ts,
                end_ts=db_cutoff,   # R2 covers start → DB boundary
                quality=quality,
                max_rows=r2_remaining,
            )
            if not r2_frame.empty:
                frames.append(r2_frame)

        if not frames:
            return pd.DataFrame(columns=_WIDE_COLUMN_ORDER)

        # ── Merge sources, deduplicate on (sensor_id, ts) ─────────────────────
        df_wide = (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["sensor_id", "ts"])
            .sort_values(["sensor_id", "ts"])
            .reset_index(drop=True)
        )

        # ── Sensor metadata columns ────────────────────────────────────────────
        def _sattr(sid, attr, default=""):
            try:
                s = sensor_map.get(int(sid))
            except (TypeError, ValueError):
                return default
            return getattr(s, attr, default) if s else default

        def _site_attr(sid, attr):
            try:
                s = sensor_map.get(int(sid))
            except (TypeError, ValueError):
                return ""
            return getattr(s.site, attr, "") if s and s.site else ""

        df_wide["Timestamp"]     = df_wide["ts"].dt.strftime("%m/%d/%Y %H:%M:%S")
        df_wide["Device ID"]     = df_wide["sensor_id"].map(lambda x: _sattr(x, "serial_number"))
        df_wide["Serial Number"] = df_wide["Device ID"]
        df_wide["Model"]         = df_wide["sensor_id"].map(lambda x: _sattr(x, "model"))
        df_wide["Sub Model"]     = ""
        df_wide["Friendly Name"] = df_wide["sensor_id"].map(lambda x: _sattr(x, "display_name"))
        df_wide["Latitude"]      = df_wide["sensor_id"].map(lambda x: _site_attr(x, "latitude"))
        df_wide["Longitude"]     = df_wide["sensor_id"].map(lambda x: _site_attr(x, "longitude"))
        df_wide["Is Indoor"]     = df_wide["sensor_id"].map(lambda x: _sattr(x, "is_indoor", False))
        df_wide["Is Public"]     = True

        # ── Computed AQI columns ───────────────────────────────────────────────
        df_wide["PM2.5 AQI"] = df_wide["PM2.5"].apply(_compute_pm25_aqi) if "PM2.5" in df_wide.columns else None
        df_wide["PM10 AQI"]  = df_wide["PM10"].apply(_compute_pm10_aqi)  if "PM10"  in df_wide.columns else None

        # ── Fill calibration / status columns (not tracked per-reading) ────────
        for col in _WIDE_COLUMN_ORDER:
            if col not in df_wide.columns:
                df_wide[col] = "<nil>" if col in _OFFSET_COLS else ""

        for col in _OFFSET_COLS:
            if col in df_wide.columns:
                df_wide[col] = df_wide[col].where(df_wide[col] != "", "<nil>")

        # ── Reorder and filter columns ─────────────────────────────────────────
        if requested_cols:
            cols_to_use = [c for c in _WIDE_COLUMN_ORDER if c in requested_cols and c in df_wide.columns]
            if not cols_to_use:
                cols_to_use = [c for c in _WIDE_COLUMN_ORDER if c in df_wide.columns]
        else:
            cols_to_use = [c for c in _WIDE_COLUMN_ORDER if c in df_wide.columns]

        return df_wide[cols_to_use]

    def _pivot_from_db(self, sensor_ids, pollutants, start_ts, end_ts, quality, max_rows):
        """
        Fetch from CanonicalReading (long format) and pivot to wide.

        Returns a DataFrame with columns: sensor_id (int), ts (datetime),
        plus display-name pollutant columns (PM2.5, PM10, …).
        """
        import pandas as pd

        qs = CanonicalReading.objects.filter(
            sensor_id__in=sensor_ids, is_duplicate=False,
        ).order_by("original_ts")
        if pollutants:
            qs = qs.filter(pollutant__in=pollutants)
        if quality != "ALL":
            qs = qs.filter(quality_flag=quality)
        if start_ts:
            qs = qs.filter(original_ts__gte=start_ts)
        if end_ts:
            qs = qs.filter(original_ts__lte=end_ts)

        rows = list(qs[:max_rows].values("sensor_id", "original_ts", "pollutant", "raw_value"))
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df = df.rename(columns={"original_ts": "ts", "raw_value": "value"})

        wide = df.pivot_table(
            index=["sensor_id", "ts"],
            columns="pollutant",
            values="value",
            aggfunc="first",
        ).reset_index()
        wide.columns.name = None
        wide["sensor_id"] = wide["sensor_id"].astype(object).apply(
            lambda x: int(x) if x is not None and x == x else None
        )
        wide.rename(columns=_WIDE_POLLUTANT_MAP, inplace=True)
        return wide

    def _pivot_from_r2(self, sensor_ids, sensor_map, pollutants_filter,
                       start_ts, end_ts, quality, max_rows):
        """
        Read R2 parquet for each sensor in the date range, apply quality and
        pollutant filters, and return a wide DataFrame matching the DB pivot format.

        Quality filtering is applied per-pollutant column: values whose quality
        flag does not match the requested quality are set to NaN (not dropped),
        preserving the row for other pollutants that do pass.
        """
        import pandas as pd
        from apps.ingestion.parquet_store import read as parquet_read, POLLUTANTS as ALL_POLLS

        frames = []
        remaining = max(0, max_rows)

        for sensor_id in sensor_ids:
            if remaining <= 0:
                break
            sensor = sensor_map.get(sensor_id)
            if sensor is None:
                continue

            df = parquet_read(sensor.serial_number, start_ts, end_ts)
            if df.empty:
                continue

            df = df.rename(columns={"ts_utc": "ts"})

            # Apply per-pollutant quality filter: null out values that don't pass.
            # This mirrors how the DB query filters individual (ts, pollutant) rows.
            if quality != "ALL":
                for poll_code in ALL_POLLS:
                    if poll_code in df.columns and f"qf_{poll_code}" in df.columns:
                        bad = df[f"qf_{poll_code}"] != quality
                        df.loc[bad, poll_code] = float("nan")

            # Drop qf_ columns — not included in export output
            qf_cols = [c for c in df.columns if c.startswith("qf_")]
            df = df.drop(columns=qf_cols + ["source_type", "is_indoor"], errors="ignore")

            # Keep only requested pollutant columns (if filter is active)
            active_polls = pollutants_filter if pollutants_filter else ALL_POLLS
            poll_cols = [p for p in active_polls if p in df.columns]
            if not poll_cols:
                continue

            df = df[["ts"] + poll_cols].copy()

            # Drop rows where all pollutant values are NaN after quality filtering
            df = df.dropna(subset=poll_cols, how="all")
            if df.empty:
                continue

            df["sensor_id"] = sensor_id
            df.rename(columns=_WIDE_POLLUTANT_MAP, inplace=True)

            df = df.head(remaining)
            remaining -= len(df)
            frames.append(df)

        if not frames:
            return pd.DataFrame()

        return pd.concat(frames, ignore_index=True)

    def _export_hourly(self, sensor_ids, sensor_map, pollutants, start_ts, end_ts, quality_flags, max_rows):
        import pandas as pd
        from apps.readings.models import HourlyAggregate
        qs = HourlyAggregate.objects.filter(sensor_id__in=sensor_ids)
        if pollutants:
            qs = qs.filter(pollutant__in=pollutants)
        if start_ts:
            qs = qs.filter(hour__gte=start_ts)
        if end_ts:
            qs = qs.filter(hour__lte=end_ts)
        rows = list(qs.order_by("hour", "sensor_id", "pollutant")[:max_rows].values(
            "sensor_id", "hour", "pollutant", "is_indoor",
            "count", "mean", "std", "min_value", "max_value", "completeness",
        ))
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df = df.rename(columns={"hour": "hour_utc"})
        df["sensor_name"] = df["sensor_id"].map(lambda x: sensor_map[x].display_name if x in sensor_map else "")
        df["site"]        = df["sensor_id"].map(lambda x: (sensor_map[x].site.name if sensor_map[x].site else "") if x in sensor_map else "")
        return df[["hour_utc", "sensor_id", "sensor_name", "site", "is_indoor",
                   "pollutant", "count", "mean", "std", "min_value", "max_value", "completeness"]]

    def _export_daily(self, sensor_ids, sensor_map, pollutants, start_ts, end_ts, max_rows):
        import pandas as pd
        from apps.readings.models import DailyAggregate
        qs = DailyAggregate.objects.filter(sensor_id__in=sensor_ids)
        if pollutants:
            qs = qs.filter(pollutant__in=pollutants)
        if start_ts:
            qs = qs.filter(date__gte=start_ts.date() if hasattr(start_ts, "date") else start_ts)
        if end_ts:
            qs = qs.filter(date__lte=end_ts.date() if hasattr(end_ts, "date") else end_ts)
        rows = list(qs.order_by("date", "sensor_id", "pollutant")[:max_rows].values(
            "sensor_id", "date", "pollutant", "is_indoor",
            "count", "mean", "std", "min_value", "max_value",
            "p25", "median", "p75", "p95", "completeness",
        ))
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["sensor_name"] = df["sensor_id"].map(lambda x: sensor_map[x].display_name if x in sensor_map else "")
        df["site"]        = df["sensor_id"].map(lambda x: (sensor_map[x].site.name if sensor_map[x].site else "") if x in sensor_map else "")
        return df[["date", "sensor_id", "sensor_name", "site", "is_indoor", "pollutant",
                   "count", "mean", "std", "min_value", "max_value",
                   "p25", "median", "p75", "p95", "completeness"]]
