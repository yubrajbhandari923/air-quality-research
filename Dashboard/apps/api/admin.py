"""Admin for API keys."""
from django.contrib import admin
from .models import APIKey


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "is_active", "last_used", "expires_at", "created_at")
    list_filter = ("role", "is_active")
    search_fields = ("name",)
    readonly_fields = ("key", "last_used", "created_at", "updated_at")

    actions = ["deactivate_keys"]

    def deactivate_keys(self, request, queryset):
        queryset.update(is_active=False)
    deactivate_keys.short_description = "Deactivate selected API keys"
