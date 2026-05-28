"""Admin configuration for the readings app."""
from django.contrib import admin

from .models import CanonicalReading, DailyAggregate, Dataset, IngestionLog


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ("name", "source_type", "record_count", "created_at", "uploaded_by")
    list_filter = ("source_type",)
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(CanonicalReading)
class CanonicalReadingAdmin(admin.ModelAdmin):
    list_display = (
        "original_ts", "sensor", "pollutant", "raw_value", "unit",
        "quality_flag", "is_indoor", "is_duplicate", "source_type",
    )
    list_filter = ("pollutant", "quality_flag", "is_indoor", "is_duplicate", "source_type", "sensor")
    search_fields = ("sensor__serial_number", "sensor__friendly_name", "flag_reason")
    date_hierarchy = "original_ts"
    readonly_fields = ("received_ts", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        # Prevent accidental deletion of raw data
        return request.user.is_superuser


@admin.register(IngestionLog)
class IngestionLogAdmin(admin.ModelAdmin):
    list_display = (
        "source_name", "status", "records_saved", "records_duplicate", "records_error",
        "created_at",
    )
    list_filter = ("status", "source_name")
    readonly_fields = ("created_at", "updated_at")
