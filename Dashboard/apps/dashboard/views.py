"""
Views for the Nepal Air Observatory public portal.

URL namespace: observatory

Public views (no login required):
  HomepageView         GET /
  MapView              GET /map/
  SensorListView       GET /sensors/
  SensorDetailView     GET /sensors/<pk>/
  LocationView         GET /locations/<pk>/
  IndoorOutdoorView    GET /locations/<pk>/indoor-outdoor/
  AnalysisView         GET /analysis/
  DataAccessView       GET /data/
  MethodsView          GET /methods/
  AboutView            GET /about/

Portal views (login required, role-gated):
  PortalIndexView            GET  /portal/
  IngestionLogsView          GET  /portal/ingestion-logs/
  UploadCSVView              GET/POST /portal/upload-csv/
  APIKeysView                GET/POST /portal/api-keys/
  RunIngestionView           GET  /portal/run-ingestion/
  SensorRegisterPortalView   GET/POST /portal/sensors/register/
  SensorListPortalView       GET  /portal/sensors/
  AggregationView            GET/POST /portal/aggregate/
"""
import json
import logging
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Avg, Count, Max, Min
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.readings.models import CanonicalReading, IngestionLog
from apps.sensors.models import Sensor, Site

logger = logging.getLogger(__name__)

WHO_24H          = 15.0
WHO_ANNUAL       = 5.0
NEPAL_NAAQS_24H  = 40.0

