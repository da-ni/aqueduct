import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from openai.types.chat import ChatCompletion

from gateway.config import (
    get_all_model_request_limit_multipliers,
    get_model_request_limit_multiplier,
    get_model_supports_response_format,
    get_router_config,
)
from gateway.tests.utils import _build_chat_headers
from gateway.tests.utils.base import GatewayIntegrationTestCase
from management.models import Org, Request
from management.tests.helpers import router_config


def entry(name: str, **model_info) -> dict:
    return {
        "model_name": name,
        "litellm_params": {"model": f"openai/{name}", "api_key": "os.environ/OPENAI_API_KEY"},
        "model_info": model_info,
    }


class ModelAliasConfigValidationTest(SimpleTestCase):
    def test_config_load_keeps_every_alias_of_a_model(self):
        entries = [
            entry("gpt-4.1-nano", aliases=["main", "coding"]),
            entry("text-embedding-ada-002", mode="embedding", aliases=["embedding"]),
        ]
        with router_config(entries):
            loaded = get_router_config()["model_list"]
        self.assertEqual(
            [model["model_info"]["aliases"] for model in loaded],
            [["main", "coding"], ["embedding"]],
        )

    def test_config_load_with_duplicate_aliases_raises_error(self):
        entries = [
            entry("gpt-4.1-nano", aliases=["main"]),
            entry("text-embedding-ada-002", aliases=["main"]),
        ]
        with router_config(entries), self.assertRaises(RuntimeError) as context:
            get_router_config()
        self.assertIn("Duplicate alias", str(context.exception))
        self.assertIn("main", str(context.exception))

    def test_aliases_differing_only_in_case_are_distinct(self):
        entries = [
            entry("gpt-4.1-nano", aliases=["Main"]),
            entry("text-embedding-ada-002", aliases=["main"]),
        ]
        with router_config(entries):
            loaded = get_router_config()["model_list"]
        self.assertEqual([model["model_info"]["aliases"] for model in loaded], [["Main"], ["main"]])

    def test_request_limit_multiplier_per_model_and_alias(self):
        entries = [
            entry("gpt-4.1-nano", aliases=["main"], request_limit_multiplier=1.5),
            entry("gpt-4o", aliases=["default"]),
            entry("text-embedding-ada-002", mode="embedding", request_limit_multiplier=0.5),
        ]
        with router_config(entries):
            for model, expected in (
                ("gpt-4.1-nano", 1.5),
                ("main", 1.5),
                ("gpt-4o", 1.0),
                ("default", 1.0),
                ("text-embedding-ada-002", 0.5),
                ("unknown-model", 1.0),
            ):
                with self.subTest(model=model):
                    self.assertEqual(get_model_request_limit_multiplier(model), expected)
            self.assertEqual(
                get_all_model_request_limit_multipliers(),
                {"gpt-4.1-nano": 1.5, "gpt-4o": 1.0, "text-embedding-ada-002": 0.5},
            )

    def test_response_format_support_per_model_and_alias(self):
        entries = [
            entry("gpt-image-1", aliases=["img"], supports_response_format=False),
            entry("dall-e-2", aliases=["image"]),
        ]
        with router_config(entries):
            for model, expected in (
                ("gpt-image-1", False),
                ("img", False),
                ("dall-e-2", True),
                ("image", True),
                ("unknown-model", True),
            ):
                with self.subTest(model=model):
                    self.assertEqual(get_model_supports_response_format(model), expected)


