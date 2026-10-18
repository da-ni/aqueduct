import tempfile
from http import HTTPStatus
from pathlib import Path

import yaml
from django.test import TestCase, override_settings
from django.urls import reverse

from management.admin import TeamAdminForm, UserProfileAdminForm
from management.availability import Reach, configured_models
from management.models import Org, Team, Token, UserGroup
from management.tests.helpers import make_token
from management.tests.test_organization_page import TEST_MCP_CONFIG, mcp_config
from management.tests.test_teams_pages import TeamsPagesTestBase

ROUTER_CONFIG = Path(__file__).resolve().parents[3] / "example_router_config.yaml"


def posted(form) -> dict:
    data = {name: form[name].value() for name in form.fields}
    data |= {f"{name}-shown": "1" for name in form.fields if name.startswith("available_")}
    return {name: value for name, value in data.items() if value not in (None, False)}


@override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=ROUTER_CONFIG)
class TeamAvailabilityAdminFormTest(TestCase):
    def setUp(self):
        self.team = Team.objects.create(name="Whale", org=Org.objects.create(name="E060"))

    def test_every_box_starts_checked(self):
        form = TeamAdminForm(instance=self.team)
        self.assertEqual(form["available_models"].value(), configured_models())

    def test_saving_unchanged_keeps_what_is_stored(self):
        self.team.excluded_models = ["retired-model", "whisper-1"]
        self.team.save()
        with mcp_config(TEST_MCP_CONFIG):
            self.team.excluded_mcp_servers = ["retired-server", "aqueduct"]
            self.team.save()
            form = TeamAdminForm(posted(TeamAdminForm(instance=self.team)), instance=self.team)
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
        self.team.refresh_from_db()
        self.assertEqual(self.team.excluded_models, ["retired-model", "whisper-1"])
        self.assertEqual(self.team.excluded_mcp_servers, ["retired-server", "aqueduct"])

    def test_clearing_a_box_makes_it_unavailable_and_unknown_names_stay(self):
        self.team.excluded_models = ["retired-model"]
        self.team.save()
        data = posted(TeamAdminForm(instance=self.team))
        data["available_models"].remove("dall-e-2")
        form = TeamAdminForm(data, instance=self.team)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.team.refresh_from_db()
        self.assertEqual(self.team.excluded_models, ["retired-model", "dall-e-2"])

    def test_a_list_the_form_did_not_render_stays_as_stored(self):
        self.team.excluded_models = ["whisper-1"]
        self.team.save()
        data = posted(TeamAdminForm(instance=self.team))
        del data["available_models"], data["available_models-shown"]
        form = TeamAdminForm(data, instance=self.team)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.team.refresh_from_db()
        self.assertEqual(self.team.excluded_models, ["whisper-1"])

    def test_a_cleared_list_makes_every_model_unavailable(self):
        data = posted(TeamAdminForm(instance=self.team))
        del data["available_models"]
        form = TeamAdminForm(data, instance=self.team)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.team.refresh_from_db()
        self.assertEqual(self.team.excluded_models, configured_models())


class RepeatedModelNameTest(TestCase):
    def setUp(self):
        self.team = Team.objects.create(name="Whale", org=Org.objects.create(name="E060"))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = Path(directory.name) / "router.yaml"
        entries = [{"model_name": n, "litellm_params": {"model": f"openai/{n}"}} for n in "aab"]
        config.write_text(yaml.safe_dump({"model_list": entries}))
        settings = override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=str(config))
        settings.enable()
        self.addCleanup(settings.disable)

    def test_a_repeated_name_is_one_box(self):
        form = TeamAdminForm(instance=self.team)
        self.assertEqual(form["available_models"].value(), ["a", "b"])
        data = posted(form)
        data["available_models"] = ["b"]
        form = TeamAdminForm(data, instance=self.team)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.team.refresh_from_db()
        self.assertEqual(self.team.excluded_models, ["a"])

    def test_a_key_that_may_call_every_name_calls_every_model(self):
        self.assertTrue(Reach.of(configured_models(), []).every)
        self.assertEqual(Reach.of(configured_models(), ["a"]).usable, 1)


@override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=ROUTER_CONFIG)
class ProfileAvailabilityAdminFormTest(TeamsPagesTestBase):
    def test_a_cleared_box_is_what_the_gateway_refuses(self):
        profile = self.member.profile
        data = posted(UserProfileAdminForm(instance=profile))
        data["available_models"].remove("whisper-1")
        form = UserProfileAdminForm(data, instance=profile)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        key = Token.objects.get(pk=make_token(self.member, "laptop").pk)
        self.assertTrue(key.model_excluded("whisper-1"))
        self.assertFalse(key.model_excluded("dall-e-2"))


@override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=ROUTER_CONFIG)
class TeamAvailabilityPageTest(TeamsPagesTestBase):
    def url(self, team: Team | None = None) -> str:
        return reverse("team_availability", args=[(team or self.team).id])

    def unavailable_dall_e(self) -> dict:
        return {
            "available_models": [m for m in configured_models() if m != "dall-e-2"],
            "available_models-shown": "1",
        }

    def stored(self) -> list[str]:
        return Team.objects.get(pk=self.team.pk).excluded_models

    def test_signed_out_goes_to_sign_in_and_changes_nothing(self):
        self.assert_sent_to_sign_in(self.client.get(self.url()), self.url())
        self.assert_sent_to_sign_in(
            self.client.post(self.url(), self.unavailable_dall_e()), self.url()
        )
        self.assertEqual(self.stored(), [])

    def test_members_team_admins_and_other_orgs_get_404_and_change_nothing(self):
        for user in (self.member, self.team_admin, self.outsider, self.other_org_admin):
            with self.subTest(user=user.username):
                client = self.as_user(user)
                self.assertEqual(client.get(self.url()).status_code, HTTPStatus.NOT_FOUND)
                response = client.post(self.url(), self.unavailable_dall_e())
                self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
        self.assertEqual(self.stored(), [])

    def test_org_admin_and_admin_make_a_model_unavailable_to_service_accounts(self):
        admin = self.user("admin", UserGroup.ADMIN, self.other_org)
        for user in (self.org_admin, admin):
            with self.subTest(user=user.username):
                response = self.as_user(user).post(self.url(), self.unavailable_dall_e())
                self.assertRedirects(
                    response, reverse("team", args=[self.team.id]), fetch_redirect_response=False
                )
        self.assertEqual(self.stored(), ["dall-e-2"])
        key = Token.objects.get(pk=self.pipeline_key.pk)
        self.assertTrue(key.model_excluded("dall-e-2"))
        personal = Token.objects.get(pk=make_token(self.member, "laptop").pk)
        self.assertFalse(personal.model_excluded("dall-e-2"))

    def test_saving_unchanged_keeps_what_is_stored(self):
        self.team.excluded_models = ["retired-model", "whisper-1"]
        self.team.save()
        form = self.as_user(self.org_admin).get(self.url()).context["form"]
        self.assertContains(self.as_user(self.org_admin).get(self.url()), "Select none")
        self.as_user(self.org_admin).post(self.url(), posted(form))
        self.assertEqual(self.stored(), ["retired-model", "whisper-1"])

    def test_team_page_links_the_form_for_org_admins_only(self):
        page = reverse("team", args=[self.team.id])
        self.assertContains(self.as_user(self.org_admin).get(page), f'href="{self.url()}"')
        for user in (self.member, self.team_admin):
            self.assertNotContains(self.as_user(user).get(page), f'href="{self.url()}"')
