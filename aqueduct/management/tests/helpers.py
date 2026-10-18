import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from management.models import (
    Org,
    Request,
    ServiceAccount,
    Snippet,
    SnippetType,
    Team,
    TeamMembership,
    Token,
    UserGroup,
    UserProfile,
)

User = get_user_model()
CHAT = "gpt-4.1-nano"
EMBEDDING = "text-embedding-ada-002"
KEY_NAMES = ("solo-laptop", "member-laptop", "pipeline", "foreign-laptop")


TEST_MCP_CONFIG = Path(settings.BASE_DIR).parent / "test-mcp.json"


def mcp_config(path: Path | str = TEST_MCP_CONFIG) -> override_settings:
    return override_settings(MCP_CONFIG_FILE_PATH=path)


@contextmanager
def router_config(entries: list[dict]) -> Iterator[None]:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "router.yaml"
        path.write_text(yaml.safe_dump({"model_list": entries}))
        with override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=str(path)):
            yield


def make_user(name: str, org: Org, group: UserGroup = UserGroup.USER):
    user = User.objects.create_user(username=name, email=f"{name}@example.com")
    UserProfile.objects.create(user=user, org=org).group = group
    return user


def make_token(user, name: str, **kwargs) -> Token:
    token = Token(name=name, user=user, **kwargs)
    token._set_new_key()
    token.save()
    return token


def join(user, team: Team, is_admin: bool = False) -> None:
    TeamMembership.objects.create(user_profile=user.profile, team=team, is_admin=is_admin)


