"""WSGI config for Nepal Air Quality Dashboard."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nepal_aq.settings.production")

application = get_wsgi_application()
