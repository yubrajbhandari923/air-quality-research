"""APIKey model for sensor and researcher API access.

Keys are never stored in plain text. On generation:
  - A 64-char hex key is produced with secrets.token_hex(32)
  - The first 8 chars are stored as `prefix` (plain text, used for DB lookup)
  - The full key is hashed with Django's PBKDF2-SHA256 and stored as `hashed_key`
  - The raw key is returned to the caller exactly once and never saved

On authentication:
  - Incoming key is split: prefix = key[:8]
  - DB lookup on prefix (fast indexed query, small candidate set)
  - check_password(raw_key, hashed_key) verifies the match
"""
import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel

KEY_PREFIX_LENGTH = 8


class APIKey(TimeStampedModel):
    class Role(models.TextChoices):
        SENSOR = "SENSOR", "Sensor (POST readings)"
        RESEARCHER = "RESEARCHER", "Researcher (GET data)"
        ADMIN = "ADMIN", "Admin (full access)"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(max_length=100, help_text="Human-readable label, e.g. 'Belauri Outdoor Sensor'")
    prefix = models.CharField(max_length=KEY_PREFIX_LENGTH, db_index=True, help_text="First 8 chars of key (plain text, for lookup)")
    hashed_key = models.CharField(max_length=256, help_text="PBKDF2-SHA256 hash of the full key")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SENSOR)
    is_active = models.BooleanField(default=True)
    last_used = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_role_display()}) [{self.prefix}…]"

    @classmethod
    def generate(cls, name: str, role: str = "SENSOR", user=None) -> tuple["APIKey", str]:
        """Create and save a new API key. Returns (instance, raw_key).

        The raw_key is shown to the user once and never stored — save it now.
        """
        raw_key = secrets.token_hex(32)          # 64 hex chars
        prefix = raw_key[:KEY_PREFIX_LENGTH]
        instance = cls.objects.create(
            name=name,
            role=role,
            user=user,
            prefix=prefix,
            hashed_key=make_password(raw_key),
        )
        return instance, raw_key

    @classmethod
    def verify(cls, raw_key: str) -> "APIKey | None":
        """Look up and verify a raw key. Returns the APIKey or None."""
        if not raw_key or len(raw_key) < KEY_PREFIX_LENGTH:
            return None
        prefix = raw_key[:KEY_PREFIX_LENGTH]
        candidates = cls.objects.filter(prefix=prefix, is_active=True)
        for candidate in candidates:
            if check_password(raw_key, candidate.hashed_key):
                return candidate
        return None

    @property
    def is_valid(self) -> bool:
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    def touch(self):
        self.last_used = timezone.now()
        self.save(update_fields=["last_used"])
