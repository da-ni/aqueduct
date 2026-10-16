from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from management.models import (
    Org,
    Request,
    ServiceAccount,
    Team,
    TeamMembership,
    Token,
    UserGroup,
    UserProfile,
)
from management.request_log import PAGE_SIZE
from management.tests.test_api_keys_pages import PAGES, make_token

User = get_user_model()

MODEL = "gpt-4.1-nano"
OTHER_MODEL = "text-embedding-ada-002"
PATHS = ("/own", "/others", "/team-sa", "/side-sa", "/sent-as-owner", "/foreign")


@PAGES
class RequestsPageTestBase(TestCase):
    def setUp(self):
        self.org = Org.objects.create(name="lab-org")
        self.other_org = Org.objects.create(name="other-org")
        self.team = Team.objects.create(name="NLP Lab", org=self.org)
        self.side_team = Team.objects.create(name="Robotics", org=self.org)

        self.owner = self.user("owner")
        self.other = self.user("other")
        self.member = self.user("member")
        self.sa_creator = self.user("sa-creator")
        self.org_admin = self.user("org-admin", UserGroup.ORG_ADMIN)
        self.admin = self.user("admin", UserGroup.ADMIN)
        self.foreigner = self.user("foreigner", org=self.other_org)
        self.other_org_admin = self.user("other-org-admin", UserGroup.ORG_ADMIN, self.other_org)
        TeamMembership.objects.create(user_profile=self.member.profile, team=self.team)

        self.key = make_token(self.owner, "owner-laptop")
        self.others_key = make_token(self.other, "other-laptop")
        team_sa = ServiceAccount.objects.create(name="team-sa", team=self.team)
        self.team_key = make_token(self.sa_creator, "team-sa", service_account=team_sa)
        side_sa = ServiceAccount.objects.create(name="side-sa", team=self.side_team)
        self.side_key = make_token(self.sa_creator, "side-sa", service_account=side_sa)
        self.foreign_key = make_token(self.foreigner, "foreign-laptop")

        self.log(self.key, "/own")
        self.log(self.others_key, "/others")
        self.log(self.team_key, "/team-sa")
        self.log(self.side_key, "/side-sa")
        self.log(self.others_key, "/sent-as-owner", user_id="owner@example.com")
        self.log(self.foreign_key, "/foreign")

    def user(self, name: str, group: UserGroup = UserGroup.USER, org: Org | None = None):
        user = User.objects.create_user(username=name, email=f"{name}@example.com")
        UserProfile.objects.create(user=user, org=org or self.org).group = group
        return user

    def log(self, token: Token, path: str, ago=timedelta(hours=1), **fields) -> Request:
        fields = {"model": MODEL, "status_code": 200, **fields}
        return Request.objects.create(
            token=token, path=path, timestamp=timezone.now() - ago, **fields
        )

    def get(self, user, **params):
        self.client.force_login(user)
        return self.client.get(reverse("requests"), params)

    def paths_on(self, response) -> set[str]:
        return {path for path in PATHS if f">{path}</div>" in response.content.decode()}


class RequestsScopeTest(RequestsPageTestBase):
    def test_signed_out_goes_to_sign_in(self):
        url = reverse("requests")
        response = self.client.get(url)
        self.assertRedirects(
            response, f"{reverse('sso')}?next={url}", fetch_redirect_response=False
        )

    def test_user_sees_own_key_and_requests_sent_in_their_name(self):
        response = self.get(self.owner)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.paths_on(response), {"/own", "/sent-as-owner"})
        self.assertContains(
            response, f'href="{reverse("token_detail", args=[self.key.id])}"', count=1
        )
        self.assertNotContains(response, reverse("token_detail", args=[self.others_key.id]))

    def test_other_user_does_not_see_the_owners_requests(self):
        response = self.get(self.other)
        self.assertEqual(self.paths_on(response), {"/others", "/sent-as-owner"})
        self.assertNotContains(response, "owner-laptop")

    def test_team_member_sees_their_teams_service_account_only(self):
        response = self.get(self.member)
        self.assertEqual(self.paths_on(response), {"/team-sa"})
        self.assertContains(response, f'href="{reverse("team", args=[self.team.id])}"')
        self.assertNotContains(response, "side-sa")

    def test_org_admin_sees_the_whole_org_and_nothing_outside(self):
        response = self.get(self.org_admin)
        self.assertEqual(self.paths_on(response), set(PATHS) - {"/foreign"})
        self.assertNotContains(response, "foreign-laptop")

    def test_other_org_admin_sees_only_their_org(self):
        response = self.get(self.other_org_admin)
        self.assertEqual(self.paths_on(response), {"/foreign"})

    def test_non_admin_cannot_pick_another_org(self):
        response = self.get(self.owner, org=self.other_org.id)
        self.assertEqual(self.paths_on(response), {"/own", "/sent-as-owner"})
        response = self.get(self.other_org_admin, org="all")
        self.assertEqual(self.paths_on(response), {"/foreign"})
        self.assertNotContains(response, 'name="org"')

    def test_admin_picks_an_org_or_all_orgs(self):
        self.assertEqual(self.paths_on(self.get(self.admin)), set(PATHS) - {"/foreign"})
        self.assertEqual(self.paths_on(self.get(self.admin, org=self.other_org.id)), {"/foreign"})
        self.assertEqual(self.paths_on(self.get(self.admin, org="all")), set(PATHS))

    def test_matches_the_overview_status_counts_for_every_role(self):
        self.log(self.key, "/retired", model="not-in-the-router")
        self.log(self.others_key, "/limited", status_code=429, user_id="owner@example.com")
        self.log(self.team_key, "/invalid", status_code=400)
        self.log(self.side_key, "/broken", status_code=502)
        viewers = (
            (self.owner, {}),
            (self.other, {}),
            (self.member, {}),
            (self.org_admin, {}),
            (self.other_org_admin, {}),
            (self.admin, {"org": "all"}),
            (self.admin, {"org": self.other_org.id}),
        )
        for user, params in viewers:
            with self.subTest(user=user.username, **params):
                self.client.force_login(user)
                usage = self.client.get(reverse("usage"), {"span": "1m", **params})
                overview = {row.codes: row.count for row in usage.context["statuses"]}
                log = self.get(user, **params).context["log"]
                self.assertEqual({tab.value: tab.count for tab in log.tabs[1:]}, overview)
                self.assertEqual(log.page.paginator.count, sum(overview.values()))

    def test_overview_links_to_the_log_with_its_filters(self):
        self.client.force_login(self.admin)
        params = {"org": self.other_org.id, "token": self.foreign_key.id, "model": MODEL}
        response = self.client.get(reverse("usage"), params)
        query = f"org={self.other_org.id}&amp;key={self.foreign_key.id}&amp;model={MODEL}"
        self.assertContains(response, f'href="{reverse("requests")}?{query}"')

    def test_page_shows_no_ip_address(self):
        self.log(self.key, "/with-ip", ip_address="203.0.113.7", user_agent="probe/1")
        response = self.get(self.owner, request=Request.objects.get(path="/with-ip").id)
        self.assertContains(response, "probe/1")
        self.assertNotContains(response, "203.0.113.7")


