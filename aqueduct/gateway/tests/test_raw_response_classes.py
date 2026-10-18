import json
from unittest.mock import AsyncMock

from django.test import SimpleTestCase
from litellm.types.utils import ModelResponseStream
from litellm.types.utils import Usage as LitellmUsage
from mcp import JSONRPCResponse
from mcp.types import JSONRPCMessage
from pydantic import BaseModel

from gateway.response_type import RawJsonResponse, RawStreamingResponse
from management.models import Usage


class TestRawJsonResponse(SimpleTestCase):
    def test_changes_to_the_data_before_the_first_read_reach_the_content(self):
        response = RawJsonResponse(data={"key": "value"})
        response.data["key"] = "changed"
        self.assertEqual(json.loads(response.content), {"key": "changed"})

    def test_content_is_serialized_once(self):
        dumps = []

        class Counted(BaseModel):
            foo: str

            def model_dump(self, **kwargs):
                dumps.append(self)
                return super().model_dump(**kwargs)

        response = RawJsonResponse(data=Counted(foo="bar"))
        contents = {response.content for _ in range(3)}

        self.assertEqual(len(dumps), 1)
        self.assertEqual([json.loads(content) for content in contents], [{"foo": "bar"}])

    def test_data_serialized_to_json(self):
        class Nested(BaseModel):
            foo: str

        data = {"name": "test", "nested": Nested(foo="bar"), "items": [Nested(foo="baz")]}
        response = RawJsonResponse(data=data)
        self.assertEqual(
            json.loads(response.content),
            {"name": "test", "nested": {"foo": "bar"}, "items": [{"foo": "baz"}]},
        )

    def test_list_data_serialized_to_json(self):
        class Nested(BaseModel):
            foo: str

        # Mixed list: models, plain values, and a nested list of models
        data = [Nested(foo="bar"), {"plain": True}, [Nested(foo="baz")]]
        response = RawJsonResponse(data=data)
        self.assertEqual(
            json.loads(response.content), [{"foo": "bar"}, {"plain": True}, [{"foo": "baz"}]]
        )

    def test_rejects_unsupported_data(self):
        with self.assertRaises(TypeError):
            RawJsonResponse(data="not a dict, list, or pydantic model")  # type: ignore[arg-type]


class TestRawStreamingResponse(SimpleTestCase):
    async def test_openai_mode_applies_transforms_and_logs_request(self):
        async def streaming_content():
            yield ModelResponseStream(
                id="chatcmpl-stream-reasoning",
                created=1768398242,
                model="gpt-4.1-nano",
                object="chat.completion.chunk",
                stream=True,
                stream_options={"include_usage": True},
                choices=[{"index": 0, "delta": {"role": "assistant", "content": "Hi"}}],
                usage=LitellmUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

        def add_reasoning(chunk: ModelResponseStream) -> ModelResponseStream:
            choices = chunk.get("choices", [])
            for choice in choices:
                message = choice.get("delta")
                if message:
                    message["reasoning"] = "Deep chain of thought..."
            return chunk

        mock_request_log = AsyncMock(token_usage=None, response_time_ms=None)
        mock_request_log.asave = AsyncMock()

        response = RawStreamingResponse(
            streaming_content=streaming_content(),
            request_log=mock_request_log,
            transforms=[add_reasoning],
        )

        chunks = [chunk async for chunk in response.streaming_content]
        self.assertEqual(chunks.pop(), b"data: [DONE]\n\n")
        self.assertEqual(len(chunks), 1)

        payload = json.loads(chunks[0].decode().removeprefix("data: "))
        self.assertEqual(payload["choices"][0]["delta"]["reasoning"], "Deep chain of thought...")

        mock_request_log.asave.assert_called_once()
        self.assertIsNotNone(mock_request_log.response_time_ms)
        self.assertEqual(mock_request_log.token_usage, Usage(input_tokens=10, output_tokens=5))

    async def test_mcp_mode_applies_transforms_without_request_log(self):
        async def streaming_content():
            yield JSONRPCResponse(jsonrpc="2.0", id=0, result={"test": "hello"})
            yield JSONRPCResponse(jsonrpc="2.0", id=0, result={"test": "world"})

        def append_suffix(chunk: JSONRPCMessage) -> JSONRPCMessage:
            chunk.result["test"] = chunk.result["test"] + "!"
            return chunk

        response = RawStreamingResponse(
            streaming_content=streaming_content(),
            request_log=None,
            transforms=[append_suffix],
            mode="mcp",
        )

        chunks = [chunk async for chunk in response.streaming_content]

        # MCP responses do not get the OpenAI "data: [DONE]" terminator.
        self.assertEqual(len(chunks), 2)
        for chunk_raw in chunks:
            payload = json.loads(chunk_raw.decode().removeprefix("data: "))
            self.assertTrue(payload["result"]["test"].endswith("!"))
