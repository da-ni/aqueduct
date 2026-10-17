from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from management import usage_rollup as rollup
from management.key_activity import served
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
from management.request_log import PAGE_SIZE, Outcome
from management.tests.test_api_keys_pages import PAGES, make_token
from management.usage_report import Period, Span, org_requests

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

    def test_user_sees_their_own_keys_only_even_where_a_request_names_them(self):
        response = self.get(self.owner)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.paths_on(response), {"/own"})
        self.assertContains(
            response, f'href="{reverse("token_detail", args=[self.key.id])}"', count=1
        )
        self.assertNotContains(response, "other-laptop")
        self.assertNotContains(response, "other@example.com")
        self.assertEqual(self.get(self.owner, key=self.others_key.id).status_code, 404)

    def test_other_user_does_not_see_the_owners_requests(self):
        response = self.get(self.other)
        self.assertEqual(self.paths_on(response), {"/others", "/sent-as-owner"})
        self.assertNotContains(response, "owner-laptop")

    def test_team_member_sees_their_teams_service_account_only(self):
        response = self.get(self.member)
        self.assertEqual(self.paths_on(response), {"/team-sa"})
        self.assertContains(response, f'href="{reverse("token_detail", args=[self.team_key.id])}"')
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
        self.assertEqual(self.paths_on(response), {"/own"})
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
                groups = {tab.value: tab.count for tab in log.tabs if tab.value in overview}
                self.assertEqual(groups, overview)
                self.assertEqual(log.count, sum(overview.values()))

    def test_overview_links_to_the_log_with_its_filters(self):
        self.client.force_login(self.admin)
        params = {"org": self.other_org.id, "key": self.foreign_key.id, "model": MODEL}
        response = self.client.get(reverse("usage"), {**params, "span": "1d"})
        query = (
            f"org={self.other_org.id}&amp;key={self.foreign_key.id}&amp;model={MODEL}&amp;span=1d"
        )
        self.assertContains(response, f'href="{reverse("requests")}?{query}"')

    def test_overview_figures_open_the_log_on_the_same_requests(self):
        self.log(self.key, "/limited", status_code=429)
        self.log(self.team_key, "/invalid", status_code=400, model=OTHER_MODEL)
        self.log(self.side_key, "/broken", status_code=502)
        self.log(self.key, "/last-week", ago=timedelta(days=3))
        viewers = (
            (self.owner, {}),
            (self.member, {"span": "1d"}),
            (self.org_admin, {"span": "1h"}),
            (self.org_admin, {"scope": "mine"}),
            (self.org_admin, {"key": self.team_key.id, "span": "1m"}),
            (self.admin, {"org": "all", "model": MODEL}),
        )
        for user, params in viewers:
            with self.subTest(user=user.username, **params):
                self.client.force_login(user)
                usage = self.client.get(reverse("usage"), params).context
                figures = [(row.href, row.count) for row in usage["statuses"]]
                figures += [(row.log_url, row.activity.requests) for row in usage["models"]]
                kpis = {kpi.label: kpi for kpi in usage["kpis"]}
                total = int(kpis["Requests"].value)
                failed = sum(row.count for row in usage["statuses"] if row.codes != "2xx")
                if total:
                    self.assertEqual(kpis["Failed"].value, f"{round(100 * failed / total)}%")
                figures.append((usage["filters"].log_url(), total))
                for href, count in figures:
                    log = self.client.get(href).context["log"]
                    self.assertEqual(log.count, count, href)

    def test_log_opens_on_30_days_and_an_unknown_time_frame_falls_back_to_it(self):
        self.log(self.key, "/month-old", ago=timedelta(days=29))
        self.log(self.key, "/overdue", ago=timedelta(days=32))
        for params in ({}, {"span": "all"}, {"span": "bogus"}):
            response = self.get(self.owner, **params)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, ">/month-old</div>")
            self.assertNotContains(response, ">/overdue</div>")
            self.assertContains(response, 'aria-current="page">30 days</a>')
            self.assertNotContains(response, "All retained")

    def test_start_and_end_parameters_are_ignored(self):
        response = self.get(self.owner, start="2020-01-02T00:00", end="2020-01-01T00:00")
        self.assertEqual(self.paths_on(response), {"/own"})

    def test_empty_log_says_whether_filters_hide_requests(self):
        self.assertContains(
            self.get(self.owner, model=OTHER_MODEL), "No request matches these filters."
        )
        self.assertContains(
            self.get(self.foreigner, key=self.foreign_key.id, status="5xx"),
            "No request matches these filters.",
        )
        Request.objects.all().delete()
        self.assertContains(self.get(self.owner), "No requests in the last 30 days.")

    def test_time_frame_limits_the_log(self):
        self.log(self.key, "/last-week", ago=timedelta(days=3))
        self.assertContains(self.get(self.owner), ">/last-week</div>")
        recent = self.get(self.owner, span="1d")
        self.assertNotContains(recent, ">/last-week</div>")
        self.assertEqual(self.paths_on(recent), {"/own"})

    def test_my_keys_narrows_the_log_for_an_org_admin(self):
        self.log(make_token(self.org_admin, "admin-laptop"), "/admin-own")
        response = self.get(self.org_admin, scope="mine")
        self.assertEqual(self.paths_on(response), set())
        self.assertIn(">/admin-own</div>", response.content.decode())

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
        self.assertEqual(
            tabs, {"All": 4, "Failed": 3, "2xx": 1, "429": 1, "Other 4xx": 1, "5xx": 1}
        )
        content = response.content.decode()
        self.assertIn(">/limited</div>", content)
        self.assertNotIn(">/own</div>", content)
        self.assertNotIn(">/invalid</div>", content)

    def test_key_filter(self):
        response = self.get(self.owner, key=self.key.id)
        self.assertEqual(self.paths_on(response), {"/own"})

    def test_key_search_names_the_org_of_each_key_across_all_orgs(self):
        matches = self.matches(self.admin, "laptop", org="all")
        self.assertIn("owner-laptop · owner@example.com · lab-org", matches)
        self.assertIn("foreign-laptop · foreigner@example.com · other-org", matches)
        self.assertIn(
            "team-sa · Service account · NLP Lab · lab-org",
            self.matches(self.admin, "team", org="all"),
        )
        matches = self.matches(self.admin, "laptop", org=self.org.id)
        self.assertIn("owner-laptop · owner@example.com", matches)
        self.assertNotIn("lab-org", " ".join(matches))

    def test_all_orgs_finds_a_key_whose_owner_has_no_profile(self):
        root = User.objects.create_superuser("root", "root@example.com", "pw")
        self.log(make_token(root, "root-laptop"), "/profileless")
        self.assertIn("/profileless", self.get(self.admin, org="all").content.decode())
        self.assertEqual(
            self.matches(self.admin, "root", org="all"),
            ["root-laptop · root@example.com · No organization"],
        )

    def test_key_search_finds_only_keys_the_viewer_may_see(self):
        make_token(self.owner, "owner-spare")
        cases = (
            (self.owner, "laptop", ["owner-laptop · Personal"]),
            (self.owner, "other@example", []),
            (self.member, "sa", ["team-sa · Service account · NLP Lab"]),
            (self.sa_creator, "sa", []),
            (self.other_org_admin, "laptop", ["foreign-laptop · foreigner@example.com"]),
            (self.other_org_admin, "owner", []),
            (self.org_admin, "OTHER@", ["other-laptop · other@example.com"]),
            (
                self.org_admin,
                "sa-creator@",
                ["side-sa · Service account · Robotics", "team-sa · Service account · NLP Lab"],
            ),
            (self.member, "sa-creator@", []),
        )
        for user, query, expected in cases:
            with self.subTest(user=user.username, query=query):
                self.assertEqual(self.matches(user, query), expected)

    def test_key_search_opens_the_picker_and_keeps_the_other_filters(self):
        response = self.get(self.org_admin, key_q="laptop", model=MODEL, span="1d", before="1,1")
        self.assertContains(response, '<details class="dropdown" open>', count=1)
        self.assertContains(response, '<input type="hidden" name="model" value="gpt-4.1-nano">')
        self.assertContains(response, '<input type="hidden" name="span" value="1d">')
        self.assertNotContains(response, 'type="hidden" name="before"')
        self.assertContains(response, f'href="?model={MODEL}&amp;span=1d&amp;key={self.key.id}"')

    def test_picker_lists_the_scopes_and_the_viewers_own_keys(self):
        admin_key = make_token(self.org_admin, "admin-laptop")
        TeamMembership.objects.create(user_profile=self.org_admin.profile, team=self.side_team)
        filters = self.get(self.org_admin).context["filters"]
        self.assertEqual([key.name for key in filters.keys], ["admin-laptop", "side-sa"])
        response = self.get(self.org_admin, key=admin_key.id)
        self.assertContains(response, ">All keys in lab-org</a>")
        self.assertContains(response, ">My keys</a>")
        self.assertNotContains(response, ">Everyone</a>")
        response = self.get(self.owner)
        self.assertContains(response, 'aria-current="true" class="menu-active">All my keys</a>')
        self.assertNotContains(response, ">My keys</a>")

    def matches(self, user, query: str, **params) -> list[str]:
        filters = self.get(user, key_q=query, **params).context["filters"]
        return [key.name + " · " + key.note for key in filters.matches]

    def test_key_outside_the_scope_is_not_found(self):
        response = self.get(self.owner, key=self.foreign_key.id)
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "foreign-laptop", status_code=404)
        self.assertEqual(self.get(self.owner, key="abc").status_code, 404)

    def test_model_cell_filters_the_log_on_its_model(self):
        response = self.get(self.owner, status="2xx")
        self.assertContains(
            response, f'href="?status=2xx&amp;model={MODEL}" title="Show only {MODEL}"', count=1
        )

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
        self.assertContains(opened, f'data-copy="{own.id}"')
        foreign = Request.objects.get(path="/foreign")
        foreign.user_agent = "foreign-agent/9"
        foreign.save()
        self.assertNotContains(self.get(self.owner, request=foreign.id), "foreign-agent/9")


