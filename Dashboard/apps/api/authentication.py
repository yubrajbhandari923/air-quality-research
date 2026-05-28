"""API Key authentication backend for Django REST Framework."""
from rest_framework import authentication, exceptions

from .models import APIKey


class APIKeyAuthentication(authentication.BaseAuthentication):
    """
    Authenticate requests using an API key passed in the X-API-Key header.

    If the key is valid, returns (user_or_None, api_key_instance).
    If the key is invalid/expired, raises AuthenticationFailed.
    If no key is provided, returns None (allows other auth backends to try).
    """

    def authenticate(self, request):
        key = request.META.get("HTTP_X_API_KEY") or request.GET.get("api_key")
        if not key:
            return None  # No API key — try next auth backend

        try:
            api_key = APIKey.objects.select_related("user").get(key=key)
        except APIKey.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid API key.")

        if not api_key.is_valid:
            raise exceptions.AuthenticationFailed("API key is inactive or expired.")

        api_key.touch()

        # Return (user, auth) — user may be None for sensor keys
        return (api_key.user, api_key)

    def authenticate_header(self, request):
        return "X-API-Key"
