from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class IngestionJob(TimeStampedModel):
    """
    Represents a queued or completed CSV upload job.

    The upload view saves the file and creates a PENDING job immediately,
    then returns.  The process_ingestion_jobs management command picks up
    PENDING jobs and runs the converter in a separate process.
    """

    class Status(models.TextChoices):
        PENDING    = "PENDING",    "Pending"
        PROCESSING = "PROCESSING", "Processing"
        SUCCESS    = "SUCCESS",    "Success"
        PARTIAL    = "PARTIAL",    "Partial"
        FAILED     = "FAILED",     "Failed"

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="ingestion_jobs",
    )
    original_filename = models.CharField(max_length=255)
    file_path = models.CharField(
        max_length=500,
        help_text="Absolute path to the saved upload on disk.",
    )
    sensor = models.ForeignKey(
        "sensors.Sensor",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="ingestion_jobs",
        help_text="Pre-selected sensor (SensorUploadView). Null for the generic upload view.",
    )
    r2_key = models.CharField(
        max_length=500, blank=True,
        help_text="R2 object key for the uploaded CSV (e.g. uploads/42/data.csv).",
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    started_at  = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    records_saved      = models.PositiveIntegerField(default=0)
    records_duplicate  = models.PositiveIntegerField(default=0)
    records_error      = models.PositiveIntegerField(default=0)
    error_detail = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.original_filename} — {self.get_status_display()} ({self.created_at:%Y-%m-%d %H:%M})"
