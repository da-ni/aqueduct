from http import HTTPStatus

from django.test import TestCase, override_settings
from django.urls import reverse

from gateway.tests.utils.base import ROUTER_CONFIG_PATH
from management.models import Org, ServiceAccount, Team, UserGroup
from management.tests.helpers import join, make_token, make_user

ROLES = ("platform admin", "org admin", "team admin", "member")

M = "/aqueduct/management/"
TOKENS = M + "tokens/"
ORG = M + "org/"
TEAM = M + "team/{team}"
TEAM_CREATE = M + "team/create/"
TEAM_EDIT = TEAM + "/edit/"
TEAM_DELETE = TEAM + "/delete/"
TEAM_ADMINS = TEAM + "/admins/"
SA_ADD = TEAM + "/service-accounts/add/"
SA_EDIT = M + "service-accounts/{service_account}/edit/"
SA_DELETE = M + "service-accounts/{service_account}/delete/"
SA_TRANSFER = M + "service-accounts/{service_account}/transfer/"

PAGES = [
    TOKENS,
    TOKENS + "create/",
    TOKENS + "{token}/edit/",
    TOKENS + "{token}/delete/",
    ORG,
    TEAM_CREATE,
    TEAM,
    TEAM_EDIT,
    TEAM_DELETE,
    TEAM_ADMINS,
    SA_ADD,
    SA_EDIT,
    SA_DELETE,
    SA_TRANSFER,
    M + "models/",
    M + "usage/",
    M + "usage/?token={token}",
    M + "files/",
    M + "batches/",
    M + "mcp-servers/",
    M + "vector-stores/",
    M + "vector-stores/vs_proto/",
]

# Status and Location that each old page gave before the shell, recorded on 680cad8. Any
# (role, page) missing here answered 200 without a redirect.
OTHER_ANSWERS: dict[tuple[str, str], tuple[int, str]] = {
    ("platform admin", TEAM_ADMINS): (405, ""),
    ("org admin", TEAM_ADMINS): (405, ""),
    ("team admin", TEAM_CREATE): (302, ORG),
    ("team admin", TEAM_DELETE): (302, ORG),
    ("team admin", TEAM_ADMINS): (302, ORG),
    ("member", TEAM_CREATE): (302, ORG),
    ("member", TEAM_EDIT): (302, TEAM),
    ("member", TEAM_DELETE): (302, ORG),
    ("member", TEAM_ADMINS): (302, ORG),
    ("member", SA_ADD): (302, TEAM),
    ("member", SA_EDIT): (302, TEAM),
    ("member", SA_DELETE): (302, TEAM),
    ("member", SA_TRANSFER): (302, TEAM),
}


@override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=ROUTER_CONFIG_PATH)
class OldPagesInShellTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        org = Org.objects.create(name="E060")
        cls.team = Team.objects.create(name="Whale", org=org)
        cls.service_account = ServiceAccount.objects.create(name="TUna", team=cls.team)
        cls.users = {
            "platform admin": make_user("padmin", org, UserGroup.ADMIN),
            "org admin": make_user("oadmin", org, UserGroup.ORG_ADMIN),
            "team admin": make_user("tadmin", org),
            "member": make_user("member", org),
        }
        for role, user in cls.users.items():
            join(user, cls.team, is_admin=role == "team admin")
        cls.tokens = {role: make_token(user, f"{role} key") for role, user in cls.users.items()}
        make_token(cls.users["platform admin"], "TUna key", service_account=cls.service_account)

    def url(self, role: str, template: str) -> str:
        return template.format(
            token=self.tokens[role].pk, team=self.team.pk, service_account=self.service_account.pk
        )

    def answers(self):
        for role in ROLES:
            self.client.force_login(self.users[role])
            for template in PAGES:
                yield role, template, self.client.get(self.url(role, template))

    def test_every_page_answers_as_before(self):
        for role, template, response in self.answers():
            expected_status, location = OTHER_ANSWERS.get((role, template), (200, ""))
            with self.subTest(role=role, page=template):
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(
                    response.get("Location", ""), self.url(role, location) if location else ""
                )

    def test_every_page_that_renders_sits_in_the_shell(self):
        for role, template, response in self.answers():
            if response.status_code == HTTPStatus.OK:
                with self.subTest(role=role, page=template):
                    self.assertTemplateUsed(response, "management/_sidebar.html")
                    self.assertContains(response, 'href="{}"'.format(reverse("tokens")))
