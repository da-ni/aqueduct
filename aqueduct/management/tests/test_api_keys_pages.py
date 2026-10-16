import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from management.models import Org, Request, ServiceAccount, Team, TeamMembership, Token, UserProfile

User = get_user_model()

PAGES = override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    LITELLM_ROUTER_CONFIG_FILE_PATH=Path(settings.BASE_DIR).parent / "example_router_config.yaml",
)


def make_token(user, name: str, **kwargs) -> Token:
    token = Token(name=name, user=user, **kwargs)
    token._set_new_key()
    token.save()
    return token


def secret_on(response) -> str | None:
    match = re.search(r'data-copy="(sk-[^"]+)"', response.content.decode())
    return match.group(1) if match else None


@PAGES
class ApiKeysPagesTestBase(TestCase):
    def setUp(self):
        self.org = Org.objects.create(name="keys-org")
        self.team = Team.objects.create(name="keys-team", org=self.org)
        self.other_team = Team.objects.create(name="other-team", org=self.org)

        self.owner = self.user("owner")
        self.other = self.user("other")
        member_profile = UserProfile.objects.get(user=self.user("member"))
        self.member = member_profile.user
        TeamMembership.objects.create(user_profile=member_profile, team=self.team, is_admin=True)

        self.key = make_token(self.owner, "laptop")
        self.others_key = make_token(self.other, "others-laptop")
        self.pipeline = ServiceAccount.objects.create(name="pipeline", team=self.team)
        self.pipeline_key = make_token(self.member, "pipeline", service_account=self.pipeline)
        hidden_sa = ServiceAccount.objects.create(name="hidden-sa", team=self.other_team)
        make_token(self.other, "hidden-sa", service_account=hidden_sa)

    def user(self, name: str):
        user = User.objects.create_user(username=name, email=f"{name}@example.com")
        UserProfile.objects.create(user=user, org=self.org)
        return user

    def request_log(self, token: Token, ago: timedelta, **fields) -> Request:
        return Request.objects.create(token=token, timestamp=timezone.now() - ago, **fields)

    def get(self, user, name: str, *args, **params):
        self.client.force_login(user)
        return self.client.get(reverse(name, args=args), params)


class SecretShownOnceTest(ApiKeysPagesTestBase):
    def test_created_key_secret_appears_once_on_the_list(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("token_create"), {"name": "fresh", "expires_at": ""}, follow=True
        )
        self.assertEqual(response.redirect_chain, [(reverse("tokens"), 302)])
        self.assertContains(response, "Your key is ready")
        secret = secret_on(response)
        self.assertEqual(Token.find_by_key(secret), Token.objects.get(name="fresh"))
        self.assertContains(response, "curl http://testserver/v1/models")

        again = self.client.get(reverse("tokens"))
        self.assertNotContains(again, "Your key is ready")
        self.assertNotContains(again, secret)

    def test_rotated_key_secret_replaces_the_old_one(self):
        self.client.force_login(self.owner)
        old_hash = self.key.key_hash
        response = self.client.post(reverse("token_regenerate", args=[self.key.id]), follow=True)
        secret = secret_on(response)
        self.key.refresh_from_db()
        self.assertNotEqual(self.key.key_hash, old_hash)
        self.assertEqual(Token.find_by_key(secret), self.key)

    def test_create_at_the_limit_rerenders_the_form_with_an_error(self):
        make_token(self.owner, "two")
        make_token(self.owner, "three")
        self.client.force_login(self.owner)
        response = self.client.post(reverse("token_create"), {"name": "fourth", "expires_at": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You can only have 3 API keys.")
        self.assertFalse(Token.objects.filter(name="fourth").exists())
