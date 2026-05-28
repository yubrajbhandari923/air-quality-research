"""Admin configuration for the users app."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "email", "role", "affiliation", "is_active", "date_joined")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("username", "email", "first_name", "last_name", "affiliation")
    ordering = ("username",)

    fieldsets = UserAdmin.fieldsets + (
        (
            "Dashboard Profile",
            {"fields": ("role", "affiliation", "bio", "avatar")},
        ),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "Dashboard Profile",
            {"fields": ("role", "affiliation")},
        ),
    )
