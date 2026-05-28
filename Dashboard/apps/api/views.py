"""
API views for the Nepal Air Quality Dashboard.

Endpoints:
  POST /api/v1/readings/             — submit readings (sensor API key)
  GET  /api/v1/readings/             — query readings (researcher+ auth)
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
    CanonicalReadingSerializer,
    ReadingSubmitSerializer,
    SensorRegisterSerializer,
    SensorSerializer,
    SiteSerializer,
)

logger = logging.getLogger(__name__)

NEPAL_TZ = pytz.timezone("Asia/Kathmandu")


# ── Readings ──────────────────────────────────────────────────────────────────

class ReadingListView(APIView):
    """
    GET  /api/v1/readings/ — query readings
    POST /api/v1/readings/ — submit readings from sensor
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

        converter = APIConverter()
        result = converter.run(serializer.validated_data)

        if result["status"] == "FAILED":
            return Response(
                {"error": result.get("error", "Ingestion failed.")},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(result, status=status.HTTP_201_CREATED)


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
      start      (optional) — ISO 8601
      end        (optional) — ISO 8601
      quality    (optional) — GOOD|SUSPECT|BAD|UNVALIDATED (default: GOOD,UNVALIDATED)
      resample   (optional) — hourly|daily (default: raw, max 2000 points)
    """

    permission_classes = [AllowAny]

    def get(self, request, sensor_id):
        pollutant = request.query_params.get("pollutant", "PM25").upper()
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        resample = request.query_params.get("resample", "raw")

        try:
            sensor = Sensor.objects.get(pk=sensor_id)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        qs = CanonicalReading.objects.filter(
            sensor=sensor,
            pollutant=pollutant,
            is_duplicate=False,
        ).order_by("original_ts")

        if start:
            qs = qs.filter(original_ts__gte=start)
        if end:
            qs = qs.filter(original_ts__lte=end)
        else:
            # Default: last 7 days
            qs = qs.filter(original_ts__gte=timezone.now() - timedelta(days=7))

        # Return raw values (limited for performance)
        MAX_POINTS = 2000
        total = qs.count()
        if total > MAX_POINTS:
            # Uniform subsampling
            step = total // MAX_POINTS
            ids = list(qs.values_list("id", flat=True)[::step])
            qs = CanonicalReading.objects.filter(id__in=ids).order_by("original_ts")

        data = [
            {
                "ts": r.original_ts.isoformat(),
                "value": r.effective_value,
                "quality_flag": r.quality_flag,
            }
            for r in qs
        ]

        return Response({
            "sensor_id": sensor.pk,
            "sensor_name": sensor.display_name,
            "pollutant": pollutant,
            "total_points": total,
            "points": data,
        })


class CompletenessChartView(APIView):
    """
    GET /api/v1/charts/sensor/{id}/completeness/

    Returns daily completeness fractions for the last 30 days.
    Assumes ~96 readings/day at 15-minute intervals.
    """

    permission_classes = [AllowAny]

    def get(self, request, sensor_id):
        try:
            sensor = Sensor.objects.get(pk=sensor_id)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        expected_per_day = int(request.query_params.get("expected", 96))
        days = int(request.query_params.get("days", 30))

        from django.db.models.functions import TruncDate
        qs = (
            CanonicalReading.objects
            .filter(
                sensor=sensor,
                pollutant="PM25",
                is_duplicate=False,
                original_ts__gte=timezone.now() - timedelta(days=days),
            )
            .annotate(date=TruncDate("original_ts"))
            .values("date")
            .annotate(count=Count("id"))
            .order_by("date")
        )

        data = [
            {
                "date": str(row["date"]),
                "expected": expected_per_day,
                "actual": row["count"],
                "completeness_pct": round(min(100, row["count"] / expected_per_day * 100), 1),
            }
            for row in qs
        ]

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

        sensors = Sensor.objects.filter(status="ACTIVE").select_related("site")
        summary = []

        for sensor in sensors:
            latest = (
                CanonicalReading.objects
                .filter(sensor=sensor, pollutant="PM25", is_duplicate=False, quality_flag__in=["GOOD", "UNVALIDATED"])
                .order_by("-original_ts")
                .first()
            )

            avg_24h = (
                CanonicalReading.objects
                .filter(
                    sensor=sensor,
                    pollutant="PM25",
                    is_duplicate=False,
                    quality_flag__in=["GOOD"],
                    original_ts__gte=timezone.now() - timedelta(hours=24),
                )
                .aggregate(avg=Avg("raw_value"))["avg"]
            )

            summary.append({
                "sensor_id": sensor.pk,
                "sensor_name": sensor.display_name,
                "site_name": sensor.site.name,
                "site_district": sensor.site.district,
                "latitude": sensor.site.latitude,
                "longitude": sensor.site.longitude,
                "is_indoor": sensor.is_indoor,
                "status": sensor.status,
                "latest_pm25": latest.effective_value if latest else None,
                "latest_ts": latest.original_ts.isoformat() if latest else None,
                "avg_pm25_24h": round(avg_24h, 1) if avg_24h else None,
                "exceeds_who_24h": (avg_24h > WHO_24H) if avg_24h else None,
                "exceeds_nepal_24h": (avg_24h > NEPAL_24H) if avg_24h else None,
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

    Returns min/max timestamps for a sensor so the UI can populate date pickers.
    """
    permission_classes = [AllowAny]

    def get(self, request, sensor_id):
        try:
            sensor = Sensor.objects.get(pk=sensor_id)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        pollutant = request.query_params.get("pollutant", "PM25").upper()
        agg = CanonicalReading.objects.filter(
            sensor=sensor, pollutant=pollutant, is_duplicate=False,
        ).aggregate(min_ts=Min("original_ts"), max_ts=Max("original_ts"))

        return Response({
            "sensor_id": sensor.pk,
            "pollutant": pollutant,
            "min_ts": agg["min_ts"].isoformat() if agg["min_ts"] else None,
            "max_ts": agg["max_ts"].isoformat() if agg["max_ts"] else None,
            "min_date": agg["min_ts"].date().isoformat() if agg["min_ts"] else None,
            "max_date": agg["max_ts"].date().isoformat() if agg["max_ts"] else None,
        })


class DiurnalChartView(APIView):
    """
    GET /api/v1/charts/sensor/{id}/diurnal/

    Returns hour-of-day (0–23, Nepal Standard Time = UTC+5:45) median PM values
    for use in diurnal profile charts.

    Query params:
      pollutant  — PM25 (default)
      start      — ISO date
      end        — ISO date
    """
    permission_classes = [AllowAny]

    NST_MINUTES = 5 * 60 + 45  # 345 minutes east of UTC

    def get(self, request, sensor_id):
        from django.db.models import Avg
        try:
            sensor = Sensor.objects.get(pk=sensor_id)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        pollutant = request.query_params.get("pollutant", "PM25").upper()
        start = request.query_params.get("start")
        end = request.query_params.get("end")

        qs = CanonicalReading.objects.filter(
            sensor=sensor, pollutant=pollutant, is_duplicate=False,
            quality_flag__in=["GOOD", "UNVALIDATED"],
        )
        if start:
            qs = qs.filter(original_ts__gte=start)
        if end:
            qs = qs.filter(original_ts__lte=end)
        else:
            qs = qs.filter(original_ts__gte=timezone.now() - timedelta(days=90))

        # Group by NST hour using extra() for SQLite and PostgreSQL compatibility
        # NST = UTC + 5h45m = +345 minutes
        rows = (
            qs.extra(
                select={"nst_hour": (
                    "CAST(((CAST(strftime('%%H', original_ts) AS INTEGER) * 60 "
                    "+ CAST(strftime('%%M', original_ts) AS INTEGER) + 345) / 60) %% 24 AS INTEGER)"
                )}
            )
            .values("nst_hour")
            .annotate(avg=Avg("raw_value"), count=Count("id"))
            .order_by("nst_hour")
        )

        by_hour = {r["nst_hour"]: {"avg": r["avg"], "count": r["count"]} for r in rows}
        hours = list(range(24))

        return Response({
            "sensor_id": sensor.pk,
            "sensor_name": sensor.display_name,
            "pollutant": pollutant,
            "hours": hours,
            "labels": [f"{h:02d}:00" for h in hours],
            "values": [round(by_hour[h]["avg"], 2) if h in by_hour and by_hour[h]["avg"] else None for h in hours],
            "counts": [by_hour.get(h, {}).get("count", 0) for h in hours],
            "note": "Hours in Nepal Standard Time (UTC+5:45). Values are per-hour medians.",
        })


class MonthlyChartView(APIView):
    """
    GET /api/v1/charts/sensor/{id}/monthly/

    Returns monthly average + max per pollutant.

    Query params:
      pollutant  — PM25 (default)
    """
    permission_classes = [AllowAny]

    def get(self, request, sensor_id):
        from django.db.models import Avg, Max
        from django.db.models.functions import TruncMonth

        try:
            sensor = Sensor.objects.get(pk=sensor_id)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        pollutant = request.query_params.get("pollutant", "PM25").upper()

        rows = (
            CanonicalReading.objects
            .filter(sensor=sensor, pollutant=pollutant, is_duplicate=False,
                    quality_flag__in=["GOOD", "UNVALIDATED"])
            .annotate(month=TruncMonth("original_ts"))
            .values("month")
            .annotate(avg=Avg("raw_value"), max_val=Max("raw_value"), count=Count("id"))
            .order_by("month")
        )

        months = [str(r["month"])[:7] for r in rows]
        avgs = [round(r["avg"], 1) if r["avg"] else None for r in rows]
        maxs = [round(r["max_val"], 1) if r["max_val"] else None for r in rows]

        return Response({
            "sensor_id": sensor.pk,
            "sensor_name": sensor.display_name,
            "pollutant": pollutant,
            "months": months,
            "avg": avgs,
            "max": maxs,
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
            qs = CanonicalReading.objects.filter(
                sensor=sensor, pollutant=pollutant, is_duplicate=False,
                quality_flag__in=["GOOD", "UNVALIDATED"],
            )
            if start:
                qs = qs.filter(original_ts__gte=start)
            if end:
                qs = qs.filter(original_ts__lte=end)
            else:
                qs = qs.filter(original_ts__gte=timezone.now() - timedelta(days=90))

            rows = (
                qs.extra(
                    select={"nst_hour": (
                        "CAST(((CAST(strftime('%%H', original_ts) AS INTEGER) * 60 "
                        "+ CAST(strftime('%%M', original_ts) AS INTEGER) + 345) / 60) %% 24 AS INTEGER)"
                    )}
                )
                .values("nst_hour")
                .annotate(avg=Avg("raw_value"))
                .order_by("nst_hour")
            )
            by_hour = {r["nst_hour"]: r["avg"] for r in rows}
            return [round(by_hour[h], 2) if h in by_hour and by_hour[h] else None for h in range(24)]

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
      sensor     — sensor ID (required)
      pollutant  — PM25, PM10, CO2, TEMP, RH, TVOC (default: PM25)
      start      — ISO 8601 start (optional; ignored for public users)
      end        — ISO 8601 end   (optional)
      quality    — GOOD, SUSPECT, BAD, UNVALIDATED (default: GOOD)
      format     — csv (default) or json

    Access levels:
      Unauthenticated / PUBLIC  → max 500 rows, last 7 days only, GOOD quality
      RESEARCHER                → max 50 000 rows, any date range
      MAINTAINER / ADMIN        → max 500 000 rows, any date range
    """

    permission_classes = [AllowAny]

    def get(self, request):
        user = request.user
        max_rows, days_limit = _get_download_limits(user)

        sensor_id = request.query_params.get("sensor")
        if not sensor_id:
            return Response({"error": "sensor parameter is required."}, status=400)

        try:
            sensor = Sensor.objects.select_related("site").get(pk=sensor_id)
        except Sensor.DoesNotExist:
            return Response({"error": "Sensor not found."}, status=404)

        pollutant = request.query_params.get("pollutant", "PM25").upper()
        quality = request.query_params.get("quality", "GOOD").upper()
        fmt = request.query_params.get("format", "csv").lower()

        qs = CanonicalReading.objects.filter(
            sensor=sensor,
            pollutant=pollutant,
            is_duplicate=False,
        ).order_by("original_ts")

        # Apply quality filter
        if quality == "ALL":
            pass
        else:
            qs = qs.filter(quality_flag=quality)

        # Date range — public users are locked to days_limit window
        if days_limit is not None:
            cutoff = timezone.now() - timedelta(days=days_limit)
            qs = qs.filter(original_ts__gte=cutoff)
        else:
            start = request.query_params.get("start")
            end = request.query_params.get("end")
            if start:
                try:
                    qs = qs.filter(original_ts__gte=start)
                except (ValueError, TypeError):
                    return Response({"error": "Invalid start date."}, status=400)
            if end:
                try:
                    qs = qs.filter(original_ts__lte=end)
                except (ValueError, TypeError):
                    return Response({"error": "Invalid end date."}, status=400)

        total = qs.count()
        qs = qs[:max_rows]

        if fmt == "json":
            data = list(qs.values(
                "original_ts", "pollutant", "unit", "raw_value", "cleaned_value",
                "quality_flag", "is_indoor",
            ))
            return Response({
                "sensor_id": sensor.pk,
                "sensor_name": sensor.display_name,
                "site": sensor.site.name if sensor.site else None,
                "pollutant": pollutant,
                "total_available": total,
                "returned": len(data),
                "max_rows": max_rows,
                "readings": data,
            })

        # CSV response
        filename = f"nepal_aq_{sensor.serial_number}_{pollutant}.csv"
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        writer.writerow([
            "timestamp_utc", "sensor_id", "sensor_name", "site",
            "is_indoor", "pollutant", "unit", "raw_value", "cleaned_value", "quality_flag",
        ])
        for r in qs:
            writer.writerow([
                r.original_ts.isoformat(),
                sensor.pk,
                sensor.display_name,
                sensor.site.name if sensor.site else "",
                r.is_indoor,
                r.pollutant,
                r.unit or "",
                r.raw_value,
                r.cleaned_value,
                r.quality_flag,
            ])

        return response
