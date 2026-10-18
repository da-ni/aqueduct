from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from management.models import Request, ServiceAccount, Token
from management.tests.helpers import RoleWorld, join, make_token

CHAT = "gpt-4.1-nano"
EMBEDDING = "text-embedding-ada-002"
LAB = {
    "/solo-laptop",
    "/member-laptop",
    "/member-embed",
    "/sent-as-solo",
    "/pipeline",
    "/side-sa",
    "/org-admin-laptop",
}
FOREIGN = {"/foreign-laptop", "/foreign-sa"}
KEY_OF = {"/member-embed": "member-laptop", "/sent-as-solo": "member-laptop"}
MEMBER = {"/member-laptop", "/member-embed", "/sent-as-solo"}
NOT_FOUND = 404

ROWS = (
    ("solo", {}, {"/solo-laptop"}),
    ("solo", {"scope": "mine"}, {"/solo-laptop"}),
    ("solo", {"org": "all"}, {"/solo-laptop"}),
    ("solo", {"org": "other_org"}, {"/solo-laptop"}),
    ("solo", {"key": "solo-laptop"}, {"/solo-laptop"}),
    ("solo", {"key": "member-laptop"}, NOT_FOUND),
    ("solo", {"key": "pipeline"}, NOT_FOUND),
    ("solo", {"key": "foreign-laptop"}, NOT_FOUND),
    ("solo", {"key": "abc"}, NOT_FOUND),
    ("member", {}, MEMBER | {"/pipeline"}),
    ("member", {"model": EMBEDDING}, {"/member-embed"}),
    ("member", {"key": "pipeline"}, {"/pipeline"}),
    ("member", {"key": "side-sa"}, NOT_FOUND),
    ("member", {"key": "solo-laptop"}, NOT_FOUND),
    ("team_admin", {}, {"/pipeline"}),
    ("team_admin", {"key": "side-sa"}, NOT_FOUND),
    ("org_admin", {}, LAB),
    ("org_admin", {"scope": "mine"}, {"/org-admin-laptop"}),
    ("org_admin", {"key": "member-laptop"}, MEMBER),
    ("org_admin", {"key": "side-sa", "model": CHAT}, {"/side-sa"}),
    ("org_admin", {"org": "all"}, LAB),
    ("org_admin", {"key": "foreign-laptop"}, NOT_FOUND),
    ("admin", {}, LAB),
    ("admin", {"scope": "mine"}, set()),
    ("admin", {"org": "all"}, LAB | FOREIGN),
    ("admin", {"org": "all", "key": "foreign-sa"}, {"/foreign-sa"}),
    ("admin", {"org": "other_org"}, FOREIGN),
    ("admin", {"org": "other_org", "key": "solo-laptop"}, NOT_FOUND),
    ("other_org_admin", {}, FOREIGN),
    ("other_org_admin", {"org": "all"}, FOREIGN),
    ("other_org_admin", {"org": "org"}, FOREIGN),
    ("other_org_admin", {"key": "solo-laptop"}, NOT_FOUND),
    ("other_org_admin", {"key": "pipeline"}, NOT_FOUND),
)

ALL_KEYS_REDIRECT_TO = {"solo": "solo-laptop", "team_admin": "pipeline"}

LAB_KEYS = {"solo-laptop", "member-laptop", "pipeline", "side-sa", "org-admin-laptop"}
FOREIGN_KEYS = {"foreign-laptop", "foreign-sa"}
OPENS = (
    ("solo", None, {"solo-laptop"}),
    ("member", None, {"member-laptop", "pipeline"}),
    ("team_admin", None, {"pipeline"}),
    ("org_admin", None, LAB_KEYS),
    ("org_admin", "all", LAB_KEYS),
    ("admin", None, LAB_KEYS | FOREIGN_KEYS),
    ("admin", "other_org", LAB_KEYS | FOREIGN_KEYS),
    ("admin", "all", LAB_KEYS | FOREIGN_KEYS),
    ("other_org_admin", None, FOREIGN_KEYS),
)


