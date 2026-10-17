from pathlib import Path

from django.test import TestCase, override_settings

from management.admin import TeamAdminForm, UserProfileAdminForm
from management.availability import configured_models
from management.models import Org, Team, Token
from management.tests.test_api_keys_pages import make_token
from management.tests.test_organization_page import TEST_MCP_CONFIG, mcp_config
from management.tests.test_teams_pages import TeamsPagesTestBase

ROUTER_CONFIG = Path(__file__).resolve().parents[3] / "example_router_config.yaml"


def posted(form) -> dict:
    """What the browser sends for `form` when nobody changes a box."""
    data = {name: form[name].value() for name in form.fields}
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
