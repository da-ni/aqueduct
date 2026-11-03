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

    def test_the_log_shows_exactly_the_requests_each_role_may_see(self):
        for role, params, expected in ROWS:
            with self.subTest(role=role, **params):
                self.client.force_login(getattr(self, role))
                log = self.client.get(reverse("requests"), self.query(params))
                if expected == NOT_FOUND:
                    self.assertEqual(log.status_code, 404)
                    continue
                rows = log.context["log"].rows
                self.assertEqual({row.request.path for row in rows}, expected)
                self.assertEqual(log.context["log"].tabs[0].count, len(expected))

    def test_org_admin_in_another_orgs_team_does_not_see_its_log(self):
        join(self.org_admin, self.foreign_team)
        foreign_sa = Token.objects.get(name="foreign-sa")
        self.client.force_login(self.org_admin)
        log = self.client.get(reverse("requests"))
        self.assertEqual({row.request.path for row in log.context["log"].rows}, LAB)
        self.assertEqual(
            self.client.get(reverse("requests"), {"key": foreign_sa.id}).status_code, 404
        )
