from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from openai.types.chat import ChatCompletion

from gateway.tests.utils.base import ChatCompletionsBase
from management.models import Org, Request, ServiceAccount, Team, Token

User = get_user_model()


class TokenLimitTest(ChatCompletionsBase):
    def setUp(self):
        super().setUp()
        clock = patch("django.utils.timezone.now", return_value=timezone.now())
        clock.start()
        self.addCleanup(clock.stop)

    def _setup_limits(self, kind: str, field: str, value: int) -> None:
        if kind == "team":
            owner = Team.objects.get(name="Whale")
            account = ServiceAccount.objects.create(team=owner, name="Whale Service")
            Token.objects.filter(key_hash=Token._hash_key(self.AQUEDUCT_ACCESS_TOKEN)).update(
                service_account=account
            )
        else:
            owner = (
                Org.objects.get(name="E060")
                if kind == "org"
                else User.objects.get(username="Me").profile
            )
        setattr(owner, field, value)
        owner.save(update_fields=[field])

    def test_each_limit_of_the_org_team_and_user_stops_the_second_request(self):
        for field, value, max_completion_tokens in (
            ("requests_per_minute", 1, 5),
            ("input_tokens_per_minute", 5, 1),
            ("output_tokens_per_minute", 5, 10),
        ):
            for kind in ("org", "team", "user"):
                with self.subTest(kind=kind, field=field), transaction.atomic():
                    cache.clear()
                    self._setup_limits(kind, field, value)
                    self.assert_second_request_is_rate_limited(max_completion_tokens)
                    transaction.set_rollback(True)

    def assert_second_request_is_rate_limited(self, max_completion_tokens: int) -> None:
        first = self._send_chat_completion(
            self.MESSAGES, max_completion_tokens=max_completion_tokens
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertTrue(ChatCompletion.model_validate(first.json()).choices)
        logged = Request.objects.get()
        self.assertIn(self.url, logged.path)
        self.assertGreater(logged.input_tokens, 0)
        self.assertGreater(logged.output_tokens, 0)

        second = self._send_chat_completion(
            self.MESSAGES, max_completion_tokens=max_completion_tokens
        )
        self.assertEqual(second.status_code, 429, second.content)
        retry_after = int(second.headers["Retry-After"])
        self.assertGreaterEqual(retry_after, 1)
        self.assertLessEqual(retry_after, 60)
        self.assertIn("rate limit", second.json()["error"]["message"].lower())

    @patch("gateway.rate_limiting.get_model_request_limit_multiplier", return_value=2.0)
    def test_per_model_request_limit_multiplier_budget(self, mock_multipliers):
        self._setup_limits("org", "requests_per_minute", 3)

        for i in range(6):
            response = self._send_chat_completion(self.MESSAGES, max_completion_tokens=5)
            self.assertEqual(
                response.status_code,
                200,
                f"Expected 200 OK for request {i + 1}, got {response.status_code}",
            )

        response7 = self._send_chat_completion(self.MESSAGES, max_completion_tokens=5)
        self.assertEqual(
            response7.status_code,
            429,
            f"Expected 429 Too Many Requests, got {response7.status_code}: {response7.content}",
        )
        retry_after = response7.headers["Retry-After"]
        self.assertEqual(
            response7.json()["error"]["message"],
            f"Rate limit exceeded. Request limit (6/min) (Retry after {retry_after}s).",
        )

    @patch("gateway.rate_limiting.get_model_request_limit_multiplier", return_value=0.5)
    def test_per_model_expensive_multiplier_limits_requests(self, mock_multipliers):
        self._setup_limits("org", "requests_per_minute", 3)

        response1 = self._send_chat_completion(self.MESSAGES, max_completion_tokens=5)
        self.assertEqual(response1.status_code, 200)

        response2 = self._send_chat_completion(self.MESSAGES, max_completion_tokens=5)
        self.assertEqual(response2.status_code, 200)

        response3 = self._send_chat_completion(self.MESSAGES, max_completion_tokens=5)
        self.assertEqual(response3.status_code, 429)
        retry_after = response3.headers["Retry-After"]
        self.assertEqual(
            response3.json()["error"]["message"],
            f"Rate limit exceeded. Request limit (1.5/min) (Retry after {retry_after}s).",
        )

    def test_rate_limit_message_lists_each_exceeded_limit(self):
        self._setup_limits("org", "requests_per_minute", 1)
        self._setup_limits("org", "output_tokens_per_minute", 1)
        first = self._send_chat_completion(self.MESSAGES, max_completion_tokens=5)
        self.assertEqual(first.status_code, 200)

        blocked = self._send_chat_completion(self.MESSAGES, max_completion_tokens=5)
        self.assertEqual(blocked.status_code, 429)
        retry_after = blocked.headers["Retry-After"]
        self.assertEqual(
            blocked.json()["error"]["message"],
            f"Rate limit exceeded. Request limit (1/min) (Retry after {retry_after}s), "
            f"Output token limit (1/min) (Retry after {retry_after}s).",
        )

    def test_limits_count_from_the_rate_limit_buckets_not_the_request_log(self):
        self._setup_limits("org", "requests_per_minute", 1)
        first = self._send_chat_completion(self.MESSAGES, max_completion_tokens=5)
        self.assertEqual(first.status_code, 200)
        Request.objects.all().delete()

        blocked = self._send_chat_completion(self.MESSAGES, max_completion_tokens=5)

        self.assertEqual(blocked.status_code, 429)
        self.assertFalse(Request.objects.exists())
