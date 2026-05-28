"""URL patterns for the API v1."""
from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    # Readings
    path("readings/", views.ReadingListView.as_view(), name="readings"),

    # Sensors
    path("sensors/", views.SensorListView.as_view(), name="sensor_list"),
    path("sensors/<int:pk>/", views.SensorDetailView.as_view(), name="sensor_detail"),
    path("sensors/register/", views.SensorRegisterView.as_view(), name="sensor_register"),

    # Sites
    path("sites/", views.SiteListView.as_view(), name="site_list"),

    # Charts
    path("charts/sensor/<int:sensor_id>/timeseries/", views.TimeSeriesChartView.as_view(), name="chart_timeseries"),
    path("charts/sensor/<int:sensor_id>/completeness/", views.CompletenessChartView.as_view(), name="chart_completeness"),
    path("charts/national/summary/", views.NationalSummaryChartView.as_view(), name="chart_national"),
]
