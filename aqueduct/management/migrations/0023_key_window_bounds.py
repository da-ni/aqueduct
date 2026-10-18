from django.db import migrations, models

HOURLY = "org-hours"


def copy_window_bounds(apps, schema_editor):
    KeyUsageWindow = apps.get_model("management", "KeyUsageWindow")
    UsageRollupState = apps.get_model("management", "UsageRollupState")
    for state in UsageRollupState.objects.exclude(name=HOURLY):
        days = int(state.name.removeprefix("keys-").removesuffix("d"))
        KeyUsageWindow.objects.filter(days=days).update(
            window_start=state.window_start, sealed_until=state.sealed_until
        )
    KeyUsageWindow.objects.filter(
        models.Q(window_start=None) | models.Q(sealed_until=None)
    ).delete()
    UsageRollupState.objects.exclude(name=HOURLY).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("management", "0022_delete_cross_org_memberships"),
    ]

    operations = [
        migrations.AddField(
            model_name="keyusagewindow",
            name="window_start",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name="keyusagewindow",
            name="sealed_until",
            field=models.DateTimeField(null=True),
        ),
        migrations.RunPython(copy_window_bounds, migrations.RunPython.noop, elidable=True),
        migrations.AlterField(
            model_name="keyusagewindow",
            name="window_start",
            field=models.DateTimeField(),
        ),
        migrations.AlterField(
            model_name="keyusagewindow",
            name="sealed_until",
            field=models.DateTimeField(),
        ),
        migrations.RemoveField(
            model_name="usagerollupstate",
            name="window_start",
        ),
    ]
