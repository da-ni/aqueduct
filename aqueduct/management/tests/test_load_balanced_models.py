import re
import tempfile
from pathlib import Path

import yaml
from django.test import TestCase, override_settings
from django.urls import reverse

from management.models import Org, Request, UserGroup
from management.tests.helpers import make_token, make_user


def deployment(name: str, served: str, mode: str) -> dict:
    return {
        "model_name": name,
        "litellm_params": {"model": f"openai/{served}"},
        "model_info": {"mode": mode, "aliases": [f"{name}-alias"]},
    }


LOAD_BALANCED = [
    deployment("qwen", "qwen-east", "chat"),
    deployment("solo", "solo", "embedding"),
    deployment("qwen", "qwen-west", "audio_transcription"),
]


class LoadBalancedModelTest(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = Path(directory.name) / "router.yaml"
        config.write_text(yaml.safe_dump({"model_list": LOAD_BALANCED}))
        router = override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=str(config))
        router.enable()
        self.addCleanup(router.disable)
        org = Org.objects.create(name="lab-org")
        self.member = make_user("member", org)
        self.admin = make_user("admin", org, UserGroup.ADMIN)

    def get(self, user, name: str, *args, **params):
        self.client.force_login(user)
        response = self.client.get(reverse(name, args=args), params)
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_models_page_shows_one_card_with_the_first_deployments_data(self):
        response = self.get(self.admin, "models", model="qwen-alias")
        listed = re.findall(r'#model-detail"[^>]*>([^<]+)</a>', response.content.decode())
        self.assertEqual(sorted(listed), ["qwen", "solo"])
        self.assertContains(response, "openai/qwen-east")
        self.assertNotContains(response, "openai/qwen-west")
        self.assertNotContains(response, "/audio/transcriptions")

    def test_the_overview_lists_the_model_once(self):
        key = make_token(self.member, "laptop")
        Request.objects.create(token=key, model="qwen", status_code=200, response_time_ms=1)
        for user in (self.admin, self.member):
            with self.subTest(user=user.username):
                filters = self.get(user, "usage").context["filters"]
                self.assertEqual(filters.models, ("qwen", "solo"))

    def test_the_key_page_counts_the_model_once(self):
        key = make_token(self.member, "laptop")
        self.member.profile.excluded_models = ["solo"]
        self.member.profile.save()
        self.assertContains(self.get(self.member, "token_detail", key.id), "Can call 1 model")
