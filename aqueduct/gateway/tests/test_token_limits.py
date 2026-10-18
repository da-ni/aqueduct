from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from openai.types.chat import ChatCompletion

from gateway.tests.utils.base import ChatCompletionsBase
from management.models import Org, Request, ServiceAccount, Team, Token, UserProfile

User = get_user_model()


class TokenLimitTest(ChatCompletionsBase):
    def setUp(self):
        super().setUp()
        clock = patch("django.utils.timezone.now", return_value=timezone.now())
        clock.start()
        self.addCleanup(clock.stop)

    def _setup_limits(self, kind: str, field: str, value: int):
        if kind == "org":
            org = Org.objects.get(name="E060")
            setattr(org, field, value)
            org.save(update_fields=[field])
            return org
        if kind == "team":
            team = Team.objects.get(name="Whale")
            setattr(team, field, value)
            team.save(update_fields=[field])

            service_account = ServiceAccount.objects.create(team=team, name="Whale Service")

            token = Token.objects.filter(
                key_hash=Token._hash_key(self.AQUEDUCT_ACCESS_TOKEN)
            ).first()
            if not token:
                raise RuntimeError("Could not find Token associated with AQUEDUCT_ACCESS_TOKEN.")

            if getattr(token, "service_account_id", None) is None:
                token.service_account = service_account
                token.save(update_fields=["service_account"])
            elif token.service_account_id != service_account.id:
                raise RuntimeError(
                    f"Token is already associated with a different service account "
                    f"(id={token.service_account_id})."
                )
            return team
        if kind == "user":
            user = User.objects.get(username="Me")
            profile = (
                user.profile if hasattr(user, "profile") else UserProfile.objects.get(user=user)
            )
            setattr(profile, field, value)
            profile.save(update_fields=[field])
            return profile
        raise ValueError(f"Unknown kind: {kind}")

    def _rate_limit_test_template(
        self, kind: str, field: str, value: int, messages, max_completion_tokens, limit_desc
    ):
        self._setup_limits(kind, field, value)

        response1 = self._send_chat_completion(
            messages, max_completion_tokens=max_completion_tokens
        )
        self.assertEqual(
            response1.status_code,
            200,
            f"Expected 200 OK, got {response1.status_code}: {response1.content}",
        )
        response_json = response1.json()
        chat_completion = ChatCompletion.model_validate(response_json)
        self.assertIsNotNone(chat_completion)
        self.assertTrue(chat_completion.choices)
        self.assertGreater(len(chat_completion.choices), 0)

        requests = list(Request.objects.all())
        self.assertEqual(
            len(requests),
            1,
            f"There should be exactly one request after first chat completion ({limit_desc}).",
        )
        req = requests[0]
        self.assertIn(self.url, req.path, "Request endpoint should be for chat completion.")
        self.assertIsNotNone(req.input_tokens)
        self.assertIsNotNone(req.output_tokens)
        self.assertGreater(req.input_tokens, 0, "input_tokens should be > 0")
        self.assertGreater(req.output_tokens, 0, "output_tokens should be > 0")

        response2 = self._send_chat_completion(
            messages, max_completion_tokens=max_completion_tokens
        )
        self.assertEqual(
            response2.status_code,
            429,
            f"Expected 429 Too Many Requests, got {response2.status_code}: {response2.content}",
        )
        retry_after = int(response2.headers["Retry-After"])
        self.assertGreaterEqual(retry_after, 1)
        self.assertLessEqual(retry_after, 60)
        try:
            error_json = response2.json()
            self.assertTrue(
                "rate limit" in str(error_json).lower() or "429" in str(error_json),
                f"Expected rate limit error message, got: {error_json}",
            )
        except Exception:
            self.assertTrue(
                "rate limit" in response2.content.decode().lower()
                or "429" in response2.content.decode(),
                f"Expected rate limit error message, got: {response2.content}",
            )

    def test_org_rate_limit_requests_per_minute(self):
        self._rate_limit_test_template(
            kind="org",
            field="requests_per_minute",
            value=1,
            messages=self.MESSAGES,
            max_completion_tokens=5,
            limit_desc="org requests_per_minute",
        )

    def test_team_rate_limit_requests_per_minute(self):
        self._rate_limit_test_template(
            kind="team",
            field="requests_per_minute",
            value=1,
            messages=self.MESSAGES,
            max_completion_tokens=5,
            limit_desc="team requests_per_minute",
        )

    def test_user_rate_limit_requests_per_minute(self):
        self._rate_limit_test_template(
            kind="user",
            field="requests_per_minute",
            value=1,
            messages=self.MESSAGES,
            max_completion_tokens=5,
            limit_desc="user requests_per_minute",
        )

    def test_org_rate_limit_input_tokens_per_minute(self):
        self._rate_limit_test_template(
            kind="org",
            field="input_tokens_per_minute",
            value=5,
            messages=self.MESSAGES,
            max_completion_tokens=1,
            limit_desc="org input_tokens_per_minute",
        )

    def test_team_rate_limit_input_tokens_per_minute(self):
        self._rate_limit_test_template(
            kind="team",
            field="input_tokens_per_minute",
            value=5,
            messages=self.MESSAGES,
            max_completion_tokens=1,
            limit_desc="team input_tokens_per_minute",
        )

    def test_user_rate_limit_input_tokens_per_minute(self):
        self._rate_limit_test_template(
            kind="user",
            field="input_tokens_per_minute",
            value=5,
            messages=self.MESSAGES,
            max_completion_tokens=1,
            limit_desc="user input_tokens_per_minute",
        )

    def test_org_rate_limit_output_tokens_per_minute(self):
        self._rate_limit_test_template(
            kind="org",
            field="output_tokens_per_minute",
            value=5,
            messages=self.MESSAGES,
            max_completion_tokens=10,
            limit_desc="org output_tokens_per_minute",
        )

    def test_team_rate_limit_output_tokens_per_minute(self):
        self._rate_limit_test_template(
            kind="team",
            field="output_tokens_per_minute",
            value=5,
            messages=self.MESSAGES,
            max_completion_tokens=10,
            limit_desc="team output_tokens_per_minute",
        )

    def test_user_rate_limit_output_tokens_per_minute(self):
        self._rate_limit_test_template(
            kind="user",
            field="output_tokens_per_minute",
            value=5,
            messages=self.MESSAGES,
            max_completion_tokens=10,
            limit_desc="user output_tokens_per_minute",
        )

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