class RequestsPaginationTest(RequestsPageTestBase):
    def test_pages_hold_page_size_rows_newest_first(self):
        now = timezone.now()
        for n in range(PAGE_SIZE + 1):
            tied = timedelta(minutes=min(n, PAGE_SIZE - 1))
            Request.objects.create(
                token=self.key,
                path=f"/bulk-{n}",
                model=MODEL,
                status_code=200,
                timestamp=now - tied,
            )
        first = self.get(self.owner)
        content = first.content.decode()
        self.assertEqual(len(first.context["log"].rows), PAGE_SIZE)
        self.assertLess(content.index(">/bulk-0</div>"), content.index(">/bulk-1</div>"))
        self.assertIn(f"{PAGE_SIZE + 2} requests", content)
        self.assertNotIn(">Newer</a>", content)

        second = self.client.get(self.pager_link(content, "Older"))
        self.assertEqual(self.paths_on(second), {"/own"})
        self.assertIn(f">/bulk-{PAGE_SIZE}</div>", content)
        self.assertIn(f">/bulk-{PAGE_SIZE - 1}</div>", second.content.decode())
        self.assertNotIn(">Older</a>", second.content.decode())

        back = self.client.get(self.pager_link(second.content.decode(), "Newer"))
        self.assertEqual(
            [row.request.path for row in back.context["log"].rows],
            [row.request.path for row in first.context["log"].rows],
        )

    def test_a_linked_request_opens_on_a_page_that_holds_it(self):
        for minutes in range(PAGE_SIZE + 5):
            self.log(self.key, f"/bulk-{minutes}", ago=timedelta(minutes=minutes))
        own = Request.objects.get(path="/own")
        response = self.get(self.owner, key=self.key.id, request=own.id)
        log = response.context["log"]
        self.assertEqual([row.request.id for row in log.rows if row.is_open], [own.id])
        content = response.content.decode()
        self.assertIn(">Newer</a>", content)
        self.assertIn(urlencode({"before": log.here}), content)
        closed = self.client.get(self.pager_link(content, f"Hide details of request {own.id}"))
        self.assertEqual(
            closed.context["log"].rows,
            tuple(type(row)(row.request, row.key, False) for row in log.rows),
        )

    def test_a_linked_request_outside_the_viewers_scope_stays_closed(self):
        foreign = Request.objects.get(path="/foreign")
        log = self.get(self.owner, request=foreign.id).context["log"]
        self.assertEqual(log.here, None)
        self.assertFalse(any(row.is_open for row in log.rows))

    def test_a_malformed_cursor_or_id_shows_the_newest_page(self):
        huge = "9" * 30
        cursors = ("abc", "1,", ",1", "-5,3", "\N{SUPERSCRIPT ONE},1", "1,\N{SUPERSCRIPT ONE}")
        for cursor in (*cursors, f"{huge},1", f"1,{huge}", f"{10**17},1"):
            for direction in ("before", "after"):
                with self.subTest(cursor=cursor, direction=direction):
                    response = self.get(self.owner, **{direction: cursor})
                    self.assertEqual(self.paths_on(response), {"/own"})
        for raw in ("\N{SUPERSCRIPT ONE}", huge):
            with self.subTest(request=raw):
                self.assertEqual(self.paths_on(self.get(self.owner, request=raw)), {"/own"})
            with self.subTest(org=raw):
                self.assertEqual(self.get(self.admin, org=raw).status_code, 200)
            with self.subTest(key=raw):
                self.assertEqual(self.get(self.owner, key=raw).status_code, 404)

    def test_a_page_past_the_oldest_request_links_back_to_the_newest(self):
        content = self.get(self.owner, before="1,1").content.decode()
        self.assertEqual(self.paths_on(self.get(self.owner, before="1,1")), set())
        self.assertIn(">Newer</a>", content)
        self.assertEqual(
            self.paths_on(self.client.get(self.pager_link(content, "Newer"))), {"/own"}
        )

    def pager_link(self, content: str, label: str) -> str:
        if label.startswith("Hide"):
            anchor = content.split(f'aria-label="{label}"', maxsplit=1)[0].rsplit("<a ", 1)[1]
        else:
            anchor = content.split(f">{label}</a>", maxsplit=1)[0].rsplit("<a ", 1)[1]
        href = anchor.split('href="', 1)[1].split('"', 1)[0].replace("&amp;", "&")
        return reverse("requests") + href

    def test_paginator_reuses_the_tab_counts(self):
        self.log(self.key, "/limited", status_code=429)
        self.client.force_login(self.org_admin)
        for params in ({}, {"status": "429"}):
            with self.subTest(**params), CaptureQueriesContext(connection) as queries:
                log = self.client.get(reverse("requests"), params).context["log"]
            tab = next(tab for tab in log.tabs if tab.value == params.get("status"))
            self.assertEqual(log.count, tab.count)
            counts = [q["sql"] for q in queries if "COUNT(" in q["sql"].upper()]
            self.assertEqual(len(counts), 1, counts)

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


