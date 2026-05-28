"""Nepal Air Observatory — root URL configuration."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.dashboard.urls import observatory_patterns, portal_patterns

urlpatterns = [
    # Django admin
    path("django-admin/", admin.site.urls),

    # Auth
    path("users/", include("apps.users.urls")),

    # Public observatory pages (served at site root)
    path("", include((observatory_patterns, "observatory"))),

    # Research/admin portal (login required)
    path("portal/", include((portal_patterns, "portal"))),

    # Document library
    path("documents/", include("apps.documents.urls")),

    # REST API
    path("api/v1/", include("apps.api.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL,  document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