class ModelAliasRoutingTest(GatewayIntegrationTestCase):
    tts_model = "gpt-4o-mini-tts"
    stt_model = "whisper-1"
    image_gen_model = "dall-e-2"

    def test_chat_completion_with_alias(self):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello!"},
        ]

        payload = {"model": "main", "messages": messages, "max_tokens": 10}

        response = self.client.post(
            "/chat/completions",
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
            f"Alias resolution not working. Expected 200, "
            f"got {response.status_code}: {response.content}",
        )

        response_json = response.json()
        chat_completion = ChatCompletion.model_validate(response_json)
        self.assertIsNotNone(chat_completion)
        self.assertTrue(chat_completion.choices)

    def test_speech_endpoint_with_alias(self):
        payload = {
            "model": "tts",
            "input": "Testing alias resolution for text-to-speech.",
            "voice": "alloy",
            "response_format": "mp3",
        }

        response = self.client.post(
            "/audio/speech",
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
            f"Alias resolution not working for TTS. Expected 200, got {response.status_code}",
        )

        requests = list(Request.objects.all())
        self.assertEqual(
            len(requests), 1, "There should be exactly one request after speech generation."
        )

    def test_transcriptions_endpoint_with_alias(self):
        test_audio_content = b"mock audio data"
        test_audio_file = SimpleUploadedFile(
            "test.mp3", test_audio_content, content_type="audio/mp3"
        )
        headers = _build_chat_headers(self.AQUEDUCT_ACCESS_TOKEN)
        headers.pop("Content-Type", None)

        response = self.client.post(
            "/audio/transcriptions", {"file": test_audio_file, "model": "stt"}, headers=headers
        )

        self.assertEqual(
            response.status_code,
            200,
            f"Alias resolution not working for STT. Expected 200, "
            f"got {response.status_code}: {response.content}",
        )

        response_json = response.json()
        self.assertIn("text", response_json, "Response should contain 'text' field")

        requests = list(Request.objects.all())
        self.assertEqual(
            len(requests), 1, "There should be exactly one request after transcription."
        )

    def test_chat_completion_with_nonexistent_alias(self):
        messages = [{"role": "user", "content": "Hello!"}]
        payload = {"model": "nonexistent-alias-12345", "messages": messages, "max_tokens": 10}

        response = self.client.post(
            "/chat/completions",
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_embeddings_with_alias(self):
        payload = {"model": "embedding", "input": ["The quick brown fox."]}

        response = self.client.post(
            "/embeddings",
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
            f"Alias resolution not working for embeddings. "
            f"Expected 200, got {response.status_code}",
        )

        response_json = response.json()
        self.assertIn("data", response_json)
        self.assertIsInstance(response_json["data"], list)
        self.assertGreater(len(response_json["data"]), 0)

    def test_image_generation_with_alias(self):
        payload = {
            "model": "image",
            "prompt": "A beautiful landscape with mountains and a lake",
            "size": "256x256",
        }

        response = self.client.post(
            "/images/generations",
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        response_json = response.json()
        self.assertIn("data", response_json, "Response should contain 'data' field")
        self.assertIsInstance(response_json["data"], list, "Data should be a list")
        self.assertGreater(len(response_json["data"]), 0, "Data should not be empty")

        img_data = response_json["data"][0]
        self.assertIn("b64_json", img_data, "Image data should contain 'b64_json' field")
        self.assertIsInstance(img_data["b64_json"], str, "Image data should be a string")

        requests = list(Request.objects.all())
        self.assertEqual(
            len(requests), 1, "There should be exactly one request after image generation."
        )

    def test_multiple_aliases_same_model(self):
        messages = [{"role": "user", "content": "Test"}]
        aliases_to_test = ["main", "coding"]

        first_status = None
        for alias in aliases_to_test:
            payload = {"model": alias, "messages": messages, "max_tokens": 5}

            response = self.client.post(
                "/chat/completions",
                data=json.dumps(payload),
                headers=self.headers,
                content_type="application/json",
            )

            if first_status is None:
                first_status = response.status_code
            else:
                self.assertEqual(
                    response.status_code,
                    first_status,
                    f"Alias '{alias}' returned {response.status_code}, "
                    f"but first alias returned {first_status}",
                )

    def test_excluded_model_alias_rejected(self):
        org = Org.objects.get(name="E060")
        org.add_excluded_model(self.model)
        org.save()

        messages = [{"role": "user", "content": "Test"}]

        payload = {"model": self.model, "messages": messages, "max_tokens": 5}

        response_actual = self.client.post(
            "/chat/completions",
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        payload = {"model": "main", "messages": messages, "max_tokens": 5}

        response_alias = self.client.post(
            "/chat/completions",
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(
            response_actual.status_code,
            404,
            f"Expected 404 for excluded model, got {response_actual.status_code}",
        )

        self.assertEqual(
            response_alias.status_code,
            404,
            f"Alias to excluded model should return 404, got {response_alias.status_code}",
        )

    def test_alias_case_sensitivity_in_requests(self):
        messages = [{"role": "user", "content": "Test"}]

        payload = {"model": "main", "messages": messages, "max_tokens": 5}

        response_lowercase = self.client.post(
            "/chat/completions",
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(
            response_lowercase.status_code,
            200,
            f"Alias resolution not working for lowercase. Expected 200, "
            f"got {response_lowercase.status_code}",
        )

        payload = {"model": "Main", "messages": messages, "max_tokens": 5}
        response_uppercase = self.client.post(
            "/chat/completions",
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertIn(
            response_uppercase.status_code,
            [400, 404],
            f"Wrong case alias 'Main' should fail, got {response_uppercase.status_code}",
        )

    def test_request_stores_resolved_model_name(self):

        endpoints = [
            {
                "name": "chat_completions",
                "path": "/chat/completions",
                "alias": "main",
                "resolved_model": "gpt-4.1-nano",
                "payload": {
                    "model": "main",
                    "messages": [{"role": "user", "content": "Test"}],
                    "max_tokens": 5,
                },
            },
            {
                "name": "completions",
                "path": "/completions",
                "alias": "main",
                "resolved_model": "gpt-4.1-nano",
                "payload": {"model": "main", "prompt": "Test prompt", "max_tokens": 5},
            },
            {
                "name": "embeddings",
                "path": "/embeddings",
                "alias": "embedding",
                "resolved_model": "text-embedding-ada-002",
                "payload": {"model": "embedding", "input": ["Test input"]},
            },
            {
                "name": "speech",
                "path": "/audio/speech",
                "alias": "tts",
                "resolved_model": "gpt-4o-mini-tts",
                "payload": {"model": "tts", "input": "Test", "voice": "alloy"},
            },
            {
                "name": "image_generation",
                "path": "/images/generations",
                "alias": "image",
                "resolved_model": "dall-e-2",
                "payload": {"model": "image", "prompt": "Test image", "size": "256x256"},
            },
            {
                "name": "transcriptions",
                "path": "/audio/transcriptions",
                "alias": "stt",
                "resolved_model": "whisper-1",
                "payload": None,
                "multipart": True,
            },
        ]

        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint["name"]):
                Request.objects.all().delete()

                if endpoint.get("multipart"):
                    test_file = SimpleUploadedFile(
                        "test.mp3", b"mock audio", content_type="audio/mp3"
                    )
                    headers = _build_chat_headers(self.AQUEDUCT_ACCESS_TOKEN)
                    headers.pop("Content-Type", None)
                    response = self.client.post(
                        endpoint["path"],
                        {"file": test_file, "model": endpoint["alias"]},
                        headers=headers,
                    )
                else:
                    response = self.client.post(
                        endpoint["path"],
                        data=json.dumps(endpoint["payload"]),
                        headers=self.headers,
                        content_type="application/json",
                    )

                self.assertEqual(
                    response.status_code,
                    200,
                    f"{endpoint['name']}: Expected 200, got {response.status_code}",
                )

                request_log = Request.objects.first()
                self.assertIsNotNone(request_log, f"{endpoint['name']}: Request should be logged")
                self.assertEqual(
                    request_log.model,
                    endpoint["resolved_model"],
                    f"{endpoint['name']}: Request model should be '{endpoint['resolved_model']}' "
                    f"(resolved from alias '{endpoint['alias']}'), got '{request_log.model}'",
                )

    def test_invalid_alias_returns_error_and_logs_request(self):
        messages = [{"role": "user", "content": "Test"}]
        payload = {"model": "nonexistent-alias-xyz", "messages": messages, "max_tokens": 5}

        response = self.client.post(
            "/chat/completions",
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        request_log = Request.objects.first()
        self.assertIsNotNone(request_log, "Request should be logged even for invalid alias")
        self.assertEqual(
            request_log.model,
            "nonexistent-alias-xyz",
            "Invalid alias should be stored as-is since it cannot be resolved",
        )
