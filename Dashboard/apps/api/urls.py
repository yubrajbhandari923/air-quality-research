"""URL patterns for the API v1."""
from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    # Readings — single timestamp (live sensor)
    path("readings/", views.ReadingListView.as_view(), name="readings"),

    # Readings — batch (offline catch-up / scraper)
    path("readings/batch/", views.BatchReadingView.as_view(), name="readings_batch"),

    # Aggregation trigger (maintainer/admin)
    path("aggregate/", views.AggregationTriggerView.as_view(), name="aggregate"),

    # CSV/JSON export (access-level controlled)
    path("export/", views.ExportReadingsView.as_view(), name="export"),

    # Sensors
    path("sensors/", views.SensorListView.as_view(), name="sensor_list"),
    path("sensors/<int:pk>/", views.SensorDetailView.as_view(), name="sensor_detail"),
    path("sensors/register/", views.SensorRegisterView.as_view(), name="sensor_register"),

    # Sites
    path("sites/", views.SiteListView.as_view(), name="site_list"),

    # Charts — sensor
    path("charts/sensor/<int:sensor_id>/timeseries/",   views.TimeSeriesChartView.as_view(),  name="chart_timeseries"),
    path("charts/sensor/<int:sensor_id>/completeness/", views.CompletenessChartView.as_view(), name="chart_completeness"),
    path("charts/sensor/<int:sensor_id>/diurnal/",      views.DiurnalChartView.as_view(),      name="chart_diurnal"),
    path("charts/sensor/<int:sensor_id>/monthly/",      views.MonthlyChartView.as_view(),      name="chart_monthly"),
    path("charts/sensor/<int:sensor_id>/date-range/",   views.SensorDateRangeView.as_view(),   name="chart_date_range"),

    # Charts — site
    path("charts/site/<int:site_id>/io-comparison/",    views.IOComparisonView.as_view(),      name="chart_io"),

    # Charts — national
    path("charts/national/summary/", views.NationalSummaryChartView.as_view(), name="chart_national"),
]
