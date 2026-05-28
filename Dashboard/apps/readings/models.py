"""
CanonicalReading — the single normalised table that holds every pollutant
measurement from every source.

Key design invariants:
  1. raw_value is NEVER overwritten.  If cleaning is needed, write to
     cleaned_value and update quality_flag.
  2. is_duplicate = True marks duplicates; they stay in the DB so we can audit.
  3. Readings are always timezone-aware (USE_TZ = True).
  4. source_type records the ingestion pathway for provenance.
"""
from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel
from apps.sensors.models import Sensor, Site


class Dataset(TimeStampedModel):
    """
    A logical grouping of readings (e.g. a CSV upload, a field campaign, an
    OpenAQ bulk download). Allows bulk management and provenance tracking.
    """

    class SourceType(models.TextChoices):
        LIVE_API = "LIVE_API", "Live API"
        CSV_UPLOAD = "CSV_UPLOAD", "CSV Upload"
        BATCH_UPLOAD = "BATCH_UPLOAD", "Batch Upload"
        MANUAL = "MANUAL", "Manual Entry"
        EXTERNAL = "EXTERNAL", "External (e.g. OpenAQ)"

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    source_file = models.CharField(max_length=500, blank=True, help_text="Original filename or URL.")
    record_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.get_source_type_display()}, {self.record_count} records)"


class ReadingManager(models.Manager):
    """Common querysets for CanonicalReading."""

    def good(self):
        return self.filter(quality_flag=CanonicalReading.QualityFlag.GOOD)

    def for_sensor(self, sensor):
        return self.filter(sensor=sensor)

    def for_pollutant(self, pollutant):
        return self.filter(pollutant=pollutant)

    def indoor(self):
        return self.filter(is_indoor=True)

    def outdoor(self):
        return self.filter(is_indoor=False)


class CanonicalReading(TimeStampedModel):
    """
    One pollutant measurement from one sensor at one point in time.

    Every ingestion pathway (CSV, API POST, OpenAQ, manual) must produce
    rows in this format.  The `source_type` field records which pathway
    was used so we can reproduce or audit any reading.
    """

    class Pollutant(models.TextChoices):
        PM1 = "PM1", "PM1 (µg/m³)"
        PM25 = "PM25", "PM2.5 (µg/m³)"
        PM4 = "PM4", "PM4 (µg/m³)"
        PM10 = "PM10", "PM10 (µg/m³)"
        CO2 = "CO2", "CO₂ (ppm)"
        TVOC = "TVOC", "Total VOC (mg/m³)"
        TEMP = "TEMP", "Temperature (°C)"
        RH = "RH", "Relative Humidity (%)"
        BARO = "BARO", "Barometric Pressure (inHg)"
        NC05 = "NC05", "NC 0.5 (#/cm³)"
        NC1 = "NC1", "NC 1.0 (#/cm³)"
        NC25 = "NC25", "NC 2.5 (#/cm³)"

    class QualityFlag(models.TextChoices):
        GOOD = "GOOD", "Good"
        SUSPECT = "SUSPECT", "Suspect"
        BAD = "BAD", "Bad"
        MISSING = "MISSING", "Missing"
        UNVALIDATED = "UNVALIDATED", "Unvalidated"

    class SourceType(models.TextChoices):
        LIVE_API = "LIVE_API", "Live API"
        CSV_UPLOAD = "CSV_UPLOAD", "CSV Upload"
        BATCH_UPLOAD = "BATCH_UPLOAD", "Batch Upload"
        MANUAL = "MANUAL", "Manual Entry"
        EXTERNAL = "EXTERNAL", "External"

    # ── Temporal ──────────────────────────────────────────────────────────────
    original_ts = models.DateTimeField(
        db_index=True,
        help_text="Timestamp as reported by the sensor (timezone-aware).",
    )
    received_ts = models.DateTimeField(
        auto_now_add=True,
        help_text="When this record was ingested into our system.",
    )
    timezone = models.CharField(
        max_length=50,
        default="Asia/Kathmandu",
        help_text="IANA timezone string for the sensor's deployment location.",
    )
    interval_seconds = models.IntegerField(
        null=True, blank=True,
        help_text="Reporting interval in seconds (e.g. 60 for 1-minute data).",
    )

    # ── Measurement ───────────────────────────────────────────────────────────
    pollutant = models.CharField(max_length=10, choices=Pollutant.choices, db_index=True)
    unit = models.CharField(
        max_length=20,
        help_text="e.g. µg/m³, ppm, °C, %, inHg, #/cm³",
    )
    raw_value = models.FloatField(
        null=True, blank=True,
        help_text="Value exactly as received from the sensor. NEVER overwrite.",
    )
    cleaned_value = models.FloatField(
        null=True, blank=True,
        help_text="QC-corrected value. raw_value is always preserved separately.",
    )

    # ── Provenance ────────────────────────────────────────────────────────────
    sensor = models.ForeignKey(Sensor, on_delete=models.PROTECT, related_name="readings")
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name="readings")
    is_indoor = models.BooleanField(default=False)
    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.CSV_UPLOAD)
    dataset = models.ForeignKey(
        Dataset, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="readings",
    )

    # ── Quality ───────────────────────────────────────────────────────────────
    quality_flag = models.CharField(
        max_length=15, choices=QualityFlag.choices, default=QualityFlag.UNVALIDATED, db_index=True
    )
    flag_reason = models.TextField(
        blank=True,
        help_text="Human-readable reason for SUSPECT/BAD flag.",
    )
    is_duplicate = models.BooleanField(
        default=False,
        help_text="True if this reading is a duplicate. Kept for audit trail.",
    )

    objects = ReadingManager()

    class Meta:
        ordering = ["-original_ts"]
        indexes = [
            models.Index(fields=["sensor", "pollutant", "original_ts"]),
            models.Index(fields=["site", "original_ts"]),
            models.Index(fields=["pollutant", "quality_flag"]),
        ]
        # Prevent exact duplicate rows
        constraints = [
            models.UniqueConstraint(
                fields=["sensor", "pollutant", "original_ts"],
                condition=models.Q(is_duplicate=False),
                name="unique_reading_per_sensor_pollutant_ts",
            )
        ]

    def __str__(self):
        val = self.cleaned_value if self.cleaned_value is not None else self.raw_value
        return f"{self.get_pollutant_display()} = {val} {self.unit} @ {self.original_ts:%Y-%m-%d %H:%M} [{self.sensor}]"

    @property
    def effective_value(self):
        """Returns cleaned_value if available, else raw_value."""
        return self.cleaned_value if self.cleaned_value is not None else self.raw_value

    @property
    def quality_css_class(self):
        mapping = {
            self.QualityFlag.GOOD: "bg-green-100 text-green-800",
            self.QualityFlag.SUSPECT: "bg-yellow-100 text-yellow-800",
            self.QualityFlag.BAD: "bg-red-100 text-red-800",
            self.QualityFlag.MISSING: "bg-gray-100 text-gray-600",
            self.QualityFlag.UNVALIDATED: "bg-blue-100 text-blue-700",
        }
        return mapping.get(self.quality_flag, "bg-gray-100")


