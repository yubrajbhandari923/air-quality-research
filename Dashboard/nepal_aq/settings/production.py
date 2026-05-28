"""Production settings — security hardened, PostGIS optional, S3-ready."""
from .base import *  # noqa: F401, F403

DEBUG = False

# In production, set DJANGO_ALLOWED_HOSTS env var
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True

# Use PostGIS when GDAL is available (self-hosted / Docker), fall back to
# plain psycopg2 on platforms like Render free tier that lack GDAL.
import importlib  # noqa: E402
DATABASES = {
    "default": env.db("DATABASE_URL"),  # noqa: F405
}
if importlib.util.find_spec("osgeo") is not None:
    DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"
else:
    DATABASES["default"]["ENGINE"] = "django.db.backends.postgresql"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "apps.ingestion": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "apps.api": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
