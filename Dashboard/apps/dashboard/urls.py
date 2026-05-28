"""
URL configuration for the Nepal Air Observatory.

Two namespaces:
  observatory  — public-facing pages
  portal       — researcher/maintainer/admin tools (login required)
"""
from django.urls import path

from . import views

# ── Public namespace ──────────────────────────────────────────────────────────
observatory_patterns = [
    path("",                                views.HomepageView.as_view(),       name="home"),
    path("map/",                            views.MapView.as_view(),            name="map"),
    path("sensors/",                        views.SensorListView.as_view(),     name="sensor_list"),
    path("sensors/<int:pk>/",               views.SensorDetailView.as_view(),   name="sensor_detail"),
    path("locations/<int:pk>/",             views.LocationView.as_view(),       name="location"),
    path("locations/<int:pk>/indoor-outdoor/", views.IndoorOutdoorView.as_view(), name="indoor_outdoor"),
    path("analysis/",                       views.AnalysisView.as_view(),       name="analysis"),
    path("data/",                           views.DataAccessView.as_view(),     name="data_access"),
    path("methods/",                        views.MethodsView.as_view(),        name="methods"),
    path("about/",                          views.AboutView.as_view(),          name="about"),
]

# ── Portal namespace ──────────────────────────────────────────────────────────
portal_patterns = [
    path("",                views.PortalIndexView.as_view(),    name="index"),
    path("ingestion-logs/", views.IngestionLogsView.as_view(),  name="ingestion_logs"),
    path("upload-csv/",     views.UploadCSVView.as_view(),      name="upload_csv"),
    path("api-keys/",       views.APIKeysView.as_view(),        name="api_keys"),
    path("run-ingestion/",  views.RunIngestionView.as_view(),   name="run_ingestion"),
]
