import json
from unittest.mock import AsyncMock, MagicMock, patch

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import override_settings
from django.urls import reverse

from gateway.decorators import check_tool_availability
from gateway.decorators.response_cache import register_response_in_cache
from gateway.response_type import RawJsonResponse
from gateway.tests.utils import _build_chat_headers, _read_streaming_response_lines
from gateway.tests.utils.base import GatewayIntegrationTestCase
from management.models import Org, Request, Token, VectorStore
from mock_api.mock_configs import MockStreamingConfig, default_post_stream_configs

User = get_user_model()


class ResponsesIntegrationTest(GatewayIntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = reverse("gateway:v1_responses")

    async def test_create_response_basic(self):
        """
        Tests basic response creation via POST /v1/responses.
        Verifies that the endpoint exists and returns a 200 status code.
        """
        headers = _build_chat_headers(self.AQUEDUCT_ACCESS_TOKEN)

        # Basic request payload for responses API
        payload = {
            "model": self.model,
            "input": [{"role": "user", "content": "Hello, how are you?"}],
            "max_output_tokens": 50,
        }

        response = await self.async_client.post(
            self.url, data=json.dumps(payload), headers=headers, content_type="application/json"
        )

        self.assertEqual(
            response.status_code,
            200,
            f"Expected 200 OK, got {response.status_code}: {response.content}",
        )

        response_json = response.json()
        self.assertIn("id", response_json)
        self.assertIn("object", response_json)
        self.assertEqual(response_json["object"], "response")
        self.assertIn("output", response_json)
        self.assertIsInstance(response_json["output"], list)

        requests = [r async for r in Request.objects.all()]
        self.assertEqual(
            len(requests), 1, "There should be exactly one request after response creation."
        )
        req = requests[0]
        self.assertIn("responses", req.path, "Request endpoint should be for responses.")

        self.assertIsNotNone(req.input_tokens)
        self.assertGreater(req.input_tokens, 0)
        self.assertIsNotNone(req.output_tokens)
        self.assertGreater(req.output_tokens, 0)

        # Verify response was registered in cache
        response_id = response_json.get("id")
        self.assertIsNotNone(response_id, "Response ID should be present in response")

        # Check cache for response registration
        cache_key = f"response:{response_id}"
        cached_data = caches["default"].get(cache_key)
        self.assertEqual(
            cached_data.get("model"), self.model, "Cached model name should match request model"
        )
        self.assertEqual(
            cached_data.get("email"),
            "me@example.com",
            "Cached user email should match current user",
        )

        # Test GET response endpoint
        response_url = reverse("gateway:v1_response", kwargs={"response_id": response_id})

        get_response = await self.async_client.get(response_url, headers=headers)
        self.assertEqual(
            get_response.status_code,
            200,
            f"GET response should return 200, "
            f"got {get_response.status_code}: {get_response.content}",
        )
        get_data = get_response.json()
        self.assertIn("id", get_data)
        self.assertEqual(get_data["id"], response_id)
        self.assertEqual(get_data["object"], "response")

        # Test GET response input_items endpoint
        input_items_response = await self.async_client.get(
            reverse("gateway:v1_response_input_items", kwargs={"response_id": response_id}),
            headers=headers,
        )
        self.assertEqual(
            input_items_response.status_code,
            200,
            f"GET input_items should return 200, got {input_items_response.status_code}: "
            f"{input_items_response.content}",
        )
        input_items_data = input_items_response.json()
        self.assertIsInstance(input_items_data, dict)
        self.assertIn("data", input_items_data)
        self.assertIsInstance(input_items_data["data"], list)

        # Test DELETE response endpoint
        delete_response = await self.async_client.delete(response_url, headers=headers)
        self.assertEqual(
            delete_response.status_code,
            200,
            f"DELETE response should return 200, "
            f"got {delete_response.status_code}: {delete_response.content}",
        )

        # Verify response is deleted - GET should now return 404
        verify_get_response = await self.async_client.get(response_url, headers=headers)
        self.assertEqual(
            verify_get_response.status_code,
            404,
            f"GET after DELETE should return 404, "
            f"got {verify_get_response.status_code}: {verify_get_response.content}",
        )

    async def test_streaming_records_reasoning_tokens_from_completed_response(self):
        chunks = []
        for chunk in default_post_stream_configs["responses"].response_data:
            data = json.loads(chunk.decode().removeprefix("data: ").strip())
            if data["type"] == "response.completed":
                data["response"]["usage"]["output_tokens"] = 20
                data["response"]["usage"]["output_tokens_details"]["reasoning_tokens"] = 12
                data["response"]["usage"]["total_tokens"] = (
                    data["response"]["usage"]["input_tokens"] + 20
                )
            chunks.append(b"data: " + json.dumps(data).encode() + b"\n\n")

        with self.mock_server.patch_external_api(
            "responses", MockStreamingConfig(response_data=chunks)
        ):
            response = await self.async_client.post(
                self.url,
                data=json.dumps({"model": self.model, "input": "Hello", "stream": True}),
                headers=self.headers,
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            await _read_streaming_response_lines(response)

        req = await Request.objects.aget()
        self.assertEqual(req.output_tokens, 20)
        self.assertEqual(req.reasoning_tokens, 12)
        self.assertEqual(req.token_usage.total_tokens, req.input_tokens + 20)

    async def test_create_response_streaming(self):
        """
        Tests streaming response creation via POST /v1/responses with stream=True.
        Verifies that the endpoint returns a streaming response with proper format
        and logs the request to the database.
        """
        # Streaming request payload
        payload = {
            "model": self.model,
            "input": [{"role": "user", "content": "Hello, how are you?"}],
            "max_output_tokens": 50,
            "stream": True,
        }

        response = await self.async_client.post(
            self.url,
            data=json.dumps(payload),
            headers=self.headers,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, f"Expected 200 OK, got {response.status_code}")

        # Collect all streamed lines
        streamed_lines = await _read_streaming_response_lines(response)
        self.assertGreater(
            len(streamed_lines), 0, "Should receive at least one streamed data chunk."
        )

        # Extract text and response ID from responses API streaming format
        # The responses API uses a different format than chat completions
        # Look for response.output_text.delta events with "text" fields
        # Also capture response ID from response.created event
        full_content = ""
        response_id = None
        for line in streamed_lines:
            try:
                data = json.loads(line)
                if data.get("type") == "response.created" and "response" in data:
                    response_id = data["response"].get("id")
                elif data.get("type") == "response.output_text.delta" and "delta" in data:
                    full_content += data["delta"]
            except (json.JSONDecodeError, KeyError):
                continue

        self.assertTrue(full_content, "Streamed content should not be empty.")

        # Verify content from streaming response
        self.assertIn("Hello", full_content, "Streaming response should contain greeting.")

        # Check that the database contains one request and endpoint matches
        requests = [r async for r in Request.objects.all()]
        self.assertEqual(
            len(requests),
            1,
            "There should be exactly one request after streaming response creation.",
        )
        req = requests[0]
        self.assertIn(
            "responses", req.path, "Request endpoint should be for responses (streaming)."
        )
        self.assertIsNotNone(req.response_time_ms)
        self.assertIsNotNone(req.processing_time_ms)

        self.assertIsNotNone(req.input_tokens)
        self.assertGreater(req.input_tokens, 0)
        self.assertIsNotNone(req.output_tokens)
        self.assertGreater(req.output_tokens, 0)

        # Verify response was registered in cache (for streaming)
        self.assertIsNotNone(
            response_id, "Response ID should be captured from streaming response.created event"
        )

        cache_key = f"response:{response_id}"
        cached_data = await sync_to_async(caches["default"].get)(cache_key)
        if cached_data is not None:
            self.assertEqual(
                cached_data.get("model"), self.model, "Cached model name should match request model"
            )
            self.assertEqual(
                cached_data.get("email"),
                "me@example.com",
                "Cached user email should match current user",
            )
        else:
            self.fail(f"Response {response_id} should be registered in cache with key {cache_key}")

        # Test GET response endpoint for streaming response
        response_url = reverse("gateway:v1_response", kwargs={"response_id": response_id})

        get_response = await self.async_client.get(response_url, headers=self.headers)

        self.assertEqual(
            get_response.status_code,
            200,
            f"GET response should return 200, "
            f"got {get_response.status_code}: {get_response.content}",
        )
        get_data = get_response.json()
        self.assertIn("id", get_data)
        self.assertEqual(get_data["id"], response_id)
        self.assertEqual(get_data["object"], "response")

        # Test GET response input_items endpoint for streaming response
        input_items_response = await self.async_client.get(
            reverse("gateway:v1_response_input_items", kwargs={"response_id": response_id}),
            headers=self.headers,
        )
        self.assertEqual(
            input_items_response.status_code,
            200,
            f"GET input_items should return 200, got {input_items_response.status_code}: "
            f"{input_items_response.content}",
        )
        input_items_data = input_items_response.json()
        self.assertIsInstance(input_items_data, dict)
        self.assertIn("data", input_items_data)
        self.assertIsInstance(input_items_data["data"], list)

        # Test DELETE response endpoint for streaming response
        delete_response = await self.async_client.delete(response_url, headers=self.headers)
        self.assertEqual(
            delete_response.status_code,
            200,
            f"DELETE response should return 200, "
            f"got {delete_response.status_code}: {delete_response.content}",
        )

        # Verify streaming response is deleted - GET should now return 404
        verify_get_response = await self.async_client.get(response_url, headers=self.headers)
        self.assertEqual(
            verify_get_response.status_code,
            404,
            f"GET after DELETE should return 404, "
            f"got {verify_get_response.status_code}: {verify_get_response.content}",
        )

    def test_get_response_invalid_id(self):
        response = self.client.get("/v1/responses/invalid-id", headers=self.headers)
        self.assertEqual(
            response.status_code, 404, f"Expected 404 Not Found, got {response.status_code}"
        )

    def test_delete_response_invalid_id(self):
        response = self.client.delete("/v1/responses/invalid-id", headers=self.headers)
        self.assertEqual(
            response.status_code, 404, f"Expected 404 Not Found, got {response.status_code}"
        )

    def test_input_items_response_invalid_id(self):
        response = self.client.get(
            reverse("gateway:v1_response_input_items", kwargs={"response_id": "invalid-id"}),
            headers=self.headers,
        )
        self.assertEqual(
            response.status_code, 404, f"Expected 404 Not Found, got {response.status_code}"
        )

    def test_user_response_isolation_via_id(self):
        """
        Test that two distinct users cannot access each other's responses via ID.
        Tests both GET, DELETE and input_items endpoints.
        Follows the flow: user1 creates response -> user1 accesses -> user2 tries and fails
        """
        response_id = "resp_test123"
        register_response_in_cache(response_id, model=self.model, email="me@example.com")

        # Step 1: User1 (me@example.com) can access their own response
        headers_me = self.headers

        # Step 2: User1 can access their own response
        response_url = reverse("gateway:v1_response", kwargs={"response_id": response_id})
        get_response_me = self.client.get(response_url, headers=headers_me)
        self.assertEqual(
            get_response_me.status_code,
            200,
            f"User me@example.com should access their own response, "
            f"got {get_response_me.status_code}",
        )

        # Step 3: User2 (someone@example.com) cannot access User1's response
        # Create a token for someone@example.com
        user_someone = User.objects.get(email="someone@example.com")
        token_someone = Token(name="TestToken", user=user_someone)
        token_someone_key = token_someone._set_new_key()
        token_someone.save()

        headers_someone = _build_chat_headers(token_someone_key)

        # Test GET endpoint isolation
        get_response_someone = self.client.get(response_url, headers=headers_someone)
        self.assertEqual(
            get_response_someone.status_code,
            404,
            f"User someone@example.com should not access me@example.com's response, "
            f"got {get_response_someone.status_code}",
        )

        # Test DELETE endpoint isolation
        delete_response_someone = self.client.delete(response_url, headers=headers_someone)
        self.assertEqual(
            delete_response_someone.status_code,
            404,
            f"User someone@example.com should not delete me@example.com's response, "
            f"got {delete_response_someone.status_code}",
        )

        # Test input_items endpoint isolation
        input_items_response = self.client.get(
            reverse("gateway:v1_response_input_items", kwargs={"response_id": response_id}),
            headers=headers_someone,
        )
        self.assertEqual(
            input_items_response.status_code,
            404,
            f"User someone@example.com should not access me@example.com's input_items, "
            f"got {input_items_response.status_code}",
        )

        # Clean up - User1 should be able to delete their own response
        delete_response_me = self.client.delete(response_url, headers=headers_me)
        self.assertEqual(
            delete_response_me.status_code, 200, "User1 should be able to delete their own response"
        )


class CheckToolAvailabilityTest(GatewayIntegrationTestCase):
    """Tests for the check_tool_availability decorator, with the fixture's keys: `My Token`
    is Me's personal key, and `Initial token for TUna` is a key of the Whale team."""

    response_id = "test_response_id"

    def setUp(self):
        super().setUp()
        self.view = AsyncMock(return_value=RawJsonResponse({"result": "success"}))
        self.request = MagicMock()
        self.request.build_absolute_uri.side_effect = lambda path: f"http://testserver{path}"

    async def personal_key(self) -> Token:
        return await sync_to_async(Token.find_by_key)(self.AQUEDUCT_ACCESS_TOKEN)

    async def team_key(self) -> Token:
        return await Token.objects.select_related("service_account").aget(
            name="Initial token for TUna"
        )

    async def check(self, token: Token | None, tools: list[dict] | None = None, **kwargs):
        if token is not None:
            kwargs["token"] = token
        if tools is not None:
            kwargs["pydantic_model"] = {"tools": tools}
        result = await check_tool_availability(self.view)(self.request, self.response_id, **kwargs)
        return result, kwargs

    def assert_passed_on(self, result, kwargs) -> None:
        self.view.assert_called_once_with(self.request, self.response_id, **kwargs)
        self.assertEqual(result.status_code, 200)

    def assert_rejected(self, result, status: int, message: str) -> None:
        self.view.assert_not_called()
        self.assertIsInstance(result, RawJsonResponse)
        self.assertEqual(result.status_code, status)
        self.assertIn(message, result.data["error"]["message"])

    async def store(self, store_id: str, token: Token) -> None:
        await VectorStore.objects.acreate(id=store_id, token=token, name=store_id, created_at=42)

    async def test_check_tool_availability_success(self):
        tools = [{"type": "function", "function": {"name": "test_function"}}]
        self.assert_passed_on(*await self.check(await self.personal_key(), tools))

    @patch("gateway.decorators.responses.get_mcp_config")
    async def test_check_tool_availability_mcp_server_success(self, mock_get_mcp_config):
        """An Aqueduct MCP server URL is rewritten to the server's own URL."""
        mock_get_mcp_config.return_value = {"test_mcp_server": {"url": "http://mcp-server:8080"}}
        tool = {
            "type": "mcp",
            "server_label": "test_mcp_server",
            "server_url": "http://testserver/mcp-servers/test_mcp_server/mcp",
        }

        self.assert_passed_on(*await self.check(await self.personal_key(), [tool]))
        self.assertEqual(tool["server_url"], "http://mcp-server:8080")

    async def test_check_tool_availability_missing_token(self):
        result, _ = await self.check(None, [])
        self.assert_rejected(result, 400, "Invalid request")

    async def test_check_tool_availability_missing_pydantic_model(self):
        result, _ = await self.check(await self.personal_key())
        self.assert_rejected(result, 400, "Invalid request")

    @patch("gateway.decorators.responses.get_mcp_config")
    async def test_check_tool_availability_mcp_server_excluded(self, mock_get_mcp_config):
        """A server the key's org excludes is not found, even when it is configured."""
        mock_get_mcp_config.return_value = {"excluded_server": {"url": "http://mcp-server:8080"}}
        org = await Org.objects.aget(name="E060")
        await sync_to_async(org.add_excluded_mcp_server)("excluded_server")
        tool = {
            "type": "mcp",
            "server_label": "excluded_server",
            "server_url": "http://testserver/mcp-servers/excluded_server/mcp",
        }

        result, _ = await self.check(await self.personal_key(), [tool])
        self.assert_rejected(result, 404, "MCP server not found")

    @patch("gateway.decorators.responses.get_mcp_config")
    async def test_check_tool_availability_mcp_server_not_found(self, mock_get_mcp_config):
        mock_get_mcp_config.return_value = {}
        tool = {"type": "mcp", "server_label": "nonexistent_server"}

        result, _ = await self.check(await self.personal_key(), [tool])
        self.assert_rejected(result, 404, "MCP server not found")

    @patch("gateway.decorators.responses.get_mcp_config")
    async def test_check_tool_availability_mcp_server_url_match(self, mock_get_mcp_config):
        """A server is matched by URL, whatever label the client gives it."""
        mock_get_mcp_config.return_value = {
            "brave": {"url": "http://mcp-server:8080"},
            "other_server": {"url": "http://other:9000"},
        }
        tool = {
            "type": "mcp",
            "server_label": "websearch",
            "server_url": "http://testserver/mcp-servers/brave/mcp",
        }

        self.assert_passed_on(*await self.check(await self.personal_key(), [tool]))
        self.assertEqual(tool["server_url"], "http://mcp-server:8080")

    @override_settings(RESPONSES_API_ALLOWED_NATIVE_TOOLS=["allowed_tool"])
    async def test_check_tool_availability_invalid_tool_type(self):
        result, _ = await self.check(await self.personal_key(), [{"type": "invalid_tool_type"}])
        self.assert_rejected(result, 400, "Invalid tool type: invalid_tool_type")

    @override_settings(RESPONSES_API_ALLOWED_NATIVE_TOOLS=["allowed_native_tool"])
    async def test_check_tool_availability_allowed_native_tool(self):
        tools = [{"type": "allowed_native_tool"}]
        self.assert_passed_on(*await self.check(await self.personal_key(), tools))

    async def test_check_tool_availability_file_search_success(self):
        """A personal key may search the stores of its owner's personal keys."""
        key = await self.personal_key()
        await self.store("vs_remote_abc", key)
        await self.store("vs_remote_def", key)
        tool = {"type": "file_search", "vector_store_ids": ["vs_remote_abc", "vs_remote_def"]}

        self.assert_passed_on(*await self.check(key, [tool]))
        self.assertEqual(tool["vector_store_ids"], ["vs_remote_abc", "vs_remote_def"])

    async def test_check_tool_availability_file_search_service_account(self):
        """A team key may search its team's stores, not the personal stores of its creator."""
        team_key, personal_key = await self.team_key(), await self.personal_key()
        await self.store("vs_team", team_key)
        await self.store("vs_personal", personal_key)

        self.assert_passed_on(
            *await self.check(team_key, [{"type": "file_search", "vector_store_ids": ["vs_team"]}])
        )
        self.view.reset_mock()
        result, _ = await self.check(
            team_key, [{"type": "file_search", "vector_store_ids": ["vs_personal"]}]
        )
        self.assert_rejected(result, 404, "One or more vector stores not found")

    async def test_check_tool_availability_file_search_not_found(self):
        """A store that does not exist, or that another user's key made, is not found."""
        someone = await User.objects.aget(username="Someone")
        others_key = Token(name="someone's key", user=someone)
        others_key._set_new_key()
        await others_key.asave()
        await self.store("vs_someone", others_key)
        key = await self.personal_key()

        for store_id in ("vs_nonexistent", "vs_someone"):
            with self.subTest(store=store_id):
                tool = {"type": "file_search", "vector_store_ids": [store_id]}
                result, _ = await self.check(key, [tool])
                self.assert_rejected(result, 404, "One or more vector stores not found")

    async def test_check_tool_availability_file_search_empty_ids(self):
        tools = [{"type": "file_search", "vector_store_ids": []}]
        self.assert_passed_on(*await self.check(await self.personal_key(), tools))