PM25_SCALE = [
    ("Good",                         "bg-green-500",   "0–12"),
    ("Moderate",                     "bg-yellow-400",  "12–35.4"),
    ("Unhealthy for Sensitive Groups","bg-orange-400", "35.4–55.4"),
    ("Unhealthy",                    "bg-red-500",     "55.4–150.4"),
    ("Very Unhealthy",               "bg-purple-600",  "150.4–250.4"),
    ("Hazardous",                    "bg-red-900",     "> 250.4"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _latest_pm25(sensors):
    """Return dict {sensor.pk: {"raw_value", "quality_flag", "original_ts"}} for all sensors."""
    result = {}
    for s in sensors:
        latest = (
            CanonicalReading.objects
            .filter(sensor=s, pollutant="PM25", is_duplicate=False)
            .order_by("-original_ts")
            .values("raw_value", "quality_flag", "original_ts")
            .first()
        )
        result[s.pk] = latest
    return result


def _pm25_category(value):
    """Return (label, Tailwind colour class)."""
    if value is None:
        return "No data", "text-slate-400"
    if value <= 12:
        return "Good", "text-green-600"
    if value <= 35.4:
        return "Moderate", "text-yellow-500"
    if value <= 55.4:
        return "Unhealthy for Sensitive Groups", "text-orange-500"
    if value <= 150.4:
        return "Unhealthy", "text-red-600"
    if value <= 250.4:
        return "Very Unhealthy", "text-purple-700"
    return "Hazardous", "text-red-900"


def _sensor_geojson(sensors, latest):
    """Produce JSON-serialisable list for Leaflet map."""
    out = []
    for s in sensors:
        pm25_data = latest.get(s.pk)
        val = pm25_data["raw_value"] if pm25_data else None
        out.append({
            "id":      s.pk,
            "name":    s.friendly_name,
            "site":    s.site.name if s.site else "",
            "lat":     float(s.site.latitude)  if s.site and s.site.latitude  else None,
            "lon":     float(s.site.longitude) if s.site and s.site.longitude else None,
            "indoor":  s.is_indoor,
            "status":  s.status,
            "pm25":    round(val, 1) if val is not None else None,
        })
    return json.dumps(out)


def _sensors_with_data(qs, latest):
    result = []
    for s in qs:
        pm25_data = latest.get(s.pk)
        value = pm25_data["raw_value"] if pm25_data else None
        category, colour = _pm25_category(value)
        result.append({
            "sensor": s,
            "pm25_value":    round(value, 1) if value is not None else None,
            "pm25_category": category,
            "pm25_colour":   colour,
            "last_seen":     pm25_data["original_ts"] if pm25_data else None,
        })
    return result


# ── Data dictionary for Data Access page ──────────────────────────────────────

DATA_DICTIONARY = [
    {"field": "original_ts",      "type": "datetime", "description": "Timestamp as reported by the sensor (timezone-aware, Asia/Kathmandu)."},
    {"field": "received_ts",      "type": "datetime", "description": "When this record was ingested into the observatory system."},
    {"field": "timezone",         "type": "string",   "description": "IANA timezone string for the sensor location (e.g. Asia/Kathmandu)."},
    {"field": "interval_seconds", "type": "integer",  "description": "Nominal reporting interval in seconds (e.g. 60 for 1-min data). Null if unknown."},
    {"field": "pollutant",        "type": "enum",     "description": "PM1, PM25, PM4, PM10, CO2, TVOC, TEMP, RH, BARO, NC05, NC1, NC25."},
    {"field": "unit",             "type": "string",   "description": "Unit for the pollutant (e.g. µg/m³, ppm, °C, %, inHg, #/cm³)."},
    {"field": "raw_value",        "type": "float",    "description": "Value exactly as received from the sensor. NEVER overwritten. Null if sensor did not report."},
    {"field": "cleaned_value",    "type": "float",    "description": "QC-corrected or calibrated value. Null until QC is applied. raw_value is always preserved."},
    {"field": "sensor_id",        "type": "integer",  "description": "FK to Sensor table. Identifies the specific physical device."},
    {"field": "site_id",          "type": "integer",  "description": "FK to Site table. Deployment location."},
    {"field": "is_indoor",        "type": "boolean",  "description": "True if sensor is deployed indoors."},
    {"field": "source_type",      "type": "enum",     "description": "LIVE_API, CSV_UPLOAD, BATCH_UPLOAD, MANUAL, EXTERNAL."},
    {"field": "dataset_id",       "type": "integer",  "description": "FK to Dataset (a logical grouping of readings from one ingestion run). Null for live API submissions."},
    {"field": "quality_flag",     "type": "enum",     "description": "GOOD, SUSPECT, BAD, MISSING, UNVALIDATED. See Methods page for definitions."},
    {"field": "flag_reason",      "type": "string",   "description": "Human-readable reason for SUSPECT or BAD flag."},
    {"field": "is_duplicate",     "type": "boolean",  "description": "True if this reading is a duplicate of an earlier record. Duplicates are kept for audit; never deleted."},
]

API_ENDPOINTS = [
    {
        "method": "GET", "path": "/api/v1/sensors/",
        "description": "List all sensors",
        "detail": "Returns a paginated list of all registered sensors with metadata.",
        "params": [
            {"name": "status",   "type": "string", "description": "Filter by ACTIVE, OFFLINE, MAINTENANCE, DECOMMISSIONED"},
            {"name": "indoor",   "type": "bool",   "description": "true or false to filter by indoor/outdoor"},
        ],
        "example": "GET /api/v1/sensors/?status=ACTIVE",
    },
    {
        "method": "GET", "path": "/api/v1/sensors/{id}/",
        "description": "Sensor detail",
        "detail": "Returns full metadata for a single sensor including site information.",
        "params": [],
        "example": "GET /api/v1/sensors/1/",
    },
    {
        "method": "GET", "path": "/api/v1/readings/",
        "description": "Query readings (researcher access required)",
        "detail": "Returns paginated readings filtered by sensor, pollutant, date range, and quality flag. Requires researcher role or API key.",
        "params": [
            {"name": "sensor",    "type": "int",    "description": "Sensor ID"},
            {"name": "pollutant", "type": "string", "description": "PM25, PM10, CO2, TEMP, etc."},
            {"name": "start",     "type": "datetime","description": "ISO 8601 start timestamp"},
            {"name": "end",       "type": "datetime","description": "ISO 8601 end timestamp"},
            {"name": "quality",   "type": "string", "description": "GOOD, SUSPECT, BAD, MISSING, UNVALIDATED"},
            {"name": "format",    "type": "string", "description": "json (default) or csv"},
        ],
        "example": "GET /api/v1/readings/?sensor=1&pollutant=PM25&quality=GOOD&start=2025-11-01T00:00:00&format=csv",
    },
    {
        "method": "POST", "path": "/api/v1/readings/",
        "description": "Submit readings (API key required)",
        "detail": "Submit new sensor readings. Requires an X-API-Key header. Accepts a JSON array of reading objects.",
        "params": [
            {"name": "X-API-Key", "type": "header", "description": "Your sensor API key (obtain from portal)"},
        ],
        "example": 'POST /api/v1/readings/\nX-API-Key: your-key-here\nContent-Type: application/json\n\n[{"serial_number":"81432434001","ts":"2025-12-01T06:00:00Z","pm25":35.2,"pm10":42.1,"temp_c":18.5,"rh":72.0}]',
    },
    {
        "method": "GET", "path": "/api/v1/export/",
        "description": "CSV / JSON export (access-controlled)",
        "detail": "Generates a downloadable CSV or JSON file. Unauthenticated / PUBLIC users get the last 7 days, up to 500 rows. RESEARCHER accounts get up to 50,000 rows over any date range. ADMIN/MAINTAINER get up to 500,000 rows.",
        "params": [
            {"name": "sensor",    "type": "int",      "description": "Sensor ID (required)"},
            {"name": "pollutant", "type": "string",   "description": "PM25, PM10, PM1, CO2, TVOC, TEMP, RH (default: PM25)"},
            {"name": "quality",   "type": "string",   "description": "GOOD, UNVALIDATED, ALL (default: GOOD)"},
            {"name": "start",     "type": "datetime", "description": "ISO 8601 start — researcher+ only"},
            {"name": "end",       "type": "datetime", "description": "ISO 8601 end"},
            {"name": "format",    "type": "string",   "description": "csv (default) or json"},
        ],
        "example": "GET /api/v1/export/?sensor=1&pollutant=PM25&quality=GOOD&format=csv",
    },
    {
        "method": "GET", "path": "/api/v1/charts/sensor/{id}/timeseries/",
        "description": "Time-series chart data",
        "detail": "Returns Chart.js-compatible {x, y} data points for a sensor/pollutant combination.",
        "params": [
            {"name": "pollutant", "type": "string",   "description": "Pollutant code (default PM25)"},
            {"name": "start",     "type": "datetime", "description": "ISO 8601 start"},
            {"name": "end",       "type": "datetime", "description": "ISO 8601 end"},
        ],
        "example": "GET /api/v1/charts/sensor/1/timeseries/?pollutant=PM25&start=2025-12-01&end=2026-01-01",
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC VIEWS
# ══════════════════════════════════════════════════════════════════════════════

class HomepageView(TemplateView):
    template_name = "homepage.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sensors = list(Sensor.objects.select_related("site").all())
        sites   = list(Site.objects.all())
        latest  = _latest_pm25(sensors)

        active_count  = sum(1 for s in sensors if s.status == "ACTIVE")
        offline_count = sum(1 for s in sensors if s.status == "OFFLINE")
        readings_today = CanonicalReading.objects.filter(
            original_ts__gte=timezone.now() - timedelta(hours=24),
            is_duplicate=False,
        ).count()

        # Highest PM2.5 in last 24h
        pm25_values = [
            (s, latest[s.pk]["raw_value"])
            for s in sensors
            if latest.get(s.pk) and latest[s.pk].get("raw_value") is not None
        ]
        pm25_values.sort(key=lambda x: x[1], reverse=True)
        highest_pm25        = round(pm25_values[0][1], 1) if pm25_values else None
        highest_pm25_sensor = pm25_values[0][0] if pm25_values else None

        total_reporting   = len(pm25_values)
        stations_above_who = sum(1 for _, v in pm25_values if v > WHO_24H)

        # Site cards
        site_cards = []
        for site in sites:
            site_sensors = [s for s in sensors if s.site_id == site.pk]
            active_sensors = sum(1 for s in site_sensors if s.status == "ACTIVE")
            site_pm25 = [
                latest[s.pk]["raw_value"]
                for s in site_sensors
                if latest.get(s.pk) and latest[s.pk].get("raw_value") is not None
            ]
            site_cards.append({
                "site":            site,
                "sensor_count":    len(site_sensors),
                "active_sensors":  active_sensors,
                "latest_pm25":     round(max(site_pm25), 1) if site_pm25 else None,
                "has_indoor_outdoor": (
                    any(s.is_indoor for s in site_sensors) and
                    any(not s.is_indoor for s in site_sensors)
                ),
            })

        # Last update
        last_update = None
        all_latest = [v["original_ts"] for v in latest.values() if v]
        if all_latest:
            last_update = max(all_latest)

        ctx.update({
            "active_count":         active_count,
            "offline_count":        offline_count,
            "readings_today":       f"{readings_today:,}",
            "highest_pm25":         highest_pm25,
            "highest_pm25_sensor":  highest_pm25_sensor,
            "total_reporting":      total_reporting,
            "stations_above_who":   stations_above_who,
            "site_cards":           site_cards,
            "sensor_geojson":       _sensor_geojson(sensors, latest),
            "pm25_scale":           PM25_SCALE,
            "last_update":          last_update,
        })
        return ctx


class MapView(TemplateView):
    template_name = "pages/map.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sensors = list(Sensor.objects.select_related("site").all())
        latest  = _latest_pm25(sensors)
        ctx.update({
            "sensors_with_data": _sensors_with_data(sensors, latest),
            "sensor_geojson":    _sensor_geojson(sensors, latest),
            "pm25_scale":        PM25_SCALE,
        })
        return ctx


class SensorListView(TemplateView):
    template_name = "sensors/list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = Sensor.objects.select_related("site").order_by("site__name", "serial_number")

        status = self.request.GET.get("status", "")
        stype  = self.request.GET.get("type", "")
        if status:
            qs = qs.filter(status=status)
        if stype == "indoor":
            qs = qs.filter(is_indoor=True)
        elif stype == "outdoor":
            qs = qs.filter(is_indoor=False)

        sensors = list(qs)
        latest  = _latest_pm25(sensors)
        ctx.update({
            "sensors_with_data": _sensors_with_data(sensors, latest),
            "status_choices":    Sensor.Status.choices,
            "selected_status":   status,
            "selected_type":     stype,
        })
        return ctx


class SensorDetailView(TemplateView):
    template_name = "sensors/detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sensor = get_object_or_404(
            Sensor.objects.select_related("site").prefetch_related(
                "maintenance_logs", "calibration_records"
            ),
            pk=self.kwargs["pk"],
        )

        stats_24h = CanonicalReading.objects.filter(
            sensor=sensor, pollutant="PM25", is_duplicate=False,
            quality_flag__in=["GOOD", "UNVALIDATED"],
            original_ts__gte=timezone.now() - timedelta(hours=24),
        ).aggregate(avg=Avg("raw_value"), max_val=Max("raw_value"),
                    min_val=Min("raw_value"), count=Count("id"))

        pm25_avg  = stats_24h.get("avg")
        category, colour = _pm25_category(pm25_avg)

        companion = None
        if sensor.site:
            companion = (
                Sensor.objects
                .filter(site=sensor.site, is_indoor=not sensor.is_indoor)
                .exclude(pk=sensor.pk)
                .first()
            )

        # Last data date and 90-day availability grid from DailyAggregate
        from apps.readings.models import DailyAggregate
        import datetime as _dt

        last_agg = (
            DailyAggregate.objects
            .filter(sensor=sensor, pollutant="PM25")
            .order_by("-date")
            .values("date", "mean", "count")
            .first()
        )
        last_data_date = last_agg["date"] if last_agg else None
        last_data_mean = round(float(last_agg["mean"]), 1) if last_agg and last_agg["mean"] else None

        # Build a set of dates that have data in the last 90 days
        cutoff_90 = timezone.now().date() - _dt.timedelta(days=89)
        avail_dates = set(
            DailyAggregate.objects
            .filter(sensor=sensor, pollutant="PM25", date__gte=cutoff_90)
            .values_list("date", flat=True)
        )
        # Also include days from CanonicalReading for very recent data not yet aggregated
        recent_cr_dates = set(
            CanonicalReading.objects
            .filter(sensor=sensor, pollutant="PM25",
                    original_ts__gte=timezone.now() - timedelta(days=3))
            .dates("original_ts", "day")
        )
        avail_dates |= recent_cr_dates

        ctx.update({
            "sensor":           sensor,
            "stats_24h":        stats_24h,
            "pm25_avg_24h":     round(pm25_avg, 1) if pm25_avg else None,
            "pm25_category":    category,
            "pm25_colour":      colour,
            "exceeds_who":      (pm25_avg or 0) > WHO_24H,
            "exceeds_nepal":    (pm25_avg or 0) > NEPAL_NAAQS_24H,
            "who_24h":          WHO_24H,
            "nepal_naaqs_24h":  NEPAL_NAAQS_24H,
            "companion":        companion,
            "maintenance_logs": sensor.maintenance_logs.order_by("-event_date")[:10],
            "last_data_date":   last_data_date,
            "last_data_mean":   last_data_mean,
            "avail_dates_json": sorted(d.isoformat() for d in avail_dates),
        })
        return ctx


class LocationView(TemplateView):
    template_name = "pages/location.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        site = get_object_or_404(Site, pk=self.kwargs["pk"])
        sensors = list(Sensor.objects.filter(site=site).select_related("site"))
        latest  = _latest_pm25(sensors)

        pollutant_summaries = {}
        for pollutant in ["PM25", "PM10", "PM1", "TEMP", "RH", "CO2"]:
            agg = CanonicalReading.objects.filter(
                site=site, pollutant=pollutant, is_duplicate=False,
                quality_flag__in=["GOOD"],
                original_ts__gte=timezone.now() - timedelta(hours=24),
            ).aggregate(avg=Avg("raw_value"), count=Count("id"))
            if agg["count"]:
                pollutant_summaries[pollutant] = agg

        ctx.update({
            "site":                site,
            "sensors":             sensors,
            "sensor_latest":       latest,
            "pollutant_summaries": pollutant_summaries,
            "who_24h":             WHO_24H,
            "nepal_naaqs_24h":     NEPAL_NAAQS_24H,
            "sensor_geojson":      _sensor_geojson(sensors, latest),
            "pm25_scale":          PM25_SCALE,
        })
        return ctx


class IndoorOutdoorView(TemplateView):
    template_name = "pages/indoor_outdoor.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        site = get_object_or_404(Site, pk=self.kwargs["pk"])

        indoor_sensors  = list(Sensor.objects.filter(site=site, is_indoor=True))
        outdoor_sensors = list(Sensor.objects.filter(site=site, is_indoor=False))

        def get_24h_stats(sensor):
            return CanonicalReading.objects.filter(
                sensor=sensor, pollutant="PM25", is_duplicate=False,
                quality_flag__in=["GOOD", "UNVALIDATED"],
                original_ts__gte=timezone.now() - timedelta(hours=24),
            ).aggregate(avg=Avg("raw_value"), count=Count("id"))

        indoor_stats  = [(s, get_24h_stats(s)) for s in indoor_sensors]
        outdoor_stats = [(s, get_24h_stats(s)) for s in outdoor_sensors]

        io_ratio = None
        if indoor_stats and outdoor_stats:
            in_avg  = indoor_stats[0][1]["avg"]
            out_avg = outdoor_stats[0][1]["avg"]
            if in_avg and out_avg and out_avg > 0:
                io_ratio = round(in_avg / out_avg, 2)

        ctx.update({
            "site":            site,
            "indoor_sensors":  indoor_stats,
            "outdoor_sensors": outdoor_stats,
            "io_ratio":        io_ratio,
            "who_24h":         WHO_24H,
            "nepal_naaqs_24h": NEPAL_NAAQS_24H,
        })
        return ctx


class AnalysisView(TemplateView):
    template_name = "pages/analysis.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sensors = list(Sensor.objects.select_related("site").all())
        latest  = _latest_pm25(sensors)

        # Find site with indoor/outdoor pair
        site_with_pair = None
        for site in Site.objects.all():
            site_sensors = [s for s in sensors if s.site_id == site.pk]
            if any(s.is_indoor for s in site_sensors) and any(not s.is_indoor for s in site_sensors):
                site_with_pair = site
                break

        # Compute overall I/O ratio and % indoor worse (simple 24h window)
        io_ratio = None
        pct_indoor_worse = None
        if site_with_pair:
            indoor  = next((s for s in sensors if s.site_id == site_with_pair.pk and s.is_indoor),  None)
            outdoor = next((s for s in sensors if s.site_id == site_with_pair.pk and not s.is_indoor), None)
            if indoor and outdoor:
                in_avg  = CanonicalReading.objects.filter(
                    sensor=indoor,  pollutant="PM25", is_duplicate=False,
                    quality_flag__in=["GOOD", "UNVALIDATED"],
                    original_ts__gte=timezone.now() - timedelta(hours=24),
                ).aggregate(avg=Avg("raw_value"))["avg"]
                out_avg = CanonicalReading.objects.filter(
                    sensor=outdoor, pollutant="PM25", is_duplicate=False,
                    quality_flag__in=["GOOD", "UNVALIDATED"],
                    original_ts__gte=timezone.now() - timedelta(hours=24),
                ).aggregate(avg=Avg("raw_value"))["avg"]
                if in_avg and out_avg and out_avg > 0:
                    io_ratio = round(in_avg / out_avg, 2)
                    pct_indoor_worse = round((in_avg > out_avg) * 100)

        # Sensor list JSON for JS charts
        sensor_list_json = json.dumps([
            {"id": s.pk, "name": s.friendly_name, "is_indoor": s.is_indoor}
            for s in sensors
        ])

        # Exposure estimates
        try:
            from apps.exposure.estimator import compute_district_exposure
            exposure_estimates = compute_district_exposure()
        except Exception:
            exposure_estimates = []

        # Analysis scripts — auto-refresh CONTINUOUS ones that are stale
        try:
            from apps.analysis.models import AnalysisScript
            scripts = list(AnalysisScript.objects.filter(is_active=True))
            for s in scripts:
                if s.needs_refresh:
                    s.execute()
        except Exception:
            scripts = []

        # Sensor date ranges for date pickers (min/max available date per sensor)
        sensor_dates = {}
        for s in sensors:
            from django.db.models import Min as _Min, Max as _Max
            agg = CanonicalReading.objects.filter(
                sensor=s, pollutant="PM25", is_duplicate=False,
            ).aggregate(min_ts=_Min("original_ts"), max_ts=_Max("original_ts"))
            sensor_dates[s.pk] = {
                "min": agg["min_ts"].date().isoformat() if agg["min_ts"] else None,
                "max": agg["max_ts"].date().isoformat() if agg["max_ts"] else None,
            }

        # All sites with paired sensors
        paired_sites = []
        for site in Site.objects.all():
            site_sensors = [s for s in sensors if s.site_id == site.pk]
            if any(s.is_indoor for s in site_sensors) and any(not s.is_indoor for s in site_sensors):
                paired_sites.append(site)

        ctx.update({
            "sensors":           sensors,
            "site_with_pair":    site_with_pair,
            "paired_sites":      paired_sites,
            "io_ratio":          io_ratio,
            "pct_indoor_worse":  pct_indoor_worse,
            "sensor_list_json":  sensor_list_json,
            "sensor_dates_json": json.dumps(sensor_dates),
            "exposure_estimates": exposure_estimates,
            "analysis_scripts":  scripts,
        })
        return ctx


class DataAccessView(TemplateView):
    template_name = "pages/data_access.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sensors = list(Sensor.objects.select_related("site").order_by("site__name"))

        try:
            from apps.analysis.models import DownloadConfig
            dl_cfg = DownloadConfig.get()
        except Exception:
            class _Cfg:
                max_rows_public = 500
                public_days_limit = 7
            dl_cfg = _Cfg()

        # Determine the current user's download tier
        user = self.request.user
        if user.is_authenticated and getattr(user, "role", "PUBLIC") in ("ADMIN", "MAINTAINER"):
            dl_tier = "admin"
        elif user.is_authenticated and getattr(user, "role", "PUBLIC") == "RESEARCHER":
            dl_tier = "researcher"
        else:
            dl_tier = "public"

        ctx.update({
            "sensors":         sensors,
            "sensors_json":    json.dumps([
                {"id": s.pk, "name": s.friendly_name, "serial": s.serial_number,
                 "site": s.site.name if s.site else "", "is_indoor": s.is_indoor}
                for s in sensors
            ]),
            "pollutants_choices": [
                ("PM25", "PM₂.₅"), ("PM10", "PM₁₀"), ("PM1", "PM₁"), ("PM4", "PM₄"),
                ("CO2", "CO₂"), ("TVOC", "TVOC"), ("TEMP", "Temperature"),
                ("RH", "Humidity"), ("BARO", "Pressure"), ("CO", "CO"),
                ("NO2", "NO₂"), ("O3", "O₃"), ("SO2", "SO₂"),
            ],
            "api_endpoints":   API_ENDPOINTS,
            "data_dictionary": DATA_DICTIONARY,
            "dl_cfg":          dl_cfg,
            "dl_tier":         dl_tier,
        })
        return ctx


class MethodsView(TemplateView):
    template_name = "pages/methods.html"


class AboutView(TemplateView):
    template_name = "pages/about.html"


# ══════════════════════════════════════════════════════════════════════════════
# PORTAL VIEWS  (login required, role-gated)
# ══════════════════════════════════════════════════════════════════════════════

def _save_upload_and_queue(uploaded_file, user, sensor=None):
    """
    Save an uploaded file to MEDIA_ROOT/uploads/pending/ and create a PENDING
    IngestionJob row.  Returns the saved IngestionJob instance.

    No CSV parsing happens here — the management command does that.
    """
    import uuid
    from pathlib import Path
    from django.conf import settings
    from apps.ingestion.models import IngestionJob

    pending_dir = Path(settings.MEDIA_ROOT) / "uploads" / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    # Unique filename prevents collisions between concurrent uploads.
    suffix = Path(uploaded_file.name).suffix or ".csv"
    saved_name = f"{uuid.uuid4().hex}{suffix}"
    dest = pending_dir / saved_name

    with dest.open("wb") as f:
        for chunk in uploaded_file.chunks():
            f.write(chunk)

    return IngestionJob.objects.create(
        uploaded_by=user,
        original_filename=uploaded_file.name,
        file_path=str(dest),
        sensor=sensor,
        status=IngestionJob.Status.PENDING,
    )


class PortalRequiredMixin(LoginRequiredMixin):
    login_url = "/sign-in/"
    allowed_roles = ("RESEARCHER", "MAINTAINER", "ADMIN")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if request.user.role not in self.allowed_roles:
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("Access denied.")
        return super().dispatch(request, *args, **kwargs)


class PortalIndexView(PortalRequiredMixin, TemplateView):
    template_name = "portal/index.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["recent_logs"] = IngestionLog.objects.select_related("dataset").order_by("-created_at")[:10]
        return ctx


class IngestionLogsView(PortalRequiredMixin, TemplateView):
    template_name = "portal/ingestion_logs.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["logs"] = IngestionLog.objects.select_related("dataset", "triggered_by").order_by("-created_at")[:100]
        return ctx


class SensorRegisterPortalView(PortalRequiredMixin, View):
    """
    GET  /portal/sensors/register/ — show registration form
    POST /portal/sensors/register/ — create site (if needed) + sensor
    """

    template_name = "portal/sensor_register.html"
    allowed_roles = ("RESEARCHER", "MAINTAINER", "ADMIN")

    def get(self, request):
        from apps.sensors.models import Site
        ctx = {
            "sites": Site.objects.order_by("name"),
            "power_choices": Sensor.PowerType.choices,
            "connectivity_choices": Sensor.ConnectivityType.choices,
            "land_use_choices": Site.LandUse.choices,
        }
        return render(request, self.template_name, ctx)

    def post(self, request):
        from django.contrib import messages
        from apps.sensors.models import Site

        data = request.POST
        errors = []

        serial = data.get("serial_number", "").strip()
        if not serial:
            errors.append("Serial number is required.")

        # Resolve or create site
        existing_site_id = data.get("site_id", "").strip()
        site = None
        if existing_site_id:
            try:
                site = Site.objects.get(pk=existing_site_id)
            except Site.DoesNotExist:
                errors.append("Selected site not found.")
        else:
            site_name = data.get("site_name", "").strip()
            district   = data.get("district", "").strip()
            lat_raw    = data.get("latitude", "").strip()
            lon_raw    = data.get("longitude", "").strip()
            if not site_name or not district or not lat_raw or not lon_raw:
                errors.append("Site name, district, latitude, and longitude are required when creating a new site.")
            else:
                try:
                    lat = float(lat_raw); lon = float(lon_raw)
                except ValueError:
                    errors.append("Latitude and longitude must be numeric decimal degrees.")
                    lat = lon = None
                if lat is not None:
                    site, created = Site.objects.get_or_create(
                        name=site_name,
                        defaults={
                            "district": district,
                            "municipality": data.get("municipality", ""),
                            "province": data.get("province", ""),
                            "latitude": lat,
                            "longitude": lon,
                            "elevation_m": float(data.get("elevation_m") or 0) or None,
                            "land_use_type": data.get("land_use_type", "RESIDENTIAL"),
                            "description": data.get("site_description", ""),
                        },
                    )

        if errors:
            from apps.sensors.models import Site
            ctx = {
                "sites": Site.objects.order_by("name"),
                "power_choices": Sensor.PowerType.choices,
                "connectivity_choices": Sensor.ConnectivityType.choices,
                "land_use_choices": Site.LandUse.choices,
                "errors": errors,
                "form_data": data,
            }
            return render(request, self.template_name, ctx)

        # Check serial uniqueness
        if Sensor.objects.filter(serial_number=serial).exists():
            messages.warning(request, f"Sensor {serial} is already registered.")
            from django.shortcuts import redirect
            return redirect("portal:sensor_list")

        sensor = Sensor.objects.create(
            serial_number=serial,
            friendly_name=data.get("friendly_name", "").strip(),
            model=data.get("model", "").strip(),
            manufacturer=data.get("manufacturer", "Particles Plus").strip() or "Particles Plus",
            site=site,
            is_indoor=data.get("is_indoor") == "1",
            power_type=data.get("power_type", "UNKNOWN"),
            connectivity_type=data.get("connectivity_type", "WIFI"),
            status=Sensor.Status.ACTIVE,
            installation_notes=data.get("installation_notes", ""),
        )
        if data.get("installed_at"):
            try:
                from django.utils.dateparse import parse_date
                d = parse_date(data["installed_at"])
                if d:
                    from datetime import datetime, timezone as dt_tz
                    sensor.installed_at = datetime(d.year, d.month, d.day, tzinfo=dt_tz.utc)
                    sensor.save(update_fields=["installed_at"])
            except Exception:
                pass

        messages.success(request, f"Sensor {sensor.display_name} (#{sensor.pk}) registered successfully.")
        from django.shortcuts import redirect
        return redirect("portal:sensor_list")


class SensorListPortalView(PortalRequiredMixin, TemplateView):
    """GET /portal/sensors/ — list all registered sensors with quick actions."""

    template_name = "portal/sensor_list.html"
    allowed_roles = ("RESEARCHER", "MAINTAINER", "ADMIN")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["sensors"] = Sensor.objects.select_related("site").order_by("site__name", "serial_number")
        return ctx


class SensorUploadView(PortalRequiredMixin, View):
    """
    GET  /portal/sensors/<pk>/upload/ — upload form for a specific sensor
    POST /portal/sensors/<pk>/upload/ — save file, queue IngestionJob, return JSON immediately
    """

    template_name = "portal/sensor_upload.html"
    allowed_roles = ("RESEARCHER", "MAINTAINER", "ADMIN")

    def get(self, request, pk):
        sensor = get_object_or_404(Sensor.objects.select_related("site"), pk=pk)
        return render(request, self.template_name, {"sensor": sensor})

    def post(self, request, pk):
        sensor = get_object_or_404(Sensor.objects.select_related("site"), pk=pk)
        uploaded = request.FILES.get("file")

        if not uploaded:
            return JsonResponse({"status": "FAILED", "error": "No file selected."}, status=400)

        if uploaded.size > 200 * 1024 * 1024:
            return JsonResponse({"status": "FAILED", "error": "File exceeds 200 MB limit."}, status=400)

        job = _save_upload_and_queue(uploaded, request.user, sensor=sensor)
        return JsonResponse({
            "status": "QUEUED",
            "job_id": job.pk,
            "filename": uploaded.name,
            "sensor_serial": sensor.serial_number,
            "message": f"File queued for import (job #{job.pk}). Poll /portal/jobs/{job.pk}/status/ for progress.",
        })


class UploadCSVView(PortalRequiredMixin, View):
    """
    GET  /portal/upload-csv/ — show upload form with format documentation
    POST /portal/upload-csv/ — save file, queue an IngestionJob, redirect immediately
    """

    template_name = "portal/upload_csv.html"
    allowed_roles = ("RESEARCHER", "ADMIN", "MAINTAINER")

    def get(self, request):
        from apps.ingestion.models import IngestionJob
        ctx = {
            "sensors": Sensor.objects.select_related("site").order_by("site__name", "serial_number"),
            "recent_jobs": IngestionJob.objects.filter(uploaded_by=request.user).order_by("-created_at")[:10],
        }
        return render(request, self.template_name, ctx)

    def post(self, request):
        from django.contrib import messages
        from django.shortcuts import redirect
        from apps.ingestion.models import IngestionJob

        uploaded = request.FILES.get("file")
        if not uploaded:
            messages.error(request, "No file selected.")
            return redirect("portal:upload_csv")

        max_mb = 200
        if uploaded.size > max_mb * 1024 * 1024:
            messages.error(request, f"File exceeds {max_mb} MB limit.")
            return redirect("portal:upload_csv")

        job = _save_upload_and_queue(uploaded, request.user, sensor=None)
        messages.success(
            request,
            f"'{uploaded.name}' queued for import (job #{job.pk}). "
            "Check back in a minute — the page will show the result when done."
        )
        return redirect("portal:upload_csv")


class APIKeysView(PortalRequiredMixin, View):
    """
    GET  /portal/api-keys/  — list keys + show create form
    POST /portal/api-keys/  — create a new API key
    """

    template_name = "portal/api_keys.html"
    allowed_roles = ("RESEARCHER", "ADMIN", "MAINTAINER")

    def get(self, request):
        from apps.api.models import APIKey
        ctx = {
            "api_keys": APIKey.objects.filter(user=request.user, is_active=True).select_related("sensor"),
            "sensors": Sensor.objects.select_related("site").order_by("site__name"),
        }
        return render(request, self.template_name, ctx)

    def post(self, request):
        from django.contrib import messages
        from apps.api.models import APIKey

        action = request.POST.get("action")

        if action == "revoke":
            key_id = request.POST.get("key_id")
            try:
                key = APIKey.objects.get(pk=key_id, user=request.user)
                key.is_active = False
                key.save(update_fields=["is_active"])
                messages.success(request, "API key revoked.")
            except APIKey.DoesNotExist:
                messages.error(request, "Key not found.")
        else:
            name = request.POST.get("name", "").strip()
            role = request.POST.get("role", "SENSOR")
            sensor_id = request.POST.get("sensor_id", "").strip()

            if not name:
                messages.error(request, "Key name is required.")
            else:
                sensor = None
                if sensor_id:
                    try:
                        sensor = Sensor.objects.get(pk=sensor_id)
                    except Sensor.DoesNotExist:
                        messages.warning(request, "Sensor not found — key created without sensor binding.")

                key_obj, raw_key = APIKey.generate(name=name, role=role, user=request.user)
                if sensor:
                    key_obj.sensor = sensor
                    key_obj.save(update_fields=["sensor"])

                messages.success(request, f"Key created. Copy it now — it won't be shown again: {raw_key}")

        from django.shortcuts import redirect
        return redirect("portal:api_keys")


class AggregationView(PortalRequiredMixin, View):
    """
    GET  /portal/aggregate/ — show aggregation form
    POST /portal/aggregate/ — run synchronous aggregation + optionally write Parquet
    """

    template_name = "portal/aggregation.html"
    allowed_roles = ("MAINTAINER", "ADMIN")

    def get(self, request):
        ctx = {
            "sensors": Sensor.objects.select_related("site").order_by("site__name"),
        }
        return render(request, self.template_name, ctx)

    def post(self, request):
        from django.contrib import messages
        from apps.ingestion.tasks import compute_aggregates, write_parquet

        sensor_id = request.POST.get("sensor_id") or None
        since_str = request.POST.get("since") or None
        do_parquet = request.POST.get("write_parquet") == "1"

        since = None
        if since_str:
            from datetime import datetime, timezone as dt_tz
            try:
                from django.utils.dateparse import parse_date
                d = parse_date(since_str)
                if d:
                    since = datetime(d.year, d.month, d.day, tzinfo=dt_tz.utc)
            except Exception:
                messages.error(request, f"Invalid date: {since_str}")

        result = compute_aggregates(
            sensor_id=int(sensor_id) if sensor_id else None,
            since=since,
        )

        parquet_result = None
        if do_parquet:
            from apps.sensors.models import Sensor as _Sensor
            target_sensors = [_Sensor.objects.get(pk=sensor_id)] if sensor_id else list(_Sensor.objects.all())
            rows_written = 0
            for s in target_sensors:
                pr = write_parquet(s.pk)
                rows_written += pr.get("rows_written", 0)
            parquet_result = {"rows_written": rows_written}

        messages.success(
            request,
            f"Aggregation complete — {result['hourly_rows']:,} hourly rows, "
            f"{result['daily_rows']:,} daily rows updated across "
            f"{result['sensors_processed']} sensor(s)."
        )

        ctx = {
            "sensors": Sensor.objects.select_related("site").order_by("site__name"),
            "result": result,
            "parquet_result": parquet_result,
        }
        return render(request, self.template_name, ctx)


class RunIngestionView(PortalRequiredMixin, TemplateView):
    template_name = "portal/run_ingestion.html"
    allowed_roles = ("MAINTAINER", "ADMIN")


class RunAnalysisScriptView(PortalRequiredMixin, View):
    """POST /portal/analysis/run/<pk>/ — run a script and return JSON."""
    allowed_roles = ("MAINTAINER", "ADMIN")

    def get(self, request, pk):
        try:
            from apps.analysis.models import AnalysisScript
            script = get_object_or_404(AnalysisScript, pk=pk)
            result, error = script.execute()
            return JsonResponse({
                "ok": not bool(error),
                "error": error,
                "result": result,
                "last_run_at": script.last_run_at.isoformat() if script.last_run_at else None,
                "run_duration_ms": script.run_duration_ms,
            })
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=500)


class IngestionJobStatusView(PortalRequiredMixin, View):
    """GET /portal/jobs/<pk>/status/ — JSON polling endpoint for upload job progress."""

    allowed_roles = ("RESEARCHER", "MAINTAINER", "ADMIN")

    def get(self, request, pk):
        from apps.ingestion.models import IngestionJob
        job = get_object_or_404(IngestionJob, pk=pk, uploaded_by=request.user)
        return JsonResponse({
            "job_id":   job.pk,
            "status":   job.status,
            "filename": job.original_filename,
            "saved":      job.records_saved,
            "duplicates": job.records_duplicate,
            "errors":     job.records_error,
            "error_detail": job.error_detail,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        })
