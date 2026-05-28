"""
API views for the Nepal Air Quality Dashboard.

Endpoints:
  POST /api/v1/readings/             — submit readings (sensor API key)
  GET  /api/v1/readings/             — query readings (researcher+ auth)
  GET  /api/v1/sensors/              — list sensors (public)
  GET  /api/v1/sensors/{id}/         — sensor detail (public)
  POST /api/v1/sensors/register/     — register new sensor (sensor API key)
  GET  /api/v1/sites/                — list sites (public)
  GET  /api/v1/charts/sensor/{id}/timeseries/   — chart data
  GET  /api/v1/charts/sensor/{id}/completeness/ — completeness chart data
  GET  /api/v1/charts/national/summary/         — national summary chart
"""
import logging
from datetime import datetime, timedelta

import pytz
from django.db.models import Avg, Count, Max, Min
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

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
