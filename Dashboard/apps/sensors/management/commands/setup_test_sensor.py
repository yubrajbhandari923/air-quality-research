"""
Management command: create a test sensor + SENSOR API key for local dev/testing.

Usage:
    python manage.py setup_test_sensor
    python manage.py setup_test_sensor --indoor --serial TESTINDOOR01 --name "Test Indoor"
    python manage.py setup_test_sensor --new-key   # regenerate key for existing sensor

The printed API key is shown only once. Feed it to tools/dummy_sensor.py.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Create a test sensor + bound SENSOR API key (dev/testing only)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--serial", default="TESTSENSOR001",
            help="Sensor serial number (default: TESTSENSOR001)",
        )
        parser.add_argument(
            "--name", default="Test Sensor",
            help="Friendly name shown in the dashboard (default: 'Test Sensor')",
        )
        parser.add_argument(
            "--indoor", action="store_true",
            help="Create as indoor sensor (adds CO₂/TVOC to emitted data)",
        )
        parser.add_argument(
            "--lat", type=float, default=27.7172,
            help="Site latitude  (default: Kathmandu centre)",
        )
        parser.add_argument(
            "--lon", type=float, default=85.3240,
            help="Site longitude (default: Kathmandu centre)",
        )
        parser.add_argument(
            "--site", default="Test Site — Kathmandu",
            help="Site name (created if it doesn't exist)",
        )
        parser.add_argument(
            "--district", default="Kathmandu",
            help="District name for the site",
        )
        parser.add_argument(
            "--new-key", action="store_true", dest="new_key",
            help="Generate a fresh API key even if the sensor already has one",
        )

    # ─────────────────────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        from apps.sensors.models import Site, Sensor
        from apps.api.models import APIKey

        W = self.style.WARNING
        S = self.style.SUCCESS
        E = self.style.ERROR

        self.stdout.write("")
        self.stdout.write(S("━" * 60))
        self.stdout.write(S("  Nepal AQ — Test Sensor Setup"))
        self.stdout.write(S("━" * 60))

        # ── Site ──────────────────────────────────────────────────────────────
        site, site_created = Site.objects.get_or_create(
            name=options["site"],
            defaults={
                "district":  options["district"],
                "latitude":  options["lat"],
                "longitude": options["lon"],
            },
        )
        self.stdout.write(
            f"  Site     : {site.name}  "
            f"({site.latitude:.4f}, {site.longitude:.4f})"
            f"  {'[created]' if site_created else '[existing]'}"
        )

        # ── Sensor ────────────────────────────────────────────────────────────
        sensor, sensor_created = Sensor.objects.get_or_create(
            serial_number=options["serial"],
            defaults={
                "friendly_name":     options["name"],
                "model":             "EMULATOR",
                "manufacturer":      "Test Device",
                "site":              site,
                "is_indoor":         options["indoor"],
                "connectivity_type": "WIFI",
                "status":            "ACTIVE",
                "installed_at":      timezone.now(),
                "installation_notes": "Created by setup_test_sensor management command.",
            },
        )
        sensor_type = "Indoor" if sensor.is_indoor else "Outdoor"
        self.stdout.write(
            f"  Sensor   : {sensor.display_name}  /  {sensor.serial_number}"
            f"  [{sensor_type}]"
            f"  {'[created]' if sensor_created else '[existing]'}"
        )

        # ── API key ───────────────────────────────────────────────────────────
        raw_key = None

        if not options["new_key"] and not sensor_created:
            existing = (
                APIKey.objects
                .filter(sensor=sensor, role="SENSOR", is_active=True)
                .order_by("-created_at")
                .first()
            )
            if existing:
                self.stdout.write("")
                self.stdout.write(W(
                    f"  Sensor already has an active API key (prefix: {existing.prefix}…).\n"
                    f"  The raw key was shown only at creation time.\n"
                    f"  Run with --new-key to generate a fresh one (old key stays valid)."
                ))
                self.stdout.write(S("━" * 60))
                self._print_run_hint(options, "<your-saved-key>", sensor)
                return

        key_obj, raw_key = APIKey.generate(
            name=f"Test key — {sensor.display_name}",
            role="SENSOR",
        )
        key_obj.sensor = sensor
        key_obj.save(update_fields=["sensor"])

        # ── Result ────────────────────────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(S("  ✓ Ready! Copy the key below — it won't be shown again."))
        self.stdout.write(S("━" * 60))
        self.stdout.write(f"  Serial : {sensor.serial_number}")
        self.stdout.write(f"  Type   : {sensor_type}")
        self.stdout.write(f"  Site   : {site.name}")
        self.stdout.write("")
        self.stdout.write(f"  API Key: {W(raw_key)}")
        self.stdout.write(S("━" * 60))
        self._print_run_hint(options, raw_key, sensor)

    def _print_run_hint(self, options, key, sensor):
        indoor_flag = "--indoor " if sensor.is_indoor else ""
        self.stdout.write("")
        self.stdout.write("  Start the emulator:")
        self.stdout.write("")
        self.stdout.write(f"    python tools/dummy_sensor.py \\")
        self.stdout.write(f"        --url http://localhost:8000 \\")
        self.stdout.write(f"        --key {key} \\")
        self.stdout.write(f"        --serial {sensor.serial_number} {indoor_flag}\\")
        self.stdout.write(f"        --interval 10")
        self.stdout.write("")
        self.stdout.write(
            f"  View sensor at: "
            f"http://localhost:8000/sensors/{sensor.pk}/"
        )
        self.stdout.write("")
