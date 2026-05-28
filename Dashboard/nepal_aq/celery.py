"""Celery application for Nepal Air Quality Dashboard."""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nepal_aq.settings.development")

app = Celery("nepal_aq")

# Use Django settings with CELERY_ prefix
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks in all installed apps
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    """Simple debug task to verify Celery is working."""
    print(f"Request: {self.request!r}")
