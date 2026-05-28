"""Core abstract models shared across all apps."""
from django.db import models


class TimeStampedModel(models.Model):
    """
    Abstract base class that provides self-updating `created_at` and `updated_at`
    timestamp fields. All domain models should inherit from this.
    """

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]
