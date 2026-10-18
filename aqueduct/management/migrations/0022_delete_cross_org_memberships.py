import logging

from django.db import migrations
from django.db.models import Exists, F, OuterRef

log = logging.getLogger("aqueduct")


def delete_cross_org_memberships(apps, schema_editor):
    TeamMembership = apps.get_model("management", "TeamMembership")
    ServiceAccount = apps.get_model("management", "ServiceAccount")
    cross_org = TeamMembership.objects.exclude(team__org_id=F("user_profile__org_id"))
    rows = list(
        cross_org.values_list("user_profile__user__email", "user_profile_id", "team_id", "is_admin")
    )
    if not rows:
        return
    log.warning("Deleting %d team memberships in teams of another org.", len(rows))
    for email, profile_id, team_id, is_admin in rows:
        log.warning(
            "Deleting membership of %s (profile %s) in team %s, admin: %s.",
            email, profile_id, team_id, is_admin,
        )
    orphaned = ServiceAccount.objects.filter(
        Exists(
            cross_org.filter(
                team_id=OuterRef("team_id"), user_profile__user_id=OuterRef("token__user_id")
            )
        )
    ).values_list("token__user__email", "team_id", "pk", "name")
    for email, team_id, pk, name in orphaned:
        log.warning(
            "%s leaves team %s but owns its service account %s (%s); transfer it.",
            email, team_id, pk, name,
        )
    cross_org.delete()


class Migration(migrations.Migration):
    dependencies = [("management", "0021_file_batch_models")]

    operations = [
        migrations.RunPython(delete_cross_org_memberships, migrations.RunPython.noop),
    ]
