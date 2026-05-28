"""
Analysis script system.

Admins and maintainers write Python functions here that run against
the live database. Results are cached in the DB and displayed on the
analysis page.

Two run modes:
  CONTINUOUS — re-run every `interval_minutes`; result auto-refreshes
  CACHED     — run once (or on demand); result shown until re-run
"""
import json
import traceback
from django.db import models
from django.utils import timezone
from apps.core.models import TimeStampedModel


class AnalysisScript(TimeStampedModel):
    class RunMode(models.TextChoices):
        CONTINUOUS = "CONTINUOUS", "Continuous (auto-refresh)"
        CACHED = "CACHED", "Cached (run once)"

    class ChartType(models.TextChoices):
        TIMESERIES = "TIMESERIES", "Time Series"
        BAR = "BAR", "Bar Chart"
        TABLE = "TABLE", "Data Table"
        METRIC = "METRIC", "Single Metric"
        SCATTER = "SCATTER", "Scatter Plot"

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    code = models.TextField(
        help_text=(
            "Python code. Must define a function named 'run(db)' that returns a dict.\n"
            "The 'db' argument gives access to Django ORM (CanonicalReading, Sensor, Site).\n"
            "Return dict keys: 'title', 'labels', 'datasets', 'summary' (optional), 'table' (optional).\n"
            "Example:\n"
            "def run(db):\n"
            "    from django.utils import timezone\n"
            "    from datetime import timedelta\n"
            "    qs = db.CanonicalReading.objects.filter(\n"
            "        pollutant='PM25', is_duplicate=False,\n"
            "        original_ts__gte=timezone.now() - timedelta(days=7)\n"
            "    ).values_list('original_ts', 'raw_value')[:200]\n"
            "    return {\n"
            "        'title': 'Last 7 days PM2.5',\n"
            "        'labels': [str(r[0]) for r in qs],\n"
            "        'datasets': [{'label': 'PM2.5', 'data': [r[1] for r in qs]}]\n"
            "    }"
        )
    )
    run_mode = models.CharField(max_length=20, choices=RunMode.choices, default=RunMode.CACHED)
    chart_type = models.CharField(max_length=20, choices=ChartType.choices, default=ChartType.TIMESERIES)
    interval_minutes = models.PositiveIntegerField(
        default=60,
        help_text="For CONTINUOUS mode: re-run every N minutes.",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0, help_text="Display order on the analysis page.")

    # Cached execution results
    cached_result = models.TextField(blank=True, help_text="JSON result from last successful run.")
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    run_duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Analysis Script"
        verbose_name_plural = "Analysis Scripts"

    def __str__(self):
        return f"{self.name} ({self.get_run_mode_display()})"

    def execute(self):
        """Run the script and cache the result. Returns (result_dict, error_str)."""
        import time
        from apps.readings.models import CanonicalReading as _CR
        from apps.sensors.models import Sensor as _Sensor, Site as _Site

        class DBProxy:
            CanonicalReading = _CR
            Sensor = _Sensor
            Site = _Site

        namespace = {}
        try:
            exec(self.code, namespace)  # noqa: S102
        except SyntaxError as e:
            error = f"Syntax error: {e}"
            self.last_error = error
            self.last_run_at = timezone.now()
            self.save(update_fields=["last_error", "last_run_at"])
            return None, error

        run_fn = namespace.get("run")
        if not callable(run_fn):
            error = "Script must define a function named 'run(db)'."
            self.last_error = error
            self.last_run_at = timezone.now()
            self.save(update_fields=["last_error", "last_run_at"])
            return None, error

        t0 = time.monotonic()
        try:
            result = run_fn(DBProxy())
            duration_ms = int((time.monotonic() - t0) * 1000)
            self.cached_result = json.dumps(result, default=str)
            self.last_run_at = timezone.now()
            self.last_error = ""
            self.run_duration_ms = duration_ms
            self.save(update_fields=["cached_result", "last_run_at", "last_error", "run_duration_ms"])
            return result, ""
        except Exception:
            error = traceback.format_exc()
            self.last_error = error
            self.last_run_at = timezone.now()
            self.save(update_fields=["last_error", "last_run_at"])
            return None, error

    @property
    def result_dict(self):
        if not self.cached_result:
            return None
        try:
            return json.loads(self.cached_result)
        except (json.JSONDecodeError, ValueError):
            return None

    @property
    def needs_refresh(self):
        if self.run_mode != self.RunMode.CONTINUOUS:
            return False
        if not self.last_run_at:
            return True
        age_minutes = (timezone.now() - self.last_run_at).total_seconds() / 60
        return age_minutes >= self.interval_minutes


class DownloadConfig(models.Model):
    """Admin-configurable limits for CSV downloads."""

    max_rows_public = models.PositiveIntegerField(
        default=500,
        help_text="Max rows any unauthenticated or PUBLIC-role user can download.",
    )
    max_rows_researcher = models.PositiveIntegerField(
        default=50000,
        help_text="Max rows a RESEARCHER can download per request.",
    )
    max_rows_admin = models.PositiveIntegerField(
        default=500000,
        help_text="Max rows an ADMIN or MAINTAINER can download per request.",
    )
    public_days_limit = models.PositiveIntegerField(
        default=7,
        help_text="Public users can only download the last N days of data.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Download Configuration"
        verbose_name_plural = "Download Configuration"

    def __str__(self):
        return "Download Configuration"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
