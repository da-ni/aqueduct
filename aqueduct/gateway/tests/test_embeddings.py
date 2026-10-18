import json

from django.urls import reverse

from gateway.tests.utils.base import INTEGRATION_TEST_BACKEND, GatewayIntegrationTestCase
from management.models import Request


class EmbeddingTest(GatewayIntegrationTestCase):
    model = "Qwen-0.5B" if INTEGRATION_TEST_BACKEND == "vllm" else "text-embedding-ada-002"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = reverse("gateway:embeddings")

    def test_embeddings(self):
        if INTEGRATION_TEST_BACKEND == "vllm":
            self.skipTest(
                "Tests not adapted for vLLM yet... "
                "Requires GatewayIntegrationTestCase to manage multiple servers!"
            )

        payload = {"model": self.model, "input": ["The quick brown fox jumps over the lazy dog."]}

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

        self.assertIn("data", response_json)
        self.assertIsInstance(response_json["data"], list)
        self.assertGreater(len(response_json["data"]), 0)
        embedding_obj = response_json["data"][0]
        self.assertIn("embedding", embedding_obj)
        self.assertIsInstance(embedding_obj["embedding"], list)
        self.assertGreater(len(embedding_obj["embedding"]), 0)

        requests = list(Request.objects.all())
        self.assertEqual(len(requests), 1, "There should be exactly one request after embeddings.")
        req = requests[0]
        self.assertIn("embeddings", req.path, "Request endpoint should be for embeddings.")
        self.assertIsNotNone(req.input_tokens)
        self.assertIsNotNone(req.output_tokens)
        self.assertGreater(req.input_tokens, 0, "input_tokens should be > 0")
        self.assertEqual(req.output_tokens, 0, "output_tokens should be 0")
        self.assertEqual(
            req.cached_input_tokens,
            0,
            "No cached input tokens were reported for this embedding request.",
        )
        self.assertEqual(req.user_id, "")
