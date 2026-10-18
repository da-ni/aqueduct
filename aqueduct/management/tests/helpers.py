import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from management.models import (
    Org,
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

PAGES = override_settings(
    LITELLM_ROUTER_CONFIG_FILE_PATH=Path(settings.BASE_DIR).parent / "example_router_config.yaml"
)


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


@PAGES
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
        cls.side_account = ServiceAccount.objects.create(name="side-sa", team=cls.side_team)
        cls.side_key = make_token(cls.team_admin, "side-sa", service_account=cls.side_account)
        cls.foreign_key = make_token(cls.foreigner, "foreign-laptop")

    def as_user(self, user):
        self.client.force_login(user)
        return self.client


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
