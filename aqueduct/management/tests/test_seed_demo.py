import tempfile
from collections import Counter
from datetime import UTC, datetime
from http import HTTPStatus
from io import StringIO
from pathlib import Path
from unittest import mock

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db.models import Count
from django.test import TestCase, override_settings

from management.key_activity import router_models
from management.models import Org, Request, ServiceAccount, Team, Token, UserGroup, UserProfile

User = get_user_model()
ROUTER_CONFIG = Path(settings.BASE_DIR).parent / "example_router_config.yaml"
SERVICE_ACCOUNTS = 3
SMALL = {
    "teams": 2,
    "small_teams": 1,
    "service_accounts": SERVICE_ACCOUNTS,
    "days": 1,
    "requests_per_day": 500,
}


def seed(**options: object) -> None:
    call_command("seed_demo", force=True, stdout=StringIO(), **(SMALL | options))


@override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=ROUTER_CONFIG)
class SeedDemoTest(TestCase):
    def setUp(self):
        org = Org.objects.create(name="Real Org")
        self.user = User.objects.create_user(username="real", email="real@example.com")
        UserProfile.objects.create(user=self.user, org=org)
        token = Token(user=self.user, name="real key")
        token._set_new_key()
        token.save()
        self.request = Request.objects.create(token=token, model="gpt-4.1-nano", status_code=200)

    def seeded_orgs(self):
        return Org.objects.filter(name__startswith="seed-demo-")

    def test_builds_both_orgs(self):
        seed()

        orgs = {o.name: o for o in self.seeded_orgs()}
        self.assertEqual(sorted(orgs), ["seed-demo-campus", "seed-demo-institute"])
        self.assertEqual(Team.objects.filter(org=orgs["seed-demo-campus"]).count(), 2)
        self.assertEqual(Team.objects.filter(org=orgs["seed-demo-institute"]).count(), 1)
        for team in Team.objects.filter(org__in=orgs.values()):
            members = team.teammembership_set
            self.assertIn(members.count(), range(3, 6), team)
            self.assertGreaterEqual(members.filter(is_admin=True).count(), 1, team)
        for org in orgs.values():
            profiles = UserProfile.objects.filter(org=org)
            org_admins = [p for p in profiles if p.group == UserGroup.ORG_ADMIN]
            self.assertGreaterEqual(len(org_admins), 1, org)
            self.assertEqual(
                ServiceAccount.objects.filter(team__org=org, token__isnull=False).count(),
                SERVICE_ACCOUNTS,
                org,
            )
        personal = (
            Token.objects.filter(service_account=None, user__email__endswith="@seed.invalid")
            .values("user")
            .annotate(n=Count("id"))
        )
        self.assertEqual(
            len(personal), User.objects.filter(email__endswith="@seed.invalid").count()
        )
        self.assertTrue(all(row["n"] in range(1, 4) for row in personal))

    def test_writes_a_plausible_request_log(self):
        seed()

        seeded = Request.objects.exclude(pk=self.request.pk)
        self.assertAlmostEqual(seeded.count(), 500, delta=2)
        self.assertLessEqual(set(seeded.values_list("model", flat=True)), {"", *router_models()})
        statuses = Counter(seeded.values_list("status_code", flat=True))
        self.assertGreater(statuses[200] / seeded.count(), 0.85)
        self.assertTrue(any(code and code >= HTTPStatus.BAD_REQUEST for code in statuses))
        self.assertTrue(seeded.filter(model="").exists())

    def test_same_seed_gives_the_same_log(self):
        def log():
            return Counter(
                Request.objects.exclude(pk=self.request.pk).values_list(
                    "model", "status_code", "input_tokens", "output_tokens", "path"
                )
            )

        with mock.patch(
            "django.utils.timezone.now", return_value=datetime(2026, 3, 4, 15, tzinfo=UTC)
        ):
            seed(seed=3)
            first = log()
            seed(seed=3, reset=True)

        self.assertEqual(log(), first)

    def test_delete_removes_only_seeded_data(self):
        seed()
        call_command("seed_demo", delete=True, force=True, stdout=StringIO())

        self.assertFalse(self.seeded_orgs().exists())
        self.assertFalse(User.objects.filter(email__endswith="@seed.invalid").exists())
        self.assertEqual(list(Request.objects.all()), [self.request])
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_refuses_to_seed_twice(self):
        seed()

        with self.assertRaisesMessage(CommandError, "--reset"):
            seed()

    def test_refuses_without_debug(self):
        with self.assertRaisesMessage(CommandError, "DEBUG"):
            call_command("seed_demo", stdout=StringIO(), **SMALL)
        self.assertFalse(self.seeded_orgs().exists())

    def test_rejects_models_the_router_does_not_serve(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models.yaml"
            models.write_text(
                "models:\n"
                "  - {name: gpt-4.1-nano, kind: chat, weight: 3}\n"
                "  - {name: unserved-model, kind: embedding, weight: 1}\n"
            )
            with self.assertRaisesMessage(CommandError, "unserved-model"):
                seed(models_file=models)

            router = Path(tmp) / "router.yaml"
            call_command(
                "seed_demo", models_file=models, write_router_config=router, stdout=StringIO()
            )
            entries = yaml.safe_load(router.read_text())["model_list"]

        self.assertEqual(
            [(e["model_name"], e["model_info"]["mode"]) for e in entries],
            [("gpt-4.1-nano", "chat"), ("unserved-model", "embedding")],
        )
        self.assertFalse(self.seeded_orgs().exists())
