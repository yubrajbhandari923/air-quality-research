"""
Base Django settings for Nepal Air Quality Dashboard.

All environment-specific settings should be in development.py or production.py.
"""
import os
from pathlib import Path

import environ

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
# Read .env if it exists (real secrets); never auto-read .env.example
_dotenv = os.path.join(BASE_DIR, ".env")
if os.path.exists(_dotenv):
    environ.Env.read_env(_dotenv)

# ── Security ──────────────────────────────────────────────────────────────────
SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-key-change-in-production")
DEBUG = env("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# ── Application definition ────────────────────────────────────────────────────
INSTALLED_APPS = [
    # Django contrib
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "corsheaders",
    "axes",
    # Local apps
    "apps.core",
    "apps.users",
    "apps.sensors",
    "apps.readings",
    "apps.ingestion",
    "apps.api",
    "apps.dashboard",
    "apps.exposure",
    "apps.documents",
    "apps.analysis",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "nepal_aq.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.dashboard.context_processors.dashboard_context",
            ],
        },
    },
]

WSGI_APPLICATION = "nepal_aq.wsgi.application"

# ── Database ──────────────────────────────────────────────────────────────────
# Default to SQLite. Set DATABASE_URL=postgres://... in .env for PostgreSQL,
# or DATABASE_URL=postgis://... for PostGIS (requires GDAL + production.py).
_db_url_raw = env("DATABASE_URL", default="")
if _db_url_raw and "postgis" not in _db_url_raw:
    DATABASES = {"default": env.db("DATABASE_URL")}
elif _db_url_raw and "postgis" in _db_url_raw:
    # PostGIS requires production.py which sets the correct engine
    DATABASES = {"default": env.db("DATABASE_URL")}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "nepal_aq.sqlite3",
            "OPTIONS": {"timeout": 30},
        }
    }

# ── Auth ──────────────────────────────────────────────────────────────────────
AUTH_USER_MODEL = "users.CustomUser"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/users/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# ── django-axes (brute-force login protection) ─────────────────────────────────
AXES_FAILURE_LIMIT = 5          # lock after 5 failed attempts
AXES_COOLOFF_TIME = 1           # lock for 1 hour
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]  # lock per IP+username combo
AXES_RESET_ON_SUCCESS = True    # clear failure count on successful login
AXES_ENABLE_ADMIN = True        # show lockout records in Django admin
AXES_VERBOSE = False

# ── Internationalization ──────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kathmandu"
USE_I18N = True
USE_TZ = True  # All DateTimeFields are timezone-aware

# ── Static & Media ────────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = Path(env("STATIC_ROOT", default=str(BASE_DIR / "staticfiles")))
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "mediafiles")))

# ── Default primary key ───────────────────────────────────────────────────────
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── REST Framework ────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.api.authentication.APIKeyAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "300/min",
    },
}

# ── CORS ──────────────────────────────────────────────────────────────────────
# In production set CORS_ALLOWED_ORIGINS to your Render URL, e.g.:
#   CORS_ALLOWED_ORIGINS=https://nepal-aq-dashboard.onrender.com
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000", "http://localhost:8000"],
)

# ── Cloudflare R2 (S3-compatible archive + CSV upload storage) ────────────────
# Required in production. Leave blank to disable R2 (uploads/archiving skipped).
R2_ACCOUNT_ID        = env("R2_ACCOUNT_ID",        default="")
R2_ACCESS_KEY_ID     = env("R2_ACCESS_KEY_ID",     default="")
R2_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY", default="")
R2_BUCKET_NAME       = env("R2_BUCKET_NAME",       default="nepal-aq")

# ── Archive settings ──────────────────────────────────────────────────────────
# Raw readings older than this many days are archived to R2 and deleted from Postgres.
# Only used when AQ_INGESTION["db_raw_enabled"] = True.
ARCHIVE_AFTER_DAYS = env.int("ARCHIVE_AFTER_DAYS", default=90)

# ── Ingestion storage settings ────────────────────────────────────────────────
# Controls where raw minute-level readings land and what aggregates go to Postgres.
#
# Recommended production config (set via Render env vars):
#   AQ_DB_RAW_ENABLED=false          → raw readings go to R2 parquet only (saves ~95% DB space)
#   AQ_DB_RAW_RECENT_DAYS=0          → (ignored when db_raw_enabled=False)
#   AQ_DB_AGGREGATES=hourly,daily    → only hourly+daily in DB (drop tenmin to save more)
#   AQ_R2_PARTITION_BY=month         → one parquet file per sensor per month in R2
#
AQ_INGESTION = {
    # Store raw minute readings in PostgreSQL (CanonicalReading table).
    # False = readings go straight to R2 parquet; DB holds aggregates only.
    "db_raw_enabled": env.bool("AQ_DB_RAW_ENABLED", default=False),

    # When db_raw_enabled=True: only keep readings from the last N days in DB.
    # 0 = keep everything (only safe with a large DB plan).
    "db_raw_recent_days": env.int("AQ_DB_RAW_RECENT_DAYS", default=0),

    # Which aggregate levels to compute and persist in DB.
    # Remove "tenmin" to save ~30% more DB space; keep "hourly" and "daily" for the dashboard.
    "db_aggregates": env.list("AQ_DB_AGGREGATES", default=["tenmin", "hourly", "daily"]),

    # R2 parquet partition granularity: "month" (default), "week", or "day".
    # "month" gives one ~2-5 MB file per sensor per month — easy to download and inspect.
    "r2_partition_by": env("AQ_R2_PARTITION_BY", default="month"),
}
