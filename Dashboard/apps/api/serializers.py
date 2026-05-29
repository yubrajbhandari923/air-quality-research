"""DRF serializers for the Nepal AQ API."""
from rest_framework import serializers

from apps.sensors.models import MaintenanceLog, Sensor, Site
from apps.readings.models import CanonicalReading, Dataset


class SiteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Site
        fields = [
            "id", "name", "district", "municipality", "province",
            "latitude", "longitude", "elevation_m", "population_estimate",
            "land_use_type", "description",
        ]


class SensorSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True)
    site_district = serializers.CharField(source="site.district", read_only=True)

    class Meta:
        model = Sensor
        fields = [
            "id", "serial_number", "friendly_name", "model", "manufacturer",
            "site", "site_name", "site_district",
            "is_indoor", "power_type", "connectivity_type", "status",
            "installed_at", "decommissioned_at",
        ]


class SensorRegisterSerializer(serializers.ModelSerializer):
    """Used when a new sensor registers itself via the API."""

    class Meta:
        model = Sensor
        fields = [
            "serial_number", "friendly_name", "model", "manufacturer",
            "site", "is_indoor", "power_type", "connectivity_type",
        ]


class CanonicalReadingSerializer(serializers.ModelSerializer):
    sensor_serial = serializers.CharField(source="sensor.serial_number", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)
    effective_value = serializers.FloatField(read_only=True)

    class Meta:
        model = CanonicalReading
        fields = [
            "id",
            "original_ts", "received_ts", "timezone", "interval_seconds",
            "pollutant", "unit",
            "raw_value", "cleaned_value", "effective_value",
            "sensor", "sensor_serial",
            "site", "site_name",
            "is_indoor",
            "source_type",
            "quality_flag", "flag_reason",
            "is_duplicate",
        ]
        read_only_fields = [
            "id", "received_ts", "sensor_serial", "site_name", "effective_value"
        ]


class ReadingSubmitSerializer(serializers.Serializer):
    """For live API submissions from sensors."""

    serial_number = serializers.CharField(max_length=100)
    timestamp = serializers.DateTimeField()
    readings = serializers.ListField(
        child=serializers.DictField(),
        min_length=1,
        max_length=50,
    )

    def validate_readings(self, value):
        valid_pollutants = {c[0] for c in CanonicalReading.Pollutant.choices}
        for r in value:
            if "pollutant" not in r:
                raise serializers.ValidationError("Each reading must have a 'pollutant' key.")
            if r["pollutant"] not in valid_pollutants:
                raise serializers.ValidationError(
                    f"Unknown pollutant '{r['pollutant']}'. Valid: {sorted(valid_pollutants)}"
                )
            if "value" not in r:
                raise serializers.ValidationError("Each reading must have a 'value' key.")
        return value


class DatasetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dataset
        fields = ["id", "name", "source_type", "record_count", "created_at"]


class BatchMeasurementSerializer(serializers.Serializer):
    """One pollutant reading within a single timestamped snapshot."""
    pollutant = serializers.CharField(max_length=10)
    value = serializers.FloatField(allow_null=True)
    unit = serializers.CharField(max_length=20, default="")

    def validate_pollutant(self, value):
        valid = {c[0] for c in CanonicalReading.Pollutant.choices}
        if value.upper() not in valid:
            raise serializers.ValidationError(
                f"Unknown pollutant '{value}'. Valid values: {sorted(valid)}"
            )
        return value.upper()


class BatchSnapshotSerializer(serializers.Serializer):
    """One timestamped set of measurements from a sensor."""
    timestamp = serializers.DateTimeField()
    measurements = BatchMeasurementSerializer(many=True, min_length=1)


class BatchReadingSubmitSerializer(serializers.Serializer):
    """
    Submit multiple timestamped snapshots in a single request.

    Use this for offline sensors catching up after a power/connectivity outage,
    or for scrapers pushing historical data.

    serial_number is optional when the API key is bound to a specific sensor.
    """
    serial_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    readings = BatchSnapshotSerializer(many=True, min_length=1, max_length=10000)


# ── Chart data serializers ────────────────────────────────────────────────────

class TimeSeriesPointSerializer(serializers.Serializer):
    """Single point in a time-series chart."""
    ts = serializers.DateTimeField()
    value = serializers.FloatField(allow_null=True)
    quality_flag = serializers.CharField()


class CompletenessSerializer(serializers.Serializer):
    """Sensor completeness summary."""
    date = serializers.DateField()
    expected = serializers.IntegerField()
    actual = serializers.IntegerField()
    completeness_pct = serializers.FloatField()