class DailyAggregate(TimeStampedModel):
    """
    Pre-computed daily statistics per sensor/pollutant.

    Populated by a Celery task so dashboard queries are fast.
    Never use this as source of truth — always regenerate from CanonicalReading.
    """

    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name="daily_aggregates")
    site = models.ForeignKey(Site, on_delete=models.CASCADE, related_name="daily_aggregates")
    date = models.DateField(db_index=True)
    pollutant = models.CharField(max_length=10, choices=CanonicalReading.Pollutant.choices)
    is_indoor = models.BooleanField(default=False)

    count = models.PositiveIntegerField(default=0, help_text="Number of valid readings.")
    mean = models.FloatField(null=True, blank=True)
    median = models.FloatField(null=True, blank=True)
    std = models.FloatField(null=True, blank=True)
    min_value = models.FloatField(null=True, blank=True)
    max_value = models.FloatField(null=True, blank=True)
    p25 = models.FloatField(null=True, blank=True)
    p75 = models.FloatField(null=True, blank=True)
    p95 = models.FloatField(null=True, blank=True)

    # Fraction of expected readings that were actually received (0–1)
    completeness = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["-date"]
        unique_together = [["sensor", "date", "pollutant"]]

    def __str__(self):
        return f"{self.date} | {self.sensor} | {self.pollutant} | mean={self.mean}"


class IngestionLog(TimeStampedModel):
    """
    Audit trail for every ingestion attempt.
    Success and failure cases are both logged.
    """

    class Status(models.TextChoices):
        SUCCESS = "SUCCESS", "Success"
        PARTIAL = "PARTIAL", "Partial"
        FAILED = "FAILED", "Failed"

    source_name = models.CharField(max_length=100, help_text="e.g. BelauriCSVConverter")
    source_file = models.CharField(max_length=500, blank=True)
    dataset = models.ForeignKey(Dataset, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=10, choices=Status.choices)
    records_attempted = models.PositiveIntegerField(default=0)
    records_saved = models.PositiveIntegerField(default=0)
    records_duplicate = models.PositiveIntegerField(default=0)
    records_error = models.PositiveIntegerField(default=0)
    error_detail = models.TextField(blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.source_name} @ {self.created_at:%Y-%m-%d %H:%M} — {self.get_status_display()}"
