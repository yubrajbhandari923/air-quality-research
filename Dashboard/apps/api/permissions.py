"""
Custom DRF permissions for the Nepal AQ API.

Permission model:
  - SensorWrite: requires an active APIKey with role SENSOR or ADMIN
  - ResearcherRead: requires login or a RESEARCHER/ADMIN API key
  - MaintainerWrite: requires login with MAINTAINER/ADMIN role
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
    """Allow if user has maintainer or admin role."""

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and getattr(request.user, "is_maintainer_or_above", False)
        )
