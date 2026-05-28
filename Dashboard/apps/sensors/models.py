"""
Sensor and Site models.

Design decisions:
  - latitude/longitude are stored as plain FloatFields so the app works without
    PostGIS. When PostGIS is available, add a PointField alongside these.
  - Sensor.status tracks the operational lifecycle; combined with MaintenanceLog
    it gives a full audit trail.
  - CalibrationNote is separate from MaintenanceLog because calibration events
    need their own coefficient fields.
"""
from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.core.models import TimeStampedModel


class Site(TimeStampedModel):
    """A physical monitoring location (e.g. Belauri village)."""

    class LandUse(models.TextChoices):
        RESIDENTIAL = "RESIDENTIAL", "Residential"
        AGRICULTURAL = "AGRICULTURAL", "Agricultural"
        FOREST = "FOREST", "Forest"
        URBAN = "URBAN", "Urban"
        INDUSTRIAL = "INDUSTRIAL", "Industrial"
        MIXED = "MIXED", "Mixed"
        OTHER = "OTHER", "Other"

    name = models.CharField(max_length=200, unique=True)
    district = models.CharField(max_length=100)
    municipality = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=100, blank=True)

    # Geographic coordinates
    latitude = models.FloatField(
        help_text="Decimal degrees (WGS 84). Positive = North."
    )
    longitude = models.FloatField(
        help_text="Decimal degrees (WGS 84). Positive = East."
    )
    elevation_m = models.FloatField(null=True, blank=True, help_text="Elevation above sea level (m).")

    population_estimate = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Estimated population within ~1 km of this site.",
    )
    land_use_type = models.CharField(max_length=20, choices=LandUse.choices, default=LandUse.RESIDENTIAL)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.district})"

    def get_absolute_url(self):
        return reverse("observatory:location", kwargs={"pk": self.pk})


class SensorManager(models.Manager):
    """Custom manager providing convenience querysets."""

    def active(self):
        return self.filter(status=Sensor.Status.ACTIVE)

    def indoor(self):
        return self.filter(is_indoor=True)

    def outdoor(self):
        return self.filter(is_indoor=False)


class Sensor(TimeStampedModel):
    """A physical air quality sensor deployed at a Site."""

    class PowerType(models.TextChoices):
        GRID = "GRID", "Grid"
        SOLAR = "SOLAR", "Solar"
        BATTERY = "BATTERY", "Battery"
        UNKNOWN = "UNKNOWN", "Unknown"

    class ConnectivityType(models.TextChoices):
        WIFI = "WIFI", "Wi-Fi"
        CELLULAR = "CELLULAR", "Cellular"
        LORA = "LORA", "LoRa"
        MANUAL = "MANUAL", "Manual upload"
        UNKNOWN = "UNKNOWN", "Unknown"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        OFFLINE = "OFFLINE", "Offline"
        MAINTENANCE = "MAINTENANCE", "Under Maintenance"
        DECOMMISSIONED = "DECOMMISSIONED", "Decommissioned"

    serial_number = models.CharField(max_length=100, unique=True)
    friendly_name = models.CharField(max_length=100, blank=True)
    model = models.CharField(max_length=50, help_text="e.g. 8143, 8144")
    manufacturer = models.CharField(max_length=100, default="Particles Plus")

    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="sensors")
    is_indoor = models.BooleanField(default=False, help_text="True for indoor deployments.")

    power_type = models.CharField(max_length=20, choices=PowerType.choices, default=PowerType.UNKNOWN)
    connectivity_type = models.CharField(
        max_length=20, choices=ConnectivityType.choices, default=ConnectivityType.WIFI
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    installed_at = models.DateTimeField(null=True, blank=True)
    decommissioned_at = models.DateTimeField(null=True, blank=True)

    installation_notes = models.TextField(blank=True)
    calibration_field_notes = models.TextField(blank=True, help_text="Free-text notes about calibration.")

    objects = SensorManager()

    class Meta:
        ordering = ["site__name", "serial_number"]

    def __str__(self):
        label = self.friendly_name or self.serial_number
        placement = "indoor" if self.is_indoor else "outdoor"
        return f"{label} ({placement}) @ {self.site.name}"

    def get_absolute_url(self):
        return reverse("observatory:sensor_detail", kwargs={"pk": self.pk})

    @property
    def display_name(self):
        return self.friendly_name or self.serial_number

    @property
    def status_css_class(self):
        mapping = {
            self.Status.ACTIVE: "bg-green-500",
            self.Status.OFFLINE: "bg-red-500",
            self.Status.MAINTENANCE: "bg-yellow-500",
            self.Status.DECOMMISSIONED: "bg-gray-400",
        }
        return mapping.get(self.status, "bg-gray-400")


class MaintenanceLog(TimeStampedModel):
    """Record of a maintenance, repair, or inspection event for a sensor."""

    class EventType(models.TextChoices):
        INSPECTION = "INSPECTION", "Inspection"
        REPAIR = "REPAIR", "Repair"
        CALIBRATION = "CALIBRATION", "Calibration"
        POWER_ISSUE = "POWER_ISSUE", "Power Issue"
        CONNECTIVITY_ISSUE = "CONNECTIVITY_ISSUE", "Connectivity Issue"
        RELOCATION = "RELOCATION", "Relocation"
        OTHER = "OTHER", "Other"

    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name="maintenance_logs")
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_logs",
    )
    event_date = models.DateField()
    event_type = models.CharField(max_length=30, choices=EventType.choices)
    description = models.TextField()
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-event_date"]

    def __str__(self):
        return f"{self.get_event_type_display()} on {self.event_date} — {self.sensor}"


class CalibrationNote(TimeStampedModel):
    """Records a calibration event with before/after coefficients."""

    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name="calibration_records")
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    calibration_date = models.DateField()
    method = models.CharField(max_length=200, blank=True, help_text="e.g. co-location with reference instrument")
    slope_before = models.FloatField(null=True, blank=True)
    intercept_before = models.FloatField(null=True, blank=True)
    slope_after = models.FloatField(null=True, blank=True)
    intercept_after = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-calibration_date"]

    def __str__(self):
        return f"Calibration {self.calibration_date} — {self.sensor}"
