"""
process_ingestion_jobs — Cron 1a (runs every minute via Render Cron Job).

Picks up PENDING IngestionJob rows, downloads the CSV from R2 to a temp file,
runs GenericCSVConverter, then updates the job status.

Safe for concurrent workers: jobs are claimed atomically with
select_for_update(skip_locked=True).
"""
import logging
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process pending CSV ingestion jobs (downloads CSV from R2, imports to Postgres)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-jobs", type=int, default=5,
            help="Max jobs to process per run (default: 5).",
        )

    def handle(self, *args, **options):
        from apps.ingestion.models import IngestionJob

        processed = 0
        while processed < options["max_jobs"]:
            with transaction.atomic():
                job = (
                    IngestionJob.objects
                    .select_for_update(skip_locked=True)
                    .filter(status=IngestionJob.Status.PENDING)
                    .order_by("created_at")
                    .first()
                )
                if job is None:
                    break
                job.status     = IngestionJob.Status.PROCESSING
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
        from apps.ingestion import r2

        try:
            suffix = Path(job.original_filename).suffix or ".csv"

            if job.r2_key:
                # Production: download from R2 to a temp file.
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp_path = Path(tmp.name)
                r2.download_to_path(job.r2_key, tmp_path)
                downloaded_from_r2 = True
            elif job.file_path and Path(job.file_path).exists():
                # Local dev: read directly from disk.
                tmp_path = Path(job.file_path)
                downloaded_from_r2 = False
            else:
                raise FileNotFoundError(
                    f"Job {job.pk} has no r2_key and no valid file_path."
                )

            converter = GenericCSVConverter()
            result = converter.run(
                source=tmp_path,
                triggered_by=job.uploaded_by,
                dataset_name=f"Upload: {job.original_filename}",
            )

            job.records_saved     = result.get("saved", 0)
            job.records_duplicate = result.get("duplicates", 0)
            job.records_error     = result.get("errors", 0)
            job.error_detail      = result.get("error", "")
            job.finished_at       = timezone.now()
            job.status = {
                "SUCCESS": IngestionJob.Status.SUCCESS,
                "PARTIAL": IngestionJob.Status.PARTIAL,
            }.get(str(result.get("status", "")), IngestionJob.Status.FAILED)

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
            # In production: delete the temp download; R2 copy stays as permanent backup.
            # In local dev: delete the pending file from disk after processing.
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