class RequestsRollupCountsTest(RequestsPageTestBase):
    def setUp(self):
        super().setUp()
        TeamMembership.objects.create(
            user_profile=self.user("team-admin").profile, team=self.team, is_admin=True
        )
        keys = (self.key, self.others_key, self.team_key, self.side_key, self.foreign_key)
        statuses = (200, 302, 429, 404, 400, 500, 503, None)
        for i in range(40):
            self.log(
                keys[i % len(keys)],
                f"/old-{i}",
                ago=timedelta(hours=2 + 13 * i),
                status_code=statuses[i % len(statuses)],
                model=(MODEL, OTHER_MODEL, "not-in-the-router")[i % 3],
            )
        rollup.rebuild()

    def raw_tabs(self, requests: QuerySet[Request]) -> dict[str | None, int]:
        requests = Period.last(Span.MONTH, timezone.now()).of(requests)
        return {None: requests.count()} | {
            o.value: requests.filter(o.matches).count() for o in Outcome
        }

    def org_tabs(self, org: Org | None, model: str | None = None) -> dict[str | None, int]:
        requests = org_requests(org)
        return self.raw_tabs(requests if model is None else requests.filter(model=model))

    def tabs(self, user, **params) -> dict[str | None, int]:
        log = self.get(user, **params).context["log"]
        return {tab.value: tab.count for tab in log.tabs}

    def test_org_wide_tabs_match_the_raw_counts(self):
        viewers = (
            (self.org_admin, {}, self.org),
            (self.other_org_admin, {}, self.other_org),
            (self.admin, {}, self.org),
            (self.admin, {"org": self.other_org.id}, self.other_org),
            (self.admin, {"org": "all"}, None),
        )
        for user, params, org in viewers:
            for model in (None, MODEL):
                narrowed = {**params, **({"model": model} if model else {})}
                with self.subTest(user=user.username, **narrowed):
                    found = self.tabs(user, **narrowed)
                    self.assertEqual(found, self.org_tabs(org, model))
                    for status in ("failed", "429"):
                        log = self.get(user, **narrowed, status=status).context["log"]
                        self.assertEqual(log.count, found[status])

    def test_org_wide_tabs_read_the_rollups_and_narrower_ones_the_requests(self):
        Request.objects.filter(path="/old-0").update(status_code=500)
        self.assertEqual(self.tabs(self.org_admin)["2xx"], self.org_tabs(self.org)["2xx"] + 1)
        mine = Request.objects.none()
        self.assertEqual(self.tabs(self.org_admin, scope="mine"), self.raw_tabs(mine))
        own = served(Request.objects.filter(token=self.key))
        self.assertEqual(self.tabs(self.org_admin, key=self.key.id), self.raw_tabs(own))

    def test_users_and_team_admins_count_their_own_requests(self):
        team_admin = User.objects.get(username="team-admin")
        for user, key in (
            (self.owner, self.key),
            (self.member, self.team_key),
            (team_admin, self.team_key),
        ):
            with self.subTest(user=user.username):
                own = served(Request.objects.filter(token=key))
                self.assertEqual(self.tabs(user), self.raw_tabs(own))
