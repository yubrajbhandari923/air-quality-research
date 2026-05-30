"""
setup_dev_sensors — idempotent Docker dev environment bootstrap.

Creates one outdoor + one indoor emulator sensor with fresh API keys,
then writes the keys to a file so the emulator containers can read them.

Usage:
    python manage.py setup_dev_sensors
    python manage.py setup_dev_sensors --key-file /shared/dev_keys.env
"""
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone


OUTDOOR_SERIAL = "EMULATOR_OUTDOOR"
INDOOR_SERIAL  = "EMULATOR_INDOOR"


class Command(BaseCommand):
    help = "Create emulator sensors + fresh API keys for local Docker dev."

    def add_arguments(self, parser):
        parser.add_argument(
            "--key-file", default="",
            help="Write API keys to this path (shell-sourceable). Default: stdout only.",
        )

    def handle(self, *args, **options):
        from apps.sensors.models import Site, Sensor
        from apps.api.models import APIKey

        # ── Site ──────────────────────────────────────────────────────────────
        site, _ = Site.objects.get_or_create(
            name="Docker Dev Site — Kathmandu",
            defaults={
                "district":  "Kathmandu",
                "latitude":  27.7172,
                "longitude": 85.3240,
            },
        )

        # ── Sensors (idempotent) ───────────────────────────────────────────────
        outdoor, _ = Sensor.objects.get_or_create(
            serial_number=OUTDOOR_SERIAL,
            defaults={
                "friendly_name":      "Emulator Outdoor",
                "model":              "EMULATOR",
                "manufacturer":       "Docker Dev",
                "site":               site,
                "is_indoor":          False,
                "connectivity_type":  "WIFI",
                "status":             "ACTIVE",
                "installed_at":       timezone.now(),
                "installation_notes": "Auto-created by setup_dev_sensors.",
            },
        )

        indoor, _ = Sensor.objects.get_or_create(
            serial_number=INDOOR_SERIAL,
            defaults={
                "friendly_name":      "Emulator Indoor",
                "model":              "EMULATOR",
                "manufacturer":       "Docker Dev",
                "site":               site,
                "is_indoor":          True,
                "connectivity_type":  "WIFI",
                "status":             "ACTIVE",
                "installed_at":       timezone.now(),
                "installation_notes": "Auto-created by setup_dev_sensors.",
            },
        )

        # ── Fresh API keys every run (old dev keys revoked) ───────────────────
        APIKey.objects.filter(
            sensor__in=[outdoor, indoor],
            name__startswith="dev-",
        ).update(is_active=False)

        outdoor_obj, outdoor_raw = APIKey.generate(name="dev-outdoor", role="SENSOR")
        outdoor_obj.sensor = outdoor
        outdoor_obj.save(update_fields=["sensor"])

        indoor_obj, indoor_raw = APIKey.generate(name="dev-indoor", role="SENSOR")
        indoor_obj.sensor = indoor
        indoor_obj.save(update_fields=["sensor"])

        # ── Write key file ────────────────────────────────────────────────────
        key_content = (
            f"OUTDOOR_API_KEY={outdoor_raw}\n"
            f"OUTDOOR_SERIAL={OUTDOOR_SERIAL}\n"
            f"INDOOR_API_KEY={indoor_raw}\n"
            f"INDOOR_SERIAL={INDOOR_SERIAL}\n"
        )

        key_file = options["key_file"]
        if key_file:
            Path(key_file).parent.mkdir(parents=True, exist_ok=True)
            Path(key_file).write_text(key_content)

        self.stdout.write(self.style.SUCCESS("Dev sensors ready:"))
        self.stdout.write(f"  Outdoor  serial={OUTDOOR_SERIAL}  key={outdoor_raw[:12]}...")
        self.stdout.write(f"  Indoor   serial={INDOOR_SERIAL}   key={indoor_raw[:12]}...")
        self.stdout.write(f"  Site     : {site.name}  ({site.pk})")
        self.stdout.write(f"  Outdoor sensor pk: {outdoor.pk}")
        self.stdout.write(f"  Indoor  sensor pk: {indoor.pk}")
        if key_file:
            self.stdout.write(f"  Keys written to: {key_file}")
