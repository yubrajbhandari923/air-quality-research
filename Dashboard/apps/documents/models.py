"""Document library models for sharing research outputs and data downloads."""
from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.core.models import TimeStampedModel


class DocumentCategory(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Document Categories"

    def __str__(self):
        return self.name


class Document(TimeStampedModel):
    """A research document, dataset download, or report."""

    class AccessLevel(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        RESEARCHER = "RESEARCHER", "Researchers only"
        MAINTAINER = "MAINTAINER", "Maintainers only"

    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        DocumentCategory, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="documents",
    )
    file = models.FileField(upload_to="documents/", blank=True, null=True)
    external_url = models.URLField(blank=True, help_text="Link to external resource.")
    access_level = models.CharField(max_length=20, choices=AccessLevel.choices, default=AccessLevel.PUBLIC)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="research_documents",
    )
    download_count = models.PositiveIntegerField(default=0)
    is_featured = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("documents:detail", kwargs={"pk": self.pk})