class RequestsFilterTest(RequestsPageTestBase):
    def test_status_tabs_count_and_filter(self):
        self.log(self.key, "/limited", status_code=429)
        self.log(self.key, "/invalid", status_code=400)
        self.log(self.key, "/broken", status_code=502)
        response = self.get(self.owner, status="429")
        tabs = {tab.label: tab.count for tab in response.context["log"].tabs}
        self.assertEqual(tabs, {"All": 5, "2xx": 2, "429": 1, "Other 4xx": 1, "5xx": 1})
        content = response.content.decode()
        self.assertIn(">/limited</div>", content)
        self.assertNotIn(">/own</div>", content)
        self.assertNotIn(">/invalid</div>", content)

    def test_key_filter(self):
        response = self.get(self.owner, key=self.key.id)
        self.assertEqual(self.paths_on(response), {"/own"})

    def test_key_filter_ignores_a_key_outside_the_scope(self):
        response = self.get(self.owner, key=self.foreign_key.id)
        self.assertEqual(self.paths_on(response), {"/own", "/sent-as-owner"})
        self.assertNotContains(response, "foreign-laptop")

    def test_model_filter(self):
        self.log(self.key, "/embed", model=OTHER_MODEL)
        response = self.get(self.owner, model=OTHER_MODEL)
        content = response.content.decode()
        self.assertIn(">/embed</div>", content)
        self.assertNotIn(">/own</div>", content)

    def test_detail_opens_only_for_a_request_on_the_page(self):
        own = Request.objects.get(path="/own")
        own.user_agent = "openai-python/1.51"
        own.processing_time_ms = 12
        own.output_tokens = 300
        own.response_time_ms = 2000
        own.save()
        closed = self.get(self.owner)
        self.assertNotContains(closed, "openai-python/1.51")
        opened = self.get(self.owner, request=own.id)
        self.assertContains(opened, "openai-python/1.51")
        self.assertContains(opened, "150 tok/s")
        self.assertContains(opened, "200 OK. The request succeeded.")
        foreign = Request.objects.get(path="/foreign")
        foreign.user_agent = "foreign-agent/9"
        foreign.save()
        self.assertNotContains(self.get(self.owner, request=foreign.id), "foreign-agent/9")


class RequestsPaginationTest(RequestsPageTestBase):
    def test_pages_hold_page_size_rows_newest_first(self):
        for minutes in range(PAGE_SIZE):
            self.log(self.key, f"/bulk-{minutes}", ago=timedelta(minutes=minutes))
        first = self.get(self.owner)
        content = first.content.decode()
        self.assertEqual(len(first.context["log"].rows), PAGE_SIZE)
        self.assertLess(content.index(">/bulk-0</div>"), content.index(">/bulk-1</div>"))
        self.assertIn(f"Showing 1\N{EN DASH}{PAGE_SIZE} of {PAGE_SIZE + 2}", content)
        self.assertIn('href="?page=2">Older</a>', content)

        second = self.get(self.owner, page=2)
        self.assertEqual(self.paths_on(second), {"/own", "/sent-as-owner"})
        self.assertIn('href="?page=1">Newer</a>', second.content.decode())

    def test_query_count_does_not_grow_with_rows(self):
        self.client.force_login(self.org_admin)
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse("requests"))
        for minutes in range(30):
            self.log(self.side_key, f"/bulk-{minutes}")
            self.log(self.others_key, f"/more-{minutes}")
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse("requests"))
        self.assertEqual(len(many), len(few))
