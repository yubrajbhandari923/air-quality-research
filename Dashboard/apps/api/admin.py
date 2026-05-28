"""Admin for API keys."""
from django.contrib import admin
from django.utils.html import format_html

from .models import APIKey


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "prefix_display", "role", "is_active", "last_used", "expires_at", "created_at")
    list_filter = ("role", "is_active")
    search_fields = ("name", "prefix")
    readonly_fields = ("prefix", "hashed_key", "last_used", "created_at", "updated_at")
    exclude = ()

    actions = ["deactivate_keys"]

    def prefix_display(self, obj):
        return format_html("<code>{}…</code>", obj.prefix)
    prefix_display.short_description = "Key prefix"

    def deactivate_keys(self, request, queryset):
        queryset.update(is_active=False)
    deactivate_keys.short_description = "Deactivate selected API keys"

    def has_change_permission(self, request, obj=None):
        # Prevent editing the hash directly
        return super().has_change_permission(request, obj)
