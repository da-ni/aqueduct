import tempfile
from collections import Counter
from datetime import UTC, datetime
from http import HTTPStatus
from io import StringIO
from pathlib import Path
from unittest import mock

import yaml
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.models import Count
from django.test import TestCase, override_settings

from management.key_activity import router_models
from management.management.commands.seed_demo import Builder, capped
from management.models import Org, Request, ServiceAccount, Team, Token, UserGroup, UserProfile

User = get_user_model()
SERVICE_ACCOUNTS = 3
SMALL = {
    "teams": 2,
    "small_teams": 1,
    "service_accounts": SERVICE_ACCOUNTS,
    "days": 1,
    "requests_per_day": 500,
}


def seed(**options: object) -> None:
    call_command("seed_demo", force=True, stdout=StringIO(), stderr=StringIO(), **(SMALL | options))


def delete() -> None:
    call_command("seed_demo", delete=True, force=True, stdout=StringIO(), stderr=StringIO())


def router_config(path) -> override_settings:
    return override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=path)


class CappedTest(TestCase):
    def test_no_weight_exceeds_its_share(self):
        weights = capped([1000.0, 50.0, *[1.0] * 98], 0.03)

        self.assertLessEqual(max(weights), 0.0301 * sum(weights))
        self.assertEqual(weights[2:], [1.0] * 98)

    def test_leaves_weights_alone_when_no_cap_fits(self):
        self.assertEqual(capped([5.0, 1.0], 0.3), [5.0, 1.0])


class SeedDemoTest(TestCase):
    def setUp(self):
        served = Path(self.enterContext(tempfile.TemporaryDirectory())) / "router.yaml"
        call_command("seed_demo", write_router_config=served, stdout=StringIO())
        self.enterContext(router_config(served))
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
        self.assertAlmostEqual(seeded.count(), 500 / 0.95, delta=2)
        self.assertAlmostEqual(seeded.exclude(model="").count(), 500, delta=25)
        self.assertLessEqual(set(seeded.values_list("model", flat=True)), {"", *router_models()})
        statuses = Counter(seeded.values_list("status_code", flat=True))
        self.assertGreater(statuses[200] / seeded.count(), 0.9)
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
        delete()

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
            [e["model_info"] for e in entries], [{"mode": "chat"}, {"mode": "embedding"}]
        )
        self.assertFalse(self.seeded_orgs().exists())

    def test_router_config_links_each_default_model_card_and_quantization(self):
        with tempfile.TemporaryDirectory() as tmp:
            router = Path(tmp) / "router.yaml"
            call_command("seed_demo", write_router_config=router, stdout=StringIO())
            entries = yaml.safe_load(router.read_text())["model_list"]

        info = {e["model_name"]: e["model_info"] for e in entries}
        self.assertEqual(
            info["Qwen/Qwen3.8-27B-FP8"],
            {
                "mode": "chat",
                "model_card_url": "https://huggingface.co/Qwen/Qwen3.8-27B-FP8",
                "quantization": "FP8",
            },
        )
        self.assertEqual(info["QuantTrio/Qwen3.6-35B-A3B-AWQ"]["quantization"], "AWQ")
        self.assertNotIn("quantization", info["zai-org/GLM-5.3"])
        for name, model_info in info.items():
            with self.subTest(model=name):
                self.assertEqual(model_info["model_card_url"], f"https://huggingface.co/{name}")
                self.assertFalse({"max_tokens", "max_input_tokens"} & model_info.keys())

    def test_model_figures_shape_its_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / "models.yaml"
            models.write_text(
                "models:\n"
                "  - {name: tiny, kind: chat, weight: 1, input_tokens: 2000, output_tokens: 50,\n"
                "     cached_share: 0, latency_ms: [100, 200]}\n"
            )
            router = Path(tmp) / "router.yaml"
            call_command(
                "seed_demo", models_file=models, write_router_config=router, stdout=StringIO()
            )
            with router_config(router):
                seed(models_file=models)

        served = Request.objects.filter(model="tiny", status_code=HTTPStatus.OK)
        inputs = sorted(served.values_list("input_tokens", flat=True))
        latencies = sorted(served.values_list("response_time_ms", flat=True))
        self.assertIn(inputs[len(inputs) // 2], range(1000, 4000))
        self.assertIn(latencies[len(latencies) // 2], range(70, 140))
        self.assertFalse(served.filter(cached_input_tokens__gt=0).exists())

    def test_failed_reset_keeps_the_earlier_seed(self):
        seed()
        before = Request.objects.count()

        with (
            mock.patch.object(Builder, "storage", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            seed(reset=True)

        self.assertEqual(Request.objects.count(), before)
        self.assertEqual(self.seeded_orgs().count(), 2)

    def test_delete_needs_no_router_config(self):
        seed()

        with router_config("/nonexistent/router.yaml"):
            delete()

        self.assertFalse(self.seeded_orgs().exists())

    def test_empty_router_config_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "router.yaml"
            empty.write_text("model_list: []\n")
            with (
                router_config(empty),
                self.assertRaisesMessage(CommandError, "Not in the router config"),
            ):
                seed()

    def test_delete_refuses_when_a_real_user_is_in_a_seeded_org(self):
        seed()
        UserProfile.objects.filter(user=self.user).update(org=self.seeded_orgs().first())

        with self.assertRaisesMessage(CommandError, "real@example.com"):
            delete()

        self.assertEqual(self.seeded_orgs().count(), 2)

    def test_force_names_the_database_it_writes(self):
        err = StringIO()
        call_command("seed_demo", force=True, stdout=StringIO(), stderr=err, **SMALL)

        self.assertIn("DEBUG is off", err.getvalue())
        self.assertIn(connection.vendor, err.getvalue())

    def test_unknown_time_zone_is_a_clear_error(self):
        with self.assertRaisesMessage(CommandError, "Mars/Olympus"):
            seed(local_time_zone="Mars/Olympus")
