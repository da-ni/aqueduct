from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from management.models import Request, ServiceAccount, Token
from management.tests.helpers import CHAT, EMBEDDING, RoleWorld, join, make_token

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
MEMBER = {"/member-laptop", "/member-embed", "/sent-as-solo"}
KEY_OF = {"/member-embed": "member-laptop", "/sent-as-solo": "member-laptop"}
NOT_FOUND = 404

ROWS = (
    ("solo", {}, {"/solo-laptop"}),
    ("solo", {"scope": "mine"}, {"/solo-laptop"}),
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
    ("org_admin", {"key": "foreign-laptop"}, NOT_FOUND),
    ("admin", {}, LAB),
    ("admin", {"scope": "mine"}, set()),
    ("admin", {"key": "foreign-sa"}, NOT_FOUND),
    ("other_org_admin", {}, FOREIGN),
    ("other_org_admin", {"key": "foreign-sa"}, {"/foreign-sa"}),
    ("other_org_admin", {"key": "solo-laptop"}, NOT_FOUND),
    ("other_org_admin", {"key": "pipeline"}, NOT_FOUND),
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
        return resolved

    def get(self, role: str, name: str, params: dict[str, object]):
        self.client.force_login(getattr(self, role))
        return self.client.get(reverse(name), params)

    def test_overview_and_requests_show_the_same_requests(self):
        for role, params, expected in ROWS:
            query = self.query(params)
            with self.subTest(role=role, **params):
                overview = self.get(role, "usage", query)
                log = self.get(role, "requests", query)
                if expected == NOT_FOUND:
                    self.assertEqual((overview.status_code, log.status_code), (404, 404))
                    continue
                filters = overview.context["filters"]
                shown = filters.period.of(filters.requests)
                self.assertEqual(set(shown.values_list("path", flat=True)), expected)
                kpis = {kpi.label: kpi.value for kpi in overview.context["kpis"]}
                self.assertEqual(kpis["Requests"], str(len(expected)))
                rows = log.context["log"].rows
                self.assertEqual({row.request.path for row in rows}, expected)
                self.assertEqual(log.context["log"].tabs[0].count, len(expected))

    def test_all_keys_lists_the_keys_behind_those_requests(self):
        for role, params, expected in ROWS:
            if expected == NOT_FOUND or "key" in params:
                continue
            with self.subTest(role=role, **params):
                listed = self.get(role, "usage_keys", self.query(params)).context["keys"]
                keys = {KEY_OF.get(path, path.removeprefix("/")) for path in expected}
                self.assertEqual({key.name for key in listed}, keys)

    def test_org_admin_in_another_orgs_team_does_not_see_its_log(self):
        join(self.org_admin, self.foreign_team)
        foreign_sa = Token.objects.get(name="foreign-sa")
        self.client.force_login(self.org_admin)
        log = self.client.get(reverse("requests"))
        self.assertEqual({row.request.path for row in log.context["log"].rows}, LAB)
        self.assertEqual(
            self.client.get(reverse("requests"), {"key": foreign_sa.id}).status_code, 404
        )
        self.assertEqual(self.client.get(reverse("usage"), {"key": foreign_sa.id}).status_code, 404)
