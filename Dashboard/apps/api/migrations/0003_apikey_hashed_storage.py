"""
Migration: replace plain-text `key` field with `prefix` + `hashed_key`.

For any existing rows the migration:
  1. Adds prefix and hashed_key as nullable columns
  2. Populates them from the existing plain-text key
  3. Removes the old key column

Existing API keys stop working after this migration because the raw key
was never stored elsewhere. Affected users must regenerate their keys via
the Django admin (APIKey.generate()).
"""
from django.db import migrations, models
from django.contrib.auth.hashers import make_password


def populate_prefix_and_hash(apps, schema_editor):
    APIKey = apps.get_model("api", "APIKey")
    for obj in APIKey.objects.all():
        raw = obj.key or ""
        obj.prefix = raw[:8] if len(raw) >= 8 else raw.ljust(8, "0")
        obj.hashed_key = make_password(raw) if raw else make_password("REVOKED-" + str(obj.pk))
        obj.save(update_fields=["prefix", "hashed_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0002_initial"),
    ]

    operations = [
        # 1. Add new columns (nullable so existing rows don't break)
        migrations.AddField(
            model_name="apikey",
            name="prefix",
            field=models.CharField(
                max_length=8,
                db_index=True,
                null=True,
                help_text="First 8 chars of key (plain text, for lookup)",
            ),
        ),
        migrations.AddField(
            model_name="apikey",
            name="hashed_key",
            field=models.CharField(
                max_length=256,
                null=True,
                help_text="PBKDF2-SHA256 hash of the full key",
            ),
        ),
        # 2. Populate from existing plain-text keys
        migrations.RunPython(populate_prefix_and_hash, migrations.RunPython.noop),
        # 3. Make non-nullable now that all rows have values
        migrations.AlterField(
            model_name="apikey",
            name="prefix",
            field=models.CharField(
                max_length=8,
                db_index=True,
                help_text="First 8 chars of key (plain text, for lookup)",
            ),
        ),
        migrations.AlterField(
            model_name="apikey",
            name="hashed_key",
            field=models.CharField(
                max_length=256,
                help_text="PBKDF2-SHA256 hash of the full key",
            ),
        ),
        # 4. Drop the old plain-text key column
        migrations.RemoveField(
            model_name="apikey",
            name="key",
        ),
    ]
