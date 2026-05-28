"""API Key authentication backend for Django REST Framework."""
from rest_framework import authentication, exceptions

from .models import APIKey


class APIKeyAuthentication(authentication.BaseAuthentication):
    """
    Authenticate requests using an API key passed in the X-API-Key header.

    Keys are verified against a PBKDF2 hash — the plain-text key is never
    stored in the database. Lookup uses the first 8 chars (prefix) as an
    index, then hash-checks matching candidates.
    """

    def authenticate(self, request):
        raw_key = request.META.get("HTTP_X_API_KEY") or request.GET.get("api_key")
        if not raw_key:
            return None  # no key provided — try next auth backend

        api_key = APIKey.verify(raw_key)
        if api_key is None:
            raise exceptions.AuthenticationFailed("Invalid API key.")

        if not api_key.is_valid:
            raise exceptions.AuthenticationFailed("API key is inactive or expired.")

        api_key.touch()
        return (api_key.user, api_key)

    def authenticate_header(self, request):
        return "X-API-Key"
