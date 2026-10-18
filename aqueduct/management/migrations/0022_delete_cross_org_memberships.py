from django.db import migrations
from django.db.models import F


def delete_cross_org_memberships(apps, schema_editor):
    TeamMembership = apps.get_model("management", "TeamMembership")
    TeamMembership.objects.exclude(team__org_id=F("user_profile__org_id")).delete()


class Migration(migrations.Migration):
    dependencies = [("management", "0021_file_batch_models")]

    operations = [
        migrations.RunPython(delete_cross_org_memberships, migrations.RunPython.noop),
    ]
