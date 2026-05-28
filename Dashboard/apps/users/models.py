"""
Custom User model with role-based access control.

Roles determine which parts of the dashboard a user can access:
  - PUBLIC:      Read-only overview and latest readings
  - RESEARCHER:  Full data access, raw downloads, API docs
  - MAINTAINER:  Sensor metadata, maintenance logs, outage reports
  - ADMIN:       Full Wagtail CMS, user management, site/sensor creation
"""
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse


class CustomUser(AbstractUser):
    """Extended user model with dashboard-specific role and affiliation."""

    class Role(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        RESEARCHER = "RESEARCHER", "Researcher"
        MAINTAINER = "MAINTAINER", "Maintainer"
        ADMIN = "ADMIN", "Admin"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.PUBLIC,
        help_text="Controls which dashboard features are accessible.",
    )
    affiliation = models.CharField(
        max_length=200,
        blank=True,
        help_text="University, institution, or organisation.",
    )
    bio = models.TextField(blank=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["username"]

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    def get_absolute_url(self):
        return reverse("users:profile", kwargs={"pk": self.pk})

    # ── Role helpers ──────────────────────────────────────────────────────────

    @property
    def is_researcher_or_above(self):
        return self.role in (self.Role.RESEARCHER, self.Role.MAINTAINER, self.Role.ADMIN)

    @property
    def is_maintainer_or_above(self):
        return self.role in (self.Role.MAINTAINER, self.Role.ADMIN)

    @property
    def is_dashboard_admin(self):
        return self.role == self.Role.ADMIN
