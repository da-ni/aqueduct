import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from openai.types.chat import ChatCompletion

from gateway.config import (
    get_all_model_request_limit_multipliers,
    get_model_request_limit_multiplier,
    get_model_supports_response_format,
    get_router_config,
)
from gateway.tests.utils import _build_chat_headers
from gateway.tests.utils.base import GatewayIntegrationTestCase
from gateway.tests.utils.test_runner import ROUTER_CACHES
from management.models import Org, Request


@override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH="router.yaml")
class ModelAliasConfigValidationTest(TransactionTestCase):
    def setUp(self):
        for cached in ROUTER_CACHES:
            self.addCleanup(cached.cache_clear)

    def test_config_load_with_valid_aliases(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-4.1-nano",
                    "litellm_params": {
                        "model": "openai/gpt-4.1-nano",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["main", "coding"]},
                },
                {
                    "model_name": "text-embedding-ada-002",
                    "litellm_params": {
                        "model": "openai/text-embedding-ada-002",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"mode": "embedding", "aliases": ["embedding"]},
                },
            ]
        }

        with (
            patch("pathlib.Path.open"),
            patch("yaml.safe_load", return_value=mock_config) as mock_load,
        ):
            get_router_config.cache_clear()

            loaded_config = get_router_config()

            self.assertIsInstance(loaded_config, dict)
            self.assertIn("model_list", loaded_config)

            first_model = loaded_config["model_list"][0]
            self.assertIn("model_info", first_model)
            self.assertIn("aliases", first_model["model_info"])
            self.assertEqual(first_model["model_info"]["aliases"], ["main", "coding"])
            mock_load.assert_called_once()

    def test_config_load_with_duplicate_aliases_raises_error(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-4.1-nano",
                    "litellm_params": {
                        "model": "openai/gpt-4.1-nano",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["main"]},
                },
                {
                    "model_name": "text-embedding-ada-002",
                    "litellm_params": {
                        "model": "openai/text-embedding-ada-002",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["main"]},
                },
            ]
        }

        with (
            patch("pathlib.Path.open"),
            patch("yaml.safe_load", return_value=mock_config) as mock_load,
        ):
            get_router_config.cache_clear()

            with self.assertRaises(RuntimeError) as context:
                get_router_config()

            self.assertIn("Duplicate alias", str(context.exception))
            self.assertIn("main", str(context.exception))
            mock_load.assert_called_once()

    def test_config_load_with_multiple_aliases_per_model(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-4.1-nano",
                    "litellm_params": {
                        "model": "openai/gpt-4.1-nano",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["main", "coding", "default", "primary"]},
                }
            ]
        }

        with (
            patch("pathlib.Path.open"),
            patch("yaml.safe_load", return_value=mock_config) as mock_load,
        ):
            get_router_config.cache_clear()

            loaded_config = get_router_config()

            self.assertIsInstance(loaded_config, dict)
            model = loaded_config["model_list"][0]
            self.assertEqual(len(model["model_info"]["aliases"]), 4)
            self.assertIn("main", model["model_info"]["aliases"])
            self.assertIn("primary", model["model_info"]["aliases"])
            mock_load.assert_called_once()

    def test_alias_case_sensitivity(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-4.1-nano",
                    "litellm_params": {
                        "model": "openai/gpt-4.1-nano",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["Main"]},
                },
                {
                    "model_name": "text-embedding-ada-002",
                    "litellm_params": {
                        "model": "openai/text-embedding-ada-002",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["main"]},
                },
            ]
        }

        with (
            patch("pathlib.Path.open"),
            patch("yaml.safe_load", return_value=mock_config) as mock_load,
        ):
            get_router_config.cache_clear()

            loaded_config = get_router_config()

            self.assertIsInstance(loaded_config, dict)

            all_aliases = []
            for model in loaded_config["model_list"]:
                if "model_info" in model and "aliases" in model["model_info"]:
                    all_aliases.extend(model["model_info"]["aliases"])

            self.assertIn("Main", all_aliases)
            self.assertIn("main", all_aliases)
            self.assertEqual(len([a for a in all_aliases if a.lower() == "main"]), 2)
            mock_load.assert_called_once()

    def test_config_load_with_request_limit_multiplier(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-4.1-nano",
                    "litellm_params": {
                        "model": "openai/gpt-4.1-nano",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["main"], "request_limit_multiplier": 2},
                },
                {
                    "model_name": "gpt-4o",
                    "litellm_params": {
                        "model": "openai/gpt-4o",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["default"]},
                },
            ]
        }

        with patch("pathlib.Path.open"), patch("yaml.safe_load", return_value=mock_config):
            get_router_config.cache_clear()

            loaded_config = get_router_config()

            self.assertIsInstance(loaded_config, dict)
            first_model = loaded_config["model_list"][0]
            self.assertEqual(first_model["model_info"]["request_limit_multiplier"], 2)

    def test_get_model_request_limit_multiplier_returns_configured_value(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-4.1-nano",
                    "litellm_params": {
                        "model": "openai/gpt-4.1-nano",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["main"], "request_limit_multiplier": 1.5},
                }
            ]
        }

        with patch("pathlib.Path.open"), patch("yaml.safe_load", return_value=mock_config):
            get_router_config.cache_clear()

            multiplier = get_model_request_limit_multiplier("gpt-4.1-nano")
            self.assertEqual(multiplier, 1.5)

    def test_get_model_request_limit_multiplier_returns_default_for_unconfigured(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-4o",
                    "litellm_params": {
                        "model": "openai/gpt-4o",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["default"]},
                }
            ]
        }

        with patch("pathlib.Path.open"), patch("yaml.safe_load", return_value=mock_config):
            get_router_config.cache_clear()

            multiplier = get_model_request_limit_multiplier("gpt-4o")
            self.assertEqual(multiplier, 1.0)

    def test_get_model_request_limit_multiplier_resolves_alias(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-4.1-nano",
                    "litellm_params": {
                        "model": "openai/gpt-4.1-nano",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["main"], "request_limit_multiplier": 2},
                }
            ]
        }

        with patch("pathlib.Path.open"), patch("yaml.safe_load", return_value=mock_config):
            get_router_config.cache_clear()

            multiplier = get_model_request_limit_multiplier("main")
            self.assertEqual(multiplier, 2.0)

    def test_get_model_request_limit_multiplier_returns_1_for_unknown_model(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-4o",
                    "litellm_params": {
                        "model": "openai/gpt-4o",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["default"]},
                }
            ]
        }

        with patch("pathlib.Path.open"), patch("yaml.safe_load", return_value=mock_config):
            get_router_config.cache_clear()

            multiplier = get_model_request_limit_multiplier("unknown-model")
            self.assertEqual(multiplier, 1.0)

    def test_get_model_supports_response_format_returns_false_when_configured(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-image-1",
                    "litellm_params": {
                        "model": "openai/gpt-image-1",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"supports_response_format": False},
                }
            ]
        }

        with patch("pathlib.Path.open"), patch("yaml.safe_load", return_value=mock_config):
            get_router_config.cache_clear()

            self.assertFalse(get_model_supports_response_format("gpt-image-1"))

    def test_get_model_supports_response_format_defaults_to_true(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "dall-e-2",
                    "litellm_params": {
                        "model": "openai/dall-e-2",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["image"]},
                }
            ]
        }

        with patch("pathlib.Path.open"), patch("yaml.safe_load", return_value=mock_config):
            get_router_config.cache_clear()

            self.assertTrue(get_model_supports_response_format("dall-e-2"))

    def test_get_model_supports_response_format_resolves_alias(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-image-1",
                    "litellm_params": {
                        "model": "openai/gpt-image-1",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["img"], "supports_response_format": False},
                }
            ]
        }

        with patch("pathlib.Path.open"), patch("yaml.safe_load", return_value=mock_config):
            get_router_config.cache_clear()

            self.assertFalse(get_model_supports_response_format("img"))

    def test_get_model_supports_response_format_true_for_unknown_model(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-4o",
                    "litellm_params": {
                        "model": "openai/gpt-4o",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["default"]},
                }
            ]
        }

        with patch("pathlib.Path.open"), patch("yaml.safe_load", return_value=mock_config):
            get_router_config.cache_clear()

            self.assertTrue(get_model_supports_response_format("unknown-model"))

    def test_get_all_model_request_limit_multipliers(self):
        mock_config = {
            "model_list": [
                {
                    "model_name": "gpt-4.1-nano",
                    "litellm_params": {
                        "model": "openai/gpt-4.1-nano",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["main"], "request_limit_multiplier": 2.0},
                },
                {
                    "model_name": "gpt-4o",
                    "litellm_params": {
                        "model": "openai/gpt-4o",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"aliases": ["default"]},
                },
                {
                    "model_name": "text-embedding-ada-002",
                    "litellm_params": {
                        "model": "openai/text-embedding-ada-002",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                    "model_info": {"mode": "embedding", "request_limit_multiplier": 0.5},
                },
            ]
        }

        with patch("pathlib.Path.open"), patch("yaml.safe_load", return_value=mock_config):
            get_router_config.cache_clear()
            get_all_model_request_limit_multipliers.cache_clear()

            multipliers = get_all_model_request_limit_multipliers()

            self.assertEqual(multipliers["gpt-4.1-nano"], 2.0)
            self.assertEqual(multipliers["gpt-4o"], 1.0)
            self.assertEqual(multipliers["text-embedding-ada-002"], 0.5)
            self.assertEqual(len(multipliers), 3)


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
