"""
process_ingestion_jobs — pick up PENDING IngestionJob rows and run the CSV converter.

Intended to be called by a Render Cron Job every minute:
    python manage.py process_ingestion_jobs

Safe to run concurrently: each job is claimed atomically via
select_for_update(skip_locked=True) so two workers never process the same job.
"""
import logging
from datetime import timezone as dt_tz
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process pending CSV ingestion jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=5,
            help="Maximum number of jobs to process in one run (default: 5).",
        )

    def handle(self, *args, **options):
        from apps.ingestion.models import IngestionJob

        max_jobs = options["max_jobs"]
        processed = 0

        while processed < max_jobs:
            # Claim one PENDING job atomically — skip rows locked by another worker.
            with transaction.atomic():
                job = (
                    IngestionJob.objects.select_for_update(skip_locked=True)
                    .filter(status=IngestionJob.Status.PENDING)
                    .order_by("created_at")
                    .first()
                )
                if job is None:
                    break
                job.status = IngestionJob.Status.PROCESSING
                job.started_at = timezone.now()
                job.save(update_fields=["status", "started_at"])

            self.stdout.write(f"Processing job {job.pk}: {job.original_filename}")
            self._run_job(job)
            processed += 1

        if processed == 0:
            self.stdout.write("No pending jobs.")
        else:
            self.stdout.write(f"Processed {processed} job(s).")

    def _run_job(self, job):
        from apps.ingestion.models import IngestionJob
        from apps.ingestion.converters.generic_csv_converter import GenericCSVConverter

        path = Path(job.file_path)

        try:
            if not path.exists():
                raise FileNotFoundError(f"Upload file missing from disk: {path}")

            converter = GenericCSVConverter()
            result = converter.run(
                source=path,
                triggered_by=job.uploaded_by,
                dataset_name=f"Upload: {job.original_filename}",
            )

            job.records_saved     = result.get("saved", 0)
            job.records_duplicate = result.get("duplicates", 0)
            job.records_error     = result.get("errors", 0)
            job.error_detail      = result.get("error", "")
            job.finished_at       = timezone.now()
            job.status = (
                IngestionJob.Status.SUCCESS if result.get("status") == "SUCCESS"
                else IngestionJob.Status.PARTIAL if result.get("status") == "PARTIAL"
                else IngestionJob.Status.FAILED
            )

            # Trigger aggregation for affected sensors
            if result.get("saved", 0) > 0 and result.get("sensor_ids"):
                from apps.ingestion.tasks import aggregate_after_upload
                aggregate_after_upload(result["sensor_ids"])

        except Exception as exc:
            logger.exception("Job %d failed: %s", job.pk, exc)
            job.status       = IngestionJob.Status.FAILED
            job.error_detail = str(exc)
            job.finished_at  = timezone.now()

        finally:
            job.save(update_fields=[
                "status", "started_at", "finished_at",
                "records_saved", "records_duplicate", "records_error", "error_detail",
            ])
            # Clean up the temp file regardless of outcome
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
