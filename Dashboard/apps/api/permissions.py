"""
Custom DRF permissions for the Nepal AQ API.

Permission model (least → most privileged):
  - SensorWrite:      SENSOR or ADMIN API key  — submit readings
  - ResearcherRead:   RESEARCHER/ADMIN API key or researcher+ session user
  - MaintainerWrite:  ADMIN API key or maintainer/admin session user
  - AdminAPIKey:      ADMIN API key or admin session user only — management endpoints
"""
from rest_framework.permissions import BasePermission

from .models import APIKey


class HasSensorWritePermission(BasePermission):
    """Allow POST of readings if authenticated with a SENSOR or ADMIN API key."""

    message = "A valid sensor API key (X-API-Key) is required to submit readings."

    def has_permission(self, request, view):
        auth = request.auth
        if isinstance(auth, APIKey):
            return auth.role in (APIKey.Role.SENSOR, APIKey.Role.ADMIN)
        # Also allow admin users via session
        return request.user.is_authenticated and getattr(request.user, "is_dashboard_admin", False)


class HasResearcherPermission(BasePermission):
    """Allow if user has researcher+ role or a RESEARCHER/ADMIN API key."""

    message = "Researcher or admin access required."

    def has_permission(self, request, view):
        auth = request.auth
        if isinstance(auth, APIKey):
            return auth.role in (APIKey.Role.RESEARCHER, APIKey.Role.ADMIN)
        if request.user.is_authenticated:
            return getattr(request.user, "is_researcher_or_above", False)
        return False


class IsMaintainerOrAdmin(BasePermission):
    """Allow if user has maintainer/admin role or an ADMIN API key."""

    def has_permission(self, request, view):
        auth = request.auth
        if isinstance(auth, APIKey) and auth.role == APIKey.Role.ADMIN:
            return True
        return (
            request.user.is_authenticated
            and getattr(request.user, "is_maintainer_or_above", False)
        )


class IsAdminAPIKey(BasePermission):
    """Highest privilege tier — required for management endpoints.

    Grants access only to:
      - An active API key with role=ADMIN (X-API-Key header)
      - A session user with is_dashboard_admin (role=ADMIN)

    Use this on any endpoint that creates, updates, or deletes sensors or sites.
    """

    message = "An ADMIN API key (X-API-Key) or admin session is required for this operation."

    def has_permission(self, request, view):
        auth = request.auth
        if isinstance(auth, APIKey):
            return auth.role == APIKey.Role.ADMIN and auth.is_valid
        return (
            request.user.is_authenticated
            and getattr(request.user, "is_dashboard_admin", False)
        )
