"""Development settings — verbose logging, SQLite fallback, no PostGIS required."""
from pathlib import Path
from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Always use SQLite in development unless DATABASE_URL env var explicitly set
# to a postgres:// URL (not postgis://) by the developer.
import os as _os
_db_url = _os.environ.get("DATABASE_URL", "")
if not _db_url or "postgis" in _db_url:
    # Ignore any postgis:// URL — that needs GDAL which may not be installed locally
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "nepal_aq_dev.sqlite3",  # noqa: F405
        }
    }

# More permissive email for dev
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "apps.ingestion": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "apps.api": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

