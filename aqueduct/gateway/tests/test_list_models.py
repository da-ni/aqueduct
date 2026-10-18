import tempfile
from datetime import timedelta

import yaml
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from gateway.config import get_router_config
from gateway.tests.utils.base import GatewayIntegrationTestCase
from management.models import Org, Request, ServiceAccount, Team, Token
from management.tests.helpers import router_config

User = get_user_model()


class ListModelsIntegrationTest(GatewayIntegrationTestCase):
    def _send_model_list_request(self):
        return self.client.get("/models", content_type="application/json", headers=self.headers)

    def test_list_models(self):
        response = self._send_model_list_request()

        self.assertEqual(
            response.status_code,
            200,
            f"Expected 200 OK, got {response.status_code}: {response.content}",
        )

        response_json = response.json()

        self.assertIn("data", response_json)
        self.assertIsInstance(response_json["data"], list)
        self.assertGreater(len(response_json["data"]), 0)

        model_ids = [m["id"] for m in response_json["data"] if "id" in m]
        self.assertIn(self.model, model_ids)

        requests = list(Request.objects.all())
        self.assertEqual(len(requests), 1, "There should be exactly one request after list models.")
        req = requests[0]
        self.assertIn("models", req.path, "Request endpoint should be for model listing.")

    def test_model_group_info_exposes_context_window(self):
        entries = [
            {
                "model_name": self.model,
                "litellm_params": {"model": f"openai/{self.model}"},
                "model_info": {
                    "id": self.model,
                    "max_tokens": 262144,
                    "max_output_tokens": 32768,
                    "supports_vision": True,
                },
            },
            {
                "model_name": "explicit-model",
                "litellm_params": {"model": "openai/explicit-model"},
                "model_info": {"max_input_tokens": 999999, "max_tokens": 262144},
            },
            {"model_name": "no-info-model", "litellm_params": {"model": "openai/no-info-model"}},
        ]
        with router_config(entries):
            response = self.client.get(
                "/model_group/info", content_type="application/json", headers=self.headers
            )

        self.assertEqual(response.status_code, 200)
        entries = {entry["model_group"]: entry for entry in response.json()}
        info = entries[self.model]["model_info"]
        self.assertEqual(info["max_input_tokens"], 262144)
        self.assertEqual(info["max_output_tokens"], 32768)
        self.assertTrue(info["supports_vision"])
        self.assertEqual(entries["explicit-model"]["model_info"]["max_input_tokens"], 999999)
        self.assertNotIn("max_input_tokens", entries["no-info-model"]["model_info"])

    def test_a_load_balanced_model_is_listed_once(self):
        deployments = [
            {
                "model_name": name,
                "litellm_params": {"model": f"openai/{served}"},
                "model_info": {"aliases": [f"{name}-alias"], "max_tokens": tokens},
            }
            for name, served, tokens in (
                ("qwen", "qwen-east", 1000),
                ("solo", "solo", 10),
                ("qwen", "qwen-west", 2000),
            )
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as config:
            yaml.safe_dump({"model_list": deployments}, config)
            with override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=config.name):
                listed = self._send_model_list_request().json()["data"]
                groups = self.client.get("/model_group/info", headers=self.headers).json()

        self.assertEqual([model["id"] for model in listed], ["qwen", "solo"])
        self.assertEqual(
            [(group["model_group"], group["model_info"]["max_input_tokens"]) for group in groups],
            [("qwen", 1000), ("solo", 10)],
        )

    def test_list_models_with_invalid_token(self):
        headers = {
            "HTTP_AUTHORIZATION": "Bearer invalid-token-123",
            "CONTENT_TYPE": "application/json",
        }

        response = self.client.get("/models", content_type="application/json", headers=headers)

        self.assertIn(
            response.status_code,
            [401, 403],
            f"Expected 401 or 403 for invalid token, "
            f"got {response.status_code}: {response.content}",
        )

        requests = list(Request.objects.all())
        self.assertEqual(len(requests), 0, "There should be no request recorded for invalid token.")

    def test_list_models_with_the_key_of_a_deactivated_user(self):
        user = Token.find_by_key(self.AQUEDUCT_ACCESS_TOKEN).user
        self.assertEqual(self._send_model_list_request().status_code, 200)

        user.is_active = False
        user.save()
        self.assertEqual(self._send_model_list_request().status_code, 401)

    def test_a_session_does_not_revive_the_key_of_a_deactivated_user(self):
        user = Token.find_by_key(self.AQUEDUCT_ACCESS_TOKEN).user
        user.is_active = False
        user.save()

        self.client.force_login(User.objects.create_user(username="other@example.com"))
        self.assertEqual(self._send_model_list_request().status_code, 401)

    def test_a_session_does_not_revive_an_expired_key(self):
        Token.objects.filter(key_hash=Token._hash_key(self.AQUEDUCT_ACCESS_TOKEN)).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        self.client.force_login(User.objects.create_user(username="other@example.com"))
        self.assertEqual(self._send_model_list_request().status_code, 401)

    def test_service_account_key_outlives_its_creator_s_deactivation(self):
        team = Team.objects.create(name="Lab", org=Org.objects.get(name="E060"))
        creator = Token.find_by_key(self.AQUEDUCT_ACCESS_TOKEN).user
        key = Token(
            name="Lab key",
            user=creator,
            service_account=ServiceAccount.objects.create(name="Lab", team=team),
        )
        headers = {"Authorization": f"Bearer {key._set_new_key()}"}
        key.save()

        creator.is_active = False
        creator.save()
        self.assertEqual(self.client.get("/models", headers=headers).status_code, 200)

    def test_list_excluded_model(self):
        org = Org.objects.get(name="E060")
        org.add_excluded_model(self.model)
        org.save()
        assert len(org.excluded_models) == 1

        loaded = get_router_config()
        model_list: list[dict] = loaded["model_list"]

        response = self._send_model_list_request()

        self.assertEqual(
            response.status_code,
            200,
            f"Expected 200 OK, got {response.status_code}: {response.content}",
        )

        response_json = response.json()

        self.assertIn("data", response_json)
        self.assertIsInstance(response_json["data"], list)

        model_ids = [m["id"] for m in response_json["data"] if "id" in m]
        self.assertEqual(len(model_ids), len(model_list) - 1)
        self.assertNotIn(self.model, model_ids)

        requests = list(Request.objects.all())
        self.assertEqual(len(requests), 1, "There should be exactly one request after list models.")
        req = requests[0]
        self.assertIn("models", req.path, "Request endpoint should be for model listing.")