class RoleWorld(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Org.objects.create(name="lab-org")
        cls.other_org = Org.objects.create(name="other-org")
        cls.team = Team.objects.create(name="NLP Lab", org=cls.org, description="Language tech")
        cls.side_team = Team.objects.create(name="Robotics", org=cls.org)
        cls.foreign_team = Team.objects.create(name="Foreign", org=cls.other_org)

        cls.admin = make_user("admin", cls.org, UserGroup.ADMIN)
        cls.org_admin = make_user("org-admin", cls.org, UserGroup.ORG_ADMIN)
        cls.team_admin = make_user("team-admin", cls.org)
        cls.member = make_user("member", cls.org)
        cls.solo = make_user("solo", cls.org)
        cls.other_org_admin = make_user("other-org-admin", cls.other_org, UserGroup.ORG_ADMIN)
        cls.foreigner = make_user("foreigner", cls.other_org)
        join(cls.team_admin, cls.team, is_admin=True)
        join(cls.member, cls.team)

        cls.solo_key = make_token(cls.solo, "solo-laptop")
        cls.member_key = make_token(cls.member, "member-laptop")
        cls.pipeline = ServiceAccount.objects.create(name="pipeline", team=cls.team)
        cls.pipeline_key = make_token(cls.team_admin, "pipeline", service_account=cls.pipeline)
        cls.foreign_key = make_token(cls.foreigner, "foreign-laptop")

    @classmethod
    def user(cls, name: str, group: UserGroup = UserGroup.USER, org: Org | None = None):
        return make_user(name, org or cls.org, group)

    join = staticmethod(join)

    def as_user(self, user):
        self.client.force_login(user)
        return self.client


class UsagePageTestBase(RoleWorld):
    def setUp(self):
        super().setUp()
        hour = timedelta(hours=1)
        self.log(
            self.solo_key, hour, 200, input_tokens=1000, cached_input_tokens=250, output_tokens=500
        )
        self.log(self.member_key, hour, 429, input_tokens=2000)
        self.log(self.pipeline_key, hour, 500, input_tokens=3000, output_tokens=1000)
        self.log(self.foreign_key, hour, 200, input_tokens=7000)

    def log(
        self, token: Token, ago: timedelta, status: int, model: str = CHAT, **fields
    ) -> Request:
        return Request.objects.create(
            token=token,
            timestamp=timezone.now() - ago,
            status_code=status,
            model=model,
            response_time_ms=100,
            **fields,
        )

    def page(self, user, **params):
        self.client.force_login(user)
        return self.client.get(reverse("usage"), params)

    def kpi(self, response, label: str) -> str:
        match = re.search(
            rf'{label}</span>\s*<span class="text-2xl font-semibold">([^<]*)</span>',
            response.content.decode(),
        )
        self.assertIsNotNone(match, f"no {label} figure on the page")
        return match.group(1)

    def status_count(self, response, label: str) -> str:
        match = re.search(
            rf'<span class="font-medium">{label}</span>.*?'
            r'<span class="ml-auto font-mono">([^<]*)</span>',
            response.content.decode(),
            re.S,
        )
        self.assertIsNotNone(match, f"no {label} row on the page")
        return match.group(1)

    def assert_keys(self, response, *visible: str) -> None:
        self.assertEqual(response.status_code, 200)
        for name in KEY_NAMES:
            if name in visible:
                self.assertContains(response, name)
            else:
                self.assertNotContains(response, name)


def median_error(exact: int) -> float:
    return max(2, 0.05 * exact)


def secret_on(response) -> str | None:
    match = re.search(r'data-copy="(sk-[^"]+)"', response.content.decode())
    return match.group(1) if match else None


def seed_active_config(code: str, name: str = "test-config") -> Snippet:
    Snippet.objects.filter(type=SnippetType.CONFIG, active=True).update(active=False)
    snippet, _ = Snippet.objects.update_or_create(
        name=name, type=SnippetType.CONFIG, defaults={"active": True, "code": code}
    )
    return snippet


SNIPPET_ORG_FROM_FIRST_GROUP = """\
class MyConfig(ConfigSnippet):
    def org_name(self, claims):
        groups = claims.get("groups") or []
        return groups[0] if groups else None
"""


SNIPPET_ORG_CUSTOM = """\
class MyConfig(ConfigSnippet):
    def org_name(self, claims):
        return claims.get("custom_org")
"""


SNIPPET_TEAM_NAMES_AND_MAP = """\
class MyConfig(ConfigSnippet):
    def org_name(self, claims):
        return "default"

    def team_names(self, claims):
        groups = claims.get("groups") or []
        return [g for g in groups if g.startswith("E")]

    def display_team_names(self, team_names):
        return [(t.split("-", maxsplit=1)[0], t) for t in team_names]
"""


SNIPPET_TEAM_NAMES_KEEP_FULL = """\
class MyConfig(ConfigSnippet):
    def org_name(self, claims):
        return "default"

    def team_names(self, claims):
        groups = claims.get("groups") or []
        return [g for g in groups if g.startswith("E")]

    def display_team_names(self, team_names):
        return [(t, t) for t in team_names]
"""


SNIPPET_TEAM_NAMES_PREFIX = """\
class MyConfig(ConfigSnippet):
    def team_names(self, claims):
        return ["E123", "E456"]

    def display_team_names(self, team_names):
        return [(n, n) for n in team_names]
"""


SNIPPET_TEAM_NAMES_NON_LIST = """\
class MyConfig(ConfigSnippet):
    def team_names(self, claims):
        return "not-a-list"
"""


SNIPPET_TEAM_NAMES_NONE = """\
class MyConfig(ConfigSnippet):
    def team_names(self, claims):
        return None
"""


SNIPPET_TEAM_NAMES_RAISES = """\
class MyConfig(ConfigSnippet):
    def team_names(self, claims):
        raise RuntimeError("boom")
"""


SNIPPET_DISPLAY_MAP_NONE = """\
class MyConfig(ConfigSnippet):
    def team_names(self, claims):
        groups = claims.get("groups") or []
        return [g for g in groups if g.startswith("E")]

    def display_team_names(self, team_names):
        return None
"""


SNIPPET_DISPLAY_MAP_NON_LIST = """\
class MyConfig(ConfigSnippet):
    def team_names(self, claims):
        groups = claims.get("groups") or []
        return [g for g in groups if g.startswith("E")]

    def display_team_names(self, team_names):
        return "nope"
"""


SNIPPET_DISPLAY_MAP_BAD_ENTRIES = """\
class MyConfig(ConfigSnippet):
    def team_names(self, claims):
        groups = claims.get("groups") or []
        return [g for g in groups if g.startswith("E")]

    def display_team_names(self, team_names):
        return [("E123", "E123-Students"), ("bad",), (None, "x"), 42]
"""


SNIPPET_NO_TEAMS = """\
class MyConfig(ConfigSnippet):
    def team_names(self, claims):
        return []

    def display_team_names(self, team_names):
        return []
"""


SNIPPET_USER_GROUP_BASIC = """\
class MyConfig(ConfigSnippet):
    def user_group(self, claims):
        groups = claims.get("groups") or []
        if "E123-Students" in groups:
            return "admin"
        if "E456-OrgAdmins" in groups:
            return "org-admin"
        return "user"
"""


SNIPPET_USER_GROUP_INVALID = """\
class MyConfig(ConfigSnippet):
    def user_group(self, claims):
        return "superadmin"
"""


SNIPPET_USER_GROUP_RAISES = """\
class MyConfig(ConfigSnippet):
    def user_group(self, claims):
        raise RuntimeError("boom")
"""