class VisibilityMatrixTest(RoleWorld):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        side_account = ServiceAccount.objects.create(name="side-sa", team=cls.side_team)
        make_token(cls.team_admin, "side-sa", service_account=side_account)
        make_token(cls.org_admin, "org-admin-laptop")
        foreign_account = ServiceAccount.objects.create(name="foreign-sa", team=cls.foreign_team)
        make_token(cls.foreigner, "foreign-sa", service_account=foreign_account)
        sent = {"timestamp": timezone.now() - timedelta(hours=1), "status_code": 200}
        rows = [
            Request(token=token, path=f"/{token.name}", model=CHAT, **sent)
            for token in Token.objects.all()
        ]
        rows.append(Request(token=cls.member_key, path="/member-embed", model=EMBEDDING, **sent))
        rows.append(
            Request(
                token=cls.member_key,
                path="/sent-as-solo",
                model=CHAT,
                user_id="solo@example.com",
                **sent,
            )
        )
        Request.objects.bulk_create(rows)

    def query(self, params: dict[str, str]) -> dict[str, object]:
        resolved: dict[str, object] = dict(params)
        if token := Token.objects.filter(name=params.get("key", "")).first():
            resolved["key"] = token.id
        if params.get("org") in ("org", "other_org"):
            resolved["org"] = getattr(self, params["org"]).id
        return resolved

    def key_id(self, name: str) -> int:
        return Token.objects.get(name=name).id

    def get(self, role: str, name: str, params: dict[str, object]):
        self.client.force_login(getattr(self, role))
        return self.client.get(reverse(name), params)

    def test_overview_requests_and_all_keys_show_the_same_requests(self):
        for role, params, expected in ROWS:
            query = self.query(params)
            with self.subTest(role=role, **params):
                overview = self.get(role, "usage", query)
                log = self.get(role, "requests", query)
                if expected == NOT_FOUND:
                    self.assertEqual((overview.status_code, log.status_code), (404, 404))
                    self.assertNotContains(log, params["key"], status_code=404)
                    continue
                filters = overview.context["filters"]
                shown = filters.period.of(filters.requests)
                self.assertEqual(set(shown.values_list("path", flat=True)), expected)
                kpis = {kpi.label: kpi.value for kpi in overview.context["kpis"]}
                self.assertEqual(kpis["Requests"], str(len(expected)))
                rows = log.context["log"].rows
                self.assertEqual({row.request.path for row in rows}, expected)
                self.assertEqual(log.context["filters"].can_pick_org, role == "admin")

    def test_all_keys_lists_the_keys_behind_those_requests(self):
        for role, params, expected in ROWS:
            if expected == NOT_FOUND or "key" in params:
                continue
            with self.subTest(role=role, **params):
                response = self.get(role, "usage_keys", self.query(params))
                if role in ALL_KEYS_REDIRECT_TO:
                    token = Token.objects.get(name=ALL_KEYS_REDIRECT_TO[role])
                    self.assertRedirects(
                        response,
                        reverse("token_detail", args=[token.id]),
                        fetch_redirect_response=False,
                    )
                    continue
                keys = {KEY_OF.get(path, path.removeprefix("/")) for path in expected}
                listed = response.context["keys"]
                self.assertEqual({key.name for key in listed}, keys)
                self.assertEqual(
                    {key.href for key in listed},
                    {reverse("token_detail", args=[self.key_id(name)]) for name in keys},
                )

    def test_the_key_page_opens_exactly_the_keys_each_role_may_see(self):
        for role, viewed_org, opens in OPENS:
            self.client.force_login(getattr(self, role))
            if viewed_org is not None:
                self.client.get(reverse("tokens"), self.query({"org": viewed_org}))
            for token in Token.objects.all():
                with self.subTest(role=role, viewed_org=viewed_org, key=token.name):
                    response = self.client.get(reverse("token_detail", args=[token.id]))
                    self.assertEqual(response.status_code, 200 if token.name in opens else 404)

    def test_pinned_disagreement_org_admin_in_another_orgs_team_opens_its_key_but_not_its_log(self):
        join(self.org_admin, self.foreign_team)
        foreign_sa = Token.objects.get(name="foreign-sa")
        self.client.force_login(self.org_admin)
        key_page = self.client.get(reverse("token_detail", args=[foreign_sa.id]))
        self.assertEqual(key_page.status_code, 200)
        log = self.client.get(reverse("requests"))
        self.assertEqual({row.request.path for row in log.context["log"].rows}, LAB)
        self.assertEqual(
            self.client.get(reverse("requests"), {"key": foreign_sa.id}).status_code, 404
        )
        self.assertEqual(self.client.get(reverse("usage"), {"key": foreign_sa.id}).status_code, 404)
