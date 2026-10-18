import base64
import json
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from httpx import Request as HttpxRequest
from httpx import Response
from litellm.types.utils import Choices, Message, ModelResponse, Usage
from openai.types.chat import ChatCompletion

from gateway.tests.utils import (
    _build_chat_headers,
    _parse_streamed_content_pieces,
    _read_streaming_response_lines,
)
from gateway.tests.utils.base import INTEGRATION_TEST_BACKEND, ChatCompletionsBase
from management.models import Org, Request
from mock_api.mock_configs import MockConfig, MockStreamingConfig, convert_to_stream_data


class ChatCompletionsIntegrationTest(ChatCompletionsBase):
    def test_chat_completion(self):
        response = self._send_chat_completion(self.MESSAGES)

        self.assertEqual(
            response.status_code,
            200,
            f"Expected 200 OK, got {response.status_code}: {response.content}",
        )

        response_json = response.json()
        chat_completion = ChatCompletion.model_validate(response_json)

        self.assertIsNotNone(chat_completion)
        self.assertTrue(chat_completion.choices)
        self.assertIsInstance(chat_completion.choices, list)
        self.assertGreater(len(chat_completion.choices), 0)

        first_choice = chat_completion.choices[0]
        self.assertTrue(hasattr(first_choice, "message"))
        self.assertIsNotNone(first_choice.message)
        self.assertTrue(hasattr(first_choice.message, "content"))
        self.assertIsNotNone(first_choice.message.content)

        requests = list(Request.objects.all())
        self.assertEqual(
            len(requests), 1, "There should be exactly one request after chat completion."
        )
        req = requests[0]
        self.assertIn(self.url, req.path, "Request endpoint should be for chat completion.")
        self.assertIsNotNone(req.input_tokens)
        self.assertIsNotNone(req.output_tokens)
        self.assertGreater(req.input_tokens, 0, "input_tokens should be > 0")
        self.assertGreater(req.output_tokens, 0, "output_tokens should be > 0")
        self.assertEqual(req.user_id, "")

    @override_settings(TIKA_SERVER_URL=None)
    def test_chat_completion_base64_file_input(self):
        file_content = b"This is a test file content for base64 encoding."
        file_base64 = base64.b64encode(file_content).decode("utf-8")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's in this file?"},
                        {
                            "type": "file",
                            "file": {
                                "filename": "test.txt",
                                "file_data": f"data:text/plain;base64,{file_base64}",
                            },
                        },
                    ],
                }
            ],
            "max_completion_tokens": 50,
        }

        with patch(
            "gateway.decorators.response_type.extract_text_with_tika",
            return_value="This is a test file content for base64 encoding.",
        ):
            response = self.client.post(
                self.url,
                data=json.dumps(payload),
                headers=self.headers,
                content_type="application/json",
            )

        self.assertEqual(
            response.status_code,
            200,
            f"Expected 200 OK, got {response.status_code}: {response.content}",
        )
        response_json = response.json()
        chat_completion = ChatCompletion.model_validate(response_json)
        content = chat_completion.choices[0].message.content
        self.assertIsNotNone(content)
        content = content.strip()
        self.assertGreater(len(content), 0)

        requests = list(Request.objects.all())
        self.assertEqual(
            len(requests), 1, "There should be exactly one request after base64 file input."
        )
        req = requests[0]
        self.assertIn(
            self.url, req.path, "Request endpoint should be chat completion for base64 file input."
        )
        self.assertIsNotNone(req.input_tokens)
        self.assertIsNotNone(req.output_tokens)
        self.assertGreater(req.input_tokens, 0, "input_tokens should be > 0 for base64 file input")
        self.assertGreater(
            req.output_tokens, 0, "output_tokens should be > 0 for base64 file input"
        )

    def test_chat_completion_file_id_input(self):
        file_content = b'{"custom_id": "test_file_id_input"}\n'
        upload_file = SimpleUploadedFile(
            "test_file_id.jsonl", file_content, content_type="application/jsonl"
        )
        headers = _build_chat_headers(self.AQUEDUCT_ACCESS_TOKEN)
        headers.pop("Content-Type", None)

        upload_response = self.client.post(
            "/files", {"file": upload_file, "purpose": "user_data"}, headers=headers
        )
        self.assertEqual(
            upload_response.status_code, 200, f"File upload failed: {upload_response.json()}"
        )
        upload_data = upload_response.json()
        file_id = upload_data["id"]

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's in this file?"},
                        {"type": "file", "file": {"file_id": file_id}},
                    ],
                }
            ],
            "max_completion_tokens": 50,
        }

        with patch(
            "gateway.decorators.response_type.extract_text_with_tika",
            return_value="This is a test file content for base64 encoding.",
        ):
            response = self.client.post(
                self.url,
                data=json.dumps(payload),
                headers=self.headers,
                content_type="application/json",
            )

        self.assertEqual(
            response.status_code,
            200,
            f"Expected 200 OK, got {response.status_code}: {response.content}",
        )
        response_json = response.json()
        chat_completion = ChatCompletion.model_validate(response_json)
        content = chat_completion.choices[0].message.content.strip()
        self.assertGreater(len(content), 0)

        requests = list(Request.objects.all())
        self.assertEqual(
            len(requests),
            2,
            "There should be exactly two requests after file_id input "
            "(file upload + chat completion).",
        )

        chat_request = next(r for r in requests if self.url in r.path)
        req = chat_request
        self.assertIn(
            self.url, req.path, "Request endpoint should be chat completion for file_id input."
        )
        self.assertIsNotNone(req.input_tokens)
        self.assertIsNotNone(req.output_tokens)
        self.assertGreater(req.input_tokens, 0, "input_tokens should be > 0 for file_id input")
        self.assertGreater(req.output_tokens, 0, "output_tokens should be > 0 for file_id input")

    def test_chat_completion_file_id_not_found(self):
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's in this file?"},
                        {"type": "file", "file": {"file_id": "non-existent-file-id"}},
                    ],
                }
            ],
            "max_completion_tokens": 50,
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            404,
            f"Expected 404 Not Found, got {response.status_code}: {response.content}",
        )

    def test_chat_completion_file_id_different_user(self):
        file_content = b'{"custom_id": "test_file_id_input"}\n'
        upload_file = SimpleUploadedFile(
            "test_file_id.jsonl", file_content, content_type="application/jsonl"
        )
        headers = _build_chat_headers(self.AQUEDUCT_ACCESS_TOKEN)
        headers.pop("Content-Type", None)

        upload_response = self.client.post(
            "/files", {"file": upload_file, "purpose": "user_data"}, headers=headers
        )
        self.assertEqual(
            upload_response.status_code, 200, f"File upload failed: {upload_response.json()}"
        )
        upload_data = upload_response.json()
        file_id = upload_data["id"]

        updated_access_token, _ = self.create_new_user()
        headers = _build_chat_headers(updated_access_token)

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's in this file?"},
                        {"type": "file", "file": {"file_id": file_id}},
                    ],
                }
            ],
            "max_completion_tokens": 50,
        }

        with patch(
            "gateway.decorators.response_type.extract_text_with_tika",
            return_value="This is a test file content for base64 encoding.",
        ):
            response = self.client.post(
                self.url, data=json.dumps(payload), headers=headers, content_type="application/json"
            )

        self.assertEqual(
            response.status_code,
            404,
            f"Expected 404 Not Found, got {response.status_code}: {response.content}",
        )

    @override_settings(AQUEDUCT_CHAT_COMPLETIONS_MAX_FILE_SIZE_MB=1)
    def test_chat_completion_base64_file_size_limit_individual(self):
        file_size = 1024 * 1024 + 1
        file_content = b"x" * file_size
        file_base64 = base64.b64encode(file_content).decode("utf-8")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's in this file?"},
                        {
                            "type": "file",
                            "file": {
                                "filename": "large_file.txt",
                                "file_data": f"data:text/plain;base64,{file_base64}",
                            },
                        },
                    ],
                }
            ],
            "max_completion_tokens": 50,
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            400,
            f"Expected 400 Bad Request for file size > 1MB, "
            f"got {response.status_code}: {response.content}",
        )
        self.assertIn(b"File too large", response.content)

    @override_settings(AQUEDUCT_CHAT_COMPLETIONS_MAX_TOTAL_SIZE_MB=1)
    def test_chat_completion_base64_file_size_limit_total(self):
        individual_file_size = 350 * 1024
        file_content = b"x" * individual_file_size
        file_base64 = base64.b64encode(file_content).decode("utf-8")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's in these files?"},
                        {
                            "type": "file",
                            "file": {
                                "filename": "large_file_1.txt",
                                "file_data": f"data:text/plain;base64,{file_base64}",
                            },
                        },
                        {
                            "type": "file",
                            "file": {
                                "filename": "large_file_2.txt",
                                "file_data": f"data:text/plain;base64,{file_base64}",
                            },
                        },
                        {
                            "type": "file",
                            "file": {
                                "filename": "large_file_3.txt",
                                "file_data": f"data:text/plain;base64,{file_base64}",
                            },
                        },
                    ],
                }
            ],
            "max_completion_tokens": 50,
        }

        with patch(
            "gateway.decorators.response_type.extract_text_with_tika",
            return_value="This is a test file content for base64 encoding.",
        ):
            response = self.client.post(
                self.url,
                data=json.dumps(payload),
                headers=self.headers,
                content_type="application/json",
            )

        self.assertEqual(
            response.status_code,
            400,
            f"Expected 400 Bad Request for total file size > 1MB, "
            f"got {response.status_code}: {response.content}",
        )
        self.assertIn(b"Files too large in total", response.content)

    def test_tika_error_response(self):
        file_content = b"This is a test file content for base64 encoding."
        file_base64 = base64.b64encode(file_content).decode("utf-8")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's in this file?"},
                        {
                            "type": "file",
                            "file": {
                                "filename": "test.txt",
                                "file_data": f"data:text/plain;base64,{file_base64}",
                            },
                        },
                    ],
                }
            ],
            "max_completion_tokens": 50,
        }
        tika_response_mock = Response(
            request=HttpxRequest(method="PUT", url="http://example.com/tika"),
            json={"error": "Mocked Tika server error"},
            status_code=HTTPStatus.IM_A_TEAPOT,
        )

        with patch(
            "gateway.decorators.response_type.httpx.AsyncClient.put",
            return_value=tika_response_mock,
        ):
            response = self.client.post(
                self.url,
                data=json.dumps(payload),
                headers=self.headers,
                content_type="application/json",
            )

        self.assertEqual(
            response.status_code,
            400,
            f"Expected 400, got {response.status_code}: {response.content}",
        )
        self.assertIn("Tika error extracting text from file", response.json()["error"]["message"])

    def assert_logged_as_timeout(self, logged: Request) -> None:
        self.assertEqual((logged.status_code, logged.model), (504, self.model))
        self.assertIn("chat/completions", logged.path)
        self.assertGreater(logged.response_time_ms, 0)
        self.assertGreater(logged.processing_time_ms, 0)
        self.assertIsNotNone(logged.token_id)

    @override_settings(RELAY_REQUEST_TIMEOUT=0.0001)
    def test_chat_completion_timeout_is_answered_with_504_and_logged(self):
        Request.objects.all().delete()
        with self.upstream_too_slow("chat/completions"):
            response = self._send_chat_completion(self.MESSAGES)
        self.assertEqual(response.status_code, 504, response.content)
        self.assert_logged_as_timeout(Request.objects.get())

    @override_settings(STREAM_REQUEST_TIMEOUT=5)
    @async_to_sync
    async def test_chat_completion_streaming(self):
        response = await self._send_chat_completion_streaming(self.MESSAGES)

        self.assertEqual(response.status_code, 200, f"Expected 200 OK, got {response.status_code}")

        streamed_lines = await _read_streaming_response_lines(response)
        self.assertGreater(
            len(streamed_lines), 0, "Should receive at least one streamed data chunk."
        )

        content_pieces = _parse_streamed_content_pieces(streamed_lines)
        full_content = "".join(content_pieces).strip()
        self.assertTrue(full_content, "Streamed content should not be empty.")

        requests = [r async for r in Request.objects.all()]
        self.assertEqual(
            len(requests), 1, "There should be exactly one request after streaming chat completion."
        )
        req = requests[0]
        self.assertIn(
            self.url, req.path, "Request endpoint should be for chat completion (streaming)."
        )
        self.assertIsNotNone(req.response_time_ms)
        self.assertIsNotNone(req.processing_time_ms)
        self.assertIsNotNone(req.input_tokens)
        self.assertIsNotNone(req.output_tokens)
        self.assertGreater(req.input_tokens, 0, "input_tokens should be > 0 (streaming)")
        self.assertGreater(req.output_tokens, 0, "output_tokens should be > 0 (streaming)")

    @override_settings(RELAY_REQUEST_TIMEOUT=0.0001)
    @async_to_sync
    async def test_chat_completion_streaming_timeout_is_answered_with_504_and_logged(self):
        await Request.objects.all().adelete()
        with self.upstream_too_slow("chat/completions"):
            response = await self._send_chat_completion_streaming(self.MESSAGES)
        self.assertEqual(response.status_code, 504)
        logged = [request async for request in Request.objects.all()]
        self.assertTrue(logged)
        self.assert_logged_as_timeout(logged[0])

    def test_chat_completion_excluded_model(self):
        org = Org.objects.get(name="E060")
        org.add_excluded_model(self.model)
        org.save()
        assert len(org.excluded_models) == 1

        response = self._send_chat_completion(self.MESSAGES)

        self.assertEqual(
            response.status_code,
            404,
            f"Expected 404 Model not found, got {response.status_code}: {response.content}",
        )

    def test_chat_completion_unknown_model(self):
        payload = {"model": "unknown-model", "messages": self.MESSAGES}

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )
        self.assertEqual(
            response.status_code,
            400,
            f"Expected 400 Bad Request, got {response.status_code}: {response.content}",
        )
        self.assertIn("no healthy deployments for this model", response.json()["error"]["message"])

    def test_chat_completion_schema_generation(self):
        json_schema = {
            "type": "object",
            "properties": {"greeting": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["greeting", "count"],
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You produce JSON output based on a schema."},
                {"role": "user", "content": "Generate JSON matching the provided schema."},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "schema", "schema": json_schema},
            },
            "max_completion_tokens": 50,
        }

        mock_resp = MockConfig(
            response_data=ModelResponse(
                id="chatcmpl-123456789",
                created=1768397207,
                model="gpt-4.1-nano-2025-04-14",
                object="chat.completion",
                choices=[
                    Choices(
                        finish_reason="stop",
                        index=0,
                        message=Message(
                            content='{"greeting":"Hello, world!","count":1}', role="assistant"
                        ),
                    )
                ],
                usage=Usage(
                    completion_tokens=13,
                    prompt_tokens=59,
                    total_tokens=72,
                    completion_tokens_details={
                        "accepted_prediction_tokens": 0,
                        "audio_tokens": 0,
                        "reasoning_tokens": 0,
                        "rejected_prediction_tokens": 0,
                    },
                    prompt_tokens_details={"audio_tokens": 0, "cached_tokens": 0},
                ),
            ).model_dump()
        )
        with self.mock_server.patch_external_api("chat/completions", mock_resp):
            response = self.client.post(
                self.url,
                data=json.dumps(payload),
                headers=self.headers,
                content_type="application/json",
            )

        self.assertEqual(
            response.status_code,
            200,
            f"Expected 200 OK, got {response.status_code}: {response.content}",
        )
        response_json = response.json()
        chat_completion = ChatCompletion.model_validate(response_json)
        content = chat_completion.choices[0].message.content.strip()
        result = json.loads(content)
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result.keys()), set(json_schema["properties"].keys()))
        self.assertIsInstance(result["greeting"], str)
        self.assertIsInstance(result["count"], int)

        requests = list(Request.objects.all())
        self.assertEqual(
            len(requests), 1, "There should be exactly one request after schema generation."
        )
        req = requests[0]
        self.assertIn(
            self.url, req.path, "Request endpoint should be for chat completion schema generation."
        )
        self.assertIsNotNone(req.input_tokens)
        self.assertIsNotNone(req.output_tokens)
        self.assertGreater(req.input_tokens, 0, "input_tokens should be > 0 for schema generation")
        self.assertGreater(
            req.output_tokens, 0, "output_tokens should be > 0 for schema generation"
        )

    def test_chat_completion_multimodal_input(self):
        if INTEGRATION_TEST_BACKEND == "vllm":
            self.skipTest(
                "Tests not adapted for vLLM yet... "
                "Requires GatewayIntegrationTestCase to manage multiple servers!"
            )

        with (Path(__file__).parent / "resources" / "Polytechnisches-Institut-1823.jpg").open(
            "rb"
        ) as image_file:
            img_b64 = base64.b64encode(image_file.read()).decode("utf-8")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What's in this image?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                        },
                    ],
                }
            ],
            "max_completion_tokens": 50,
        }

        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            200,
            f"Expected 200 OK, got {response.status_code}: {response.content}",
        )
        response_json = response.json()
        chat_completion = ChatCompletion.model_validate(response_json)
        content = chat_completion.choices[0].message.content.strip()
        self.assertGreater(len(content), 0)

        requests = list(Request.objects.all())
        self.assertEqual(
            len(requests), 1, "There should be exactly one request after multimodal input."
        )
        req = requests[0]
        self.assertIn(
            self.url, req.path, "Request endpoint should be chat completion for multimodal input."
        )
        self.assertIsNotNone(req.input_tokens)
        self.assertIsNotNone(req.output_tokens)
        self.assertGreater(req.input_tokens, 0, "input_tokens should be > 0 for multimodal input")
        self.assertGreater(req.output_tokens, 0, "output_tokens should be > 0 for multimodal input")

    @override_settings(STREAM_REQUEST_TIMEOUT=5)
    @async_to_sync
    async def test_chat_completion_schema_generation_streaming(self):
        json_schema = {
            "type": "object",
            "properties": {"greeting": {"type": "string"}, "count": {"type": "integer"}},
            "required": ["greeting", "count"],
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You produce JSON output based on a schema."},
                {"role": "user", "content": "Generate JSON matching the provided schema."},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "schema", "schema": json_schema},
            },
            "max_completion_tokens": 50,
            "stream": True,
        }

        _common_stream_chunk_data = {
            "id": "chatcmpl-12345",
            "created": 1768398242,
            "model": "gpt-4.1-nano",
            "object": "chat.completion.chunk",
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        _chat_completion_stream_data = [
            ModelResponse(
                **_common_stream_chunk_data,
                choices=[{"index": 0, "delta": {"content": '{"greeting":', "role": "assistant"}}],
            ).model_dump(),
            ModelResponse(
                **_common_stream_chunk_data,
                choices=[{"index": 0, "delta": {"content": '"Hello, world!",'}}],
            ).model_dump(),
            ModelResponse(
                **_common_stream_chunk_data,
                choices=[{"index": 0, "delta": {"content": '"count":1}'}}],
            ).model_dump(),
        ]
        mock_resp = MockStreamingConfig(
            response_data=convert_to_stream_data(_chat_completion_stream_data)
        )

        with self.mock_server.patch_external_api("chat/completions", mock_resp):
            response = await self.async_client.post(
                self.url,
                data=json.dumps(payload),
                headers=self.headers,
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200, f"Expected 200 OK, got {response.status_code}")
        streamed_lines = await _read_streaming_response_lines(response)
        self.assertGreater(
            len(streamed_lines), 0, "Should receive at least one streamed data chunk."
        )

        content_pieces = _parse_streamed_content_pieces(streamed_lines)
        full_content = "".join(content_pieces).strip()
        result = json.loads(full_content)
        self.assertIsInstance(result, dict)
        self.assertEqual(set(result.keys()), set(json_schema["properties"].keys()))
        self.assertIsInstance(result["greeting"], str)
        self.assertIsInstance(result["count"], int)

        requests = [r async for r in Request.objects.all()]
        self.assertEqual(
            len(requests),
            1,
            "There should be exactly one request after schema generation streaming.",
        )
        req = requests[0]
        self.assertIn(
            self.url,
            req.path,
            "Request endpoint should be for chat completion (streaming schema generation).",
        )
        self.assertIsNotNone(req.response_time_ms)
        self.assertIsNotNone(req.processing_time_ms)
        self.assertIsNotNone(req.input_tokens)
        self.assertIsNotNone(req.output_tokens)
        self.assertGreater(
            req.input_tokens, 0, "input_tokens should be > 0 (streaming schema generation)"
        )
        self.assertGreater(
            req.output_tokens, 0, "output_tokens should be > 0 (streaming schema generation)"
        )

    def test_reasoning_content_adds_reasoning_non_streaming(self):
        mock_resp = MockConfig(
            response_data=ModelResponse(
                id="chatcmpl-reasoning-test",
                created=1768397207,
                model="gpt-4.1-nano-2025-04-14",
                object="chat.completion",
                choices=[
                    Choices(
                        finish_reason="stop",
                        index=0,
                        message=Message(
                            content="Final answer",
                            role="assistant",
                            reasoning_content="Step-by-step reasoning...",
                        ),
                    )
                ],
                usage=Usage(
                    completion_tokens=10,
                    prompt_tokens=20,
                    total_tokens=30,
                    completion_tokens_details={"reasoning_tokens": 15},
                ),
            ).model_dump()
        )
        with self.mock_server.patch_external_api("chat/completions", mock_resp):
            response = self._send_chat_completion(self.MESSAGES)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        message = data["choices"][0]["message"]
        self.assertEqual(message["reasoning_content"], "Step-by-step reasoning...")
        self.assertEqual(message["reasoning"], "Step-by-step reasoning...")

    def test_reasoning_adds_reasoning_content_non_streaming(self):
        mock_resp = MockConfig(
            response_data=ModelResponse(
                id="chatcmpl-reasoning-test",
                created=1768397207,
                model="gpt-4.1-nano-2025-04-14",
                object="chat.completion",
                choices=[
                    Choices(
                        finish_reason="stop",
                        index=0,
                        message=Message(
                            content="Final answer",
                            role="assistant",
                            reasoning="Deep chain of thought...",
                        ),
                    )
                ],
                usage=Usage(
                    completion_tokens=10,
                    prompt_tokens=20,
                    total_tokens=30,
                    completion_tokens_details={"reasoning_tokens": 12},
                ),
            ).model_dump()
        )
        with self.mock_server.patch_external_api("chat/completions", mock_resp):
            response = self._send_chat_completion(self.MESSAGES)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        message = data["choices"][0]["message"]
        self.assertEqual(message["reasoning"], "Deep chain of thought...")
        self.assertEqual(message["reasoning_content"], "Deep chain of thought...")

    def test_neither_reasoning_field_present_unchanged(self):
        mock_resp = MockConfig(
            response_data=ModelResponse(
                id="chatcmpl-no-reasoning",
                created=1768397207,
                model="gpt-4.1-nano-2025-04-14",
                object="chat.completion",
                choices=[
                    Choices(
                        finish_reason="stop",
                        index=0,
                        message=Message(content="Plain response, no reasoning.", role="assistant"),
                    )
                ],
                usage=Usage(completion_tokens=10, prompt_tokens=20, total_tokens=30),
            ).model_dump()
        )
        with self.mock_server.patch_external_api("chat/completions", mock_resp):
            response = self._send_chat_completion(self.MESSAGES)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        message = data["choices"][0]["message"]
        self.assertNotIn("reasoning", message)
        self.assertNotIn("reasoning_content", message)

    async def test_reasoning_content_adds_reasoning_streaming(self):
        _common = {
            "id": "chatcmpl-stream-reasoning",
            "created": 1768398242,
            "model": "gpt-4.1-nano",
            "object": "chat.completion.chunk",
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        stream_data = [
            ModelResponse(
                **_common,
                choices=[
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "reasoning_content": "Let me think..."},
                    }
                ],
            ).model_dump(),
            ModelResponse(
                **_common, choices=[{"index": 0, "delta": {"content": "The answer is 42."}}]
            ).model_dump(),
            ModelResponse(
                **_common, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}]
            ).model_dump(),
        ]
        mock_resp = MockStreamingConfig(response_data=convert_to_stream_data(stream_data))
        with self.mock_server.patch_external_api("chat/completions", mock_resp):
            request = self._build_chat_completion_request(self.MESSAGES, stream=True)
            response = await self.async_client.post(**request, content_type="application/json")

        self.assertEqual(response.status_code, 200)
        streamed_lines = await _read_streaming_response_lines(response)
        first_chunk = json.loads(streamed_lines[0])
        delta = first_chunk["choices"][0]["delta"]
        self.assertEqual(delta["reasoning_content"], "Let me think...")
        self.assertEqual(delta["reasoning"], "Let me think...")

        content_chunk = json.loads(streamed_lines[1])
        content_delta = content_chunk["choices"][0]["delta"]
        self.assertNotIn("reasoning", content_delta)
        self.assertNotIn("reasoning_content", content_delta)
