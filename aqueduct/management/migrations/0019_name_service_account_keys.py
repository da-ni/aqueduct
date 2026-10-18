from django.db import migrations
from django.db.models import OuterRef, Subquery


def name_after_account(apps, schema_editor):
    Token = apps.get_model("management", "Token")
    ServiceAccount = apps.get_model("management", "ServiceAccount")
    account = ServiceAccount.objects.filter(pk=OuterRef("service_account_id"))
    Token.objects.filter(service_account__isnull=False).update(
        name=Subquery(account.values("name")[:1])
    )


class Migration(migrations.Migration):
    dependencies = [("management", "0018_userprofile_language")]

    operations = [migrations.RunPython(name_after_account, migrations.RunPython.noop)]
