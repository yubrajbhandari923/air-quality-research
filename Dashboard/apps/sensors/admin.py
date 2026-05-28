"""Admin configuration for the sensors app."""
from django.contrib import admin

from .models import CalibrationNote, MaintenanceLog, Sensor, Site


class SensorInline(admin.TabularInline):
    model = Sensor
    extra = 0
    fields = ("serial_number", "friendly_name", "model", "is_indoor", "status")
    show_change_link = True


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "district", "municipality", "latitude", "longitude", "land_use_type")
    list_filter = ("district", "land_use_type")
    search_fields = ("name", "district", "municipality")
    inlines = [SensorInline]


class MaintenanceLogInline(admin.TabularInline):
    model = MaintenanceLog
    extra = 0
    fields = ("event_date", "event_type", "description", "resolved_at", "logged_by")


class CalibrationNoteInline(admin.TabularInline):
    model = CalibrationNote
    fk_name = "sensor"
    extra = 0
    fields = ("calibration_date", "method", "slope_after", "intercept_after")


@admin.register(Sensor)
class SensorAdmin(admin.ModelAdmin):
    list_display = (
        "serial_number", "friendly_name", "model", "site", "is_indoor",
        "status", "power_type", "connectivity_type",
    )
    list_filter = ("status", "is_indoor", "power_type", "connectivity_type", "site")
    search_fields = ("serial_number", "friendly_name", "model")
    inlines = [MaintenanceLogInline, CalibrationNoteInline]


@admin.register(MaintenanceLog)
class MaintenanceLogAdmin(admin.ModelAdmin):
    list_display = ("sensor", "event_type", "event_date", "logged_by", "resolved_at")
    list_filter = ("event_type", "event_date")
    search_fields = ("sensor__serial_number", "description")
