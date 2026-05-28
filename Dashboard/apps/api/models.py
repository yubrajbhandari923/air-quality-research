"""APIKey model for sensor and researcher API access."""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class APIKey(TimeStampedModel):
    """
    API key for authenticating sensor POST submissions and researcher data access.

    Keys are generated with 32 bytes of entropy (64 hex chars).
    Only the first 8 chars are shown after creation (for display purposes);
    the full key is stored hashed — but for simplicity in this implementation
    we store the raw key (for production, use django-rest-framework-api-key).
    """

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
    key = models.CharField(max_length=64, unique=True, db_index=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SENSOR)
    is_active = models.BooleanField(default=True)
    last_used = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_role_display()}) [{self.key[:8]}…]"

    @classmethod
    def generate(cls, name: str, role: str = "SENSOR", user=None) -> "APIKey":
        key = secrets.token_hex(32)
        return cls.objects.create(name=name, key=key, role=role, user=user)

    @property
    def is_valid(self) -> bool:
        if not self.is_active:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    def touch(self):
        """Update last_used timestamp."""
        self.last_used = timezone.now()
        self.save(update_fields=["last_used"])
