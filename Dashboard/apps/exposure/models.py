"""Exposure estimate models."""
from django.db import models
from apps.core.models import TimeStampedModel
from apps.sensors.models import Site


class PopulationExposureEstimate(TimeStampedModel):
    """
    Snapshot of population exposure estimate for a district.

    These are computed estimates with explicit confidence levels — never
    present these as precise measurements.
    """

    class Confidence(models.TextChoices):
        HIGH = "HIGH", "High (sensor within 20 km, >80% data completeness)"
        MEDIUM = "MEDIUM", "Medium (sensor within 50 km, 50–80% completeness)"
        LOW = "LOW", "Low (sensor >50 km away or <50% completeness)"
        NONE = "NONE", "No estimate available"

    district = models.CharField(max_length=100)
    estimate_date = models.DateField()
    nearest_site = models.ForeignKey(
        Site, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="exposure_estimates",
    )
    distance_km = models.FloatField(null=True, blank=True)
    pm25_mean_ugm3 = models.FloatField(null=True, blank=True)
    pm25_completeness = models.FloatField(null=True, blank=True, help_text="0–1")
    population = models.PositiveIntegerField(null=True, blank=True)
    pop_above_who_24h = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Estimated people exposed above WHO 24h guideline (15 µg/m³).",
    )
    confidence = models.CharField(max_length=10, choices=Confidence.choices, default=Confidence.NONE)
    methodology_note = models.TextField(
        default=(
            "Nearest-sensor assignment. Air quality at sensor location assigned to district. "
            "This is a simplified spatial proxy — actual exposure depends on microenvironments, "
            "indoor time, and local sources not captured by a single sensor."
        )
    )

    class Meta:
        ordering = ["-estimate_date", "district"]
        unique_together = [["district", "estimate_date"]]

    def __str__(self):
        return f"{self.district} | {self.estimate_date} | PM2.5={self.pm25_mean_ugm3} [{self.confidence}]"
