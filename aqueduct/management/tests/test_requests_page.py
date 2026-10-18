from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from management import usage_rollup as rollup
from management.key_activity import served
from management.models import Org, Request, TeamMembership
from management.request_log import Outcome
from management.tests.helpers import CHAT, EMBEDDING, RequestsPageTestBase, listed, make_token
from management.usage_report import STATUSES, Period, Span, org_requests

User = get_user_model()


class RequestsScopeTest(RequestsPageTestBase):
    def test_matches_the_overview_status_counts_for_every_role(self):
        self.log(self.solo_key, "/retired", model="not-in-the-router")
        self.log(self.others_key, "/limited", status_code=429, user_id="solo@example.com")
        self.log(self.team_key, "/invalid", status_code=400)
        self.log(self.side_key, "/broken", status_code=502)
        viewers = (
            (self.solo, {}),
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
        params = {"org": self.other_org.id, "key": self.foreign_key.id, "model": CHAT}
        response = self.client.get(reverse("usage"), {**params, "span": "1d"})
        query = (
            f"org={self.other_org.id}&amp;key={self.foreign_key.id}&amp;model={CHAT}&amp;span=1d"
        )
        self.assertContains(response, f'href="{reverse("requests")}?{query}"')

    def test_overview_figures_open_the_log_on_the_same_requests(self):
        self.log(self.solo_key, "/limited", status_code=429)
        self.log(self.team_key, "/invalid", status_code=400, model=EMBEDDING)
        self.log(self.side_key, "/broken", status_code=502)
        self.log(self.solo_key, "/last-week", ago=timedelta(days=3))
        viewers = (
            (self.solo, {}),
            (self.member, {"span": "1d"}),
            (self.org_admin, {"span": "1h"}),
            (self.org_admin, {"scope": "mine"}),
            (self.org_admin, {"key": self.team_key.id, "span": "1m"}),
            (self.admin, {"org": "all", "model": CHAT}),
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

    def test_log_opens_on_24_hours_and_an_unknown_time_frame_falls_back_to_it(self):
        self.log(self.solo_key, "/yesterday", ago=timedelta(hours=30))
        for params in ({}, {"span": "all"}, {"span": "bogus"}):
            response = self.get(self.solo, **params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.paths_on(response), {"/own"})
            self.assertNotContains(response, ">/yesterday</div>")
            self.assertContains(response, 'aria-current="page">24 hours</a>')

    def test_a_30_day_link_still_opens_30_days(self):
        self.log(self.solo_key, "/month-old", ago=timedelta(days=29))
        self.log(self.solo_key, "/overdue", ago=timedelta(days=32))
        response = self.get(self.solo, span="1m")
        self.assertContains(response, ">/month-old</div>")
        self.assertNotContains(response, ">/overdue</div>")
        self.assertContains(response, 'aria-current="page">30 days</a>')

    def test_a_key_pages_link_to_an_older_request_opens_it(self):
        older = self.log(
            self.solo_key, "/last-week", ago=timedelta(days=5), user_agent="old-agent/1"
        )
        self.client.force_login(self.solo)
        page = self.client.get(reverse("token_detail", args=[self.solo_key.id])).content.decode()
        link = f"{reverse('requests')}?key={self.solo_key.id}&amp;span=1m&amp;request={older.id}"
        self.assertIn(link, page)
        self.assertContains(self.client.get(link.replace("&amp;", "&")), "old-agent/1")

    def test_empty_log_says_whether_filters_hide_requests(self):
        self.assertContains(
            self.get(self.solo, model=EMBEDDING), "No request matches these filters."
        )
        self.assertContains(
            self.get(self.foreigner, key=self.foreign_key.id, status="5xx"),
            "No request matches these filters.",
        )
        Request.objects.all().delete()
        self.assertContains(self.get(self.solo), "No requests in the last 24 hours.")

    def test_time_frame_limits_the_log(self):
        self.log(self.solo_key, "/last-week", ago=timedelta(days=3))
        self.assertContains(self.get(self.solo, span="1w"), ">/last-week</div>")
        recent = self.get(self.solo)
        self.assertNotContains(recent, ">/last-week</div>")
        self.assertEqual(self.paths_on(recent), {"/own"})

    def test_page_shows_no_ip_address(self):
        self.log(self.solo_key, "/with-ip", ip_address="203.0.113.7", user_agent="probe/1")
        response = self.get(self.solo, request=Request.objects.get(path="/with-ip").id)
        self.assertContains(response, "probe/1")
        self.assertNotContains(response, "203.0.113.7")


class RequestsFilterTest(RequestsPageTestBase):
    def test_status_tabs_count_and_filter(self):
        self.log(self.solo_key, "/limited", status_code=429)
        self.log(self.solo_key, "/invalid", status_code=400)
        self.log(self.solo_key, "/broken", status_code=502)
        response = self.get(self.solo, status="429")
        tabs = {tab.value: tab.count for tab in response.context["log"].tabs}
        self.assertEqual(tabs, {None: 4, "failed": 3, "2xx": 1, "429": 1, "4xx": 1, "5xx": 1})
        content = response.content.decode()
        for label, codes, *_ in STATUSES:
            self.assertIn(f'{label} <span class="font-mono text-[10px] font-normal', content)
            self.assertIn(f">{codes}</span>", content)
        self.assertIn(">/limited</div>", content)
        self.assertNotIn(">/own</div>", content)
        self.assertNotIn(">/invalid</div>", content)

    def test_model_list_of_a_user_is_what_their_keys_may_use_or_used(self):
        self.org.excluded_models = [CHAT, EMBEDDING, "dall-e-2"]
        self.org.save()
        self.team.excluded_models = ["dall-e-2"]
        self.team.merge_exclusion_lists = False
        self.team.save()
        owner = self.get(self.solo).context["filters"].models
        self.assertIn(CHAT, owner)
        self.assertNotIn(EMBEDDING, owner)
        self.assertNotIn("dall-e-2", owner)
        member = self.get(self.member).context["filters"].models
        self.assertIn(EMBEDDING, member)
        self.assertNotIn("dall-e-2", member)
        self.assertIn("dall-e-2", self.get(self.org_admin).context["filters"].models)

    def test_key_search_names_the_org_of_each_key_across_all_orgs(self):
        matches = self.matches(self.admin, "laptop", org="all")
        self.assertIn("solo-laptop · solo@example.com · lab-org", matches)
        self.assertIn("foreign-laptop · foreigner@example.com · other-org", matches)
        self.assertIn(
            "team-sa · Service account · NLP Lab · lab-org",
            self.matches(self.admin, "team", org="all"),
        )
        matches = self.matches(self.admin, "laptop", org=self.org.id)
        self.assertIn("solo-laptop · solo@example.com", matches)
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
        make_token(self.solo, "owner-spare")
        cases = (
            (self.solo, "laptop", ["solo-laptop · Personal"]),
            (self.solo, "other@example", []),
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
        response = self.get(self.org_admin, key_q="laptop", model=CHAT, span="1d", before="1,1")
        self.assertContains(response, '<details class="dropdown" open>', count=1)
        self.assertContains(response, '<input type="hidden" name="model" value="gpt-4.1-nano">')
        self.assertContains(response, '<input type="hidden" name="span" value="1d">')
        self.assertNotContains(response, 'type="hidden" name="before"')
        self.assertContains(
            response, f'href="?model={CHAT}&amp;span=1d&amp;key={self.solo_key.id}"'
        )

    def test_picker_lists_the_scopes_and_the_viewers_own_keys(self):
        admin_key = make_token(self.org_admin, "admin-laptop")
        TeamMembership.objects.create(user_profile=self.org_admin.profile, team=self.side_team)
        filters = self.get(self.org_admin).context["filters"]
        self.assertEqual([key.name for key in filters.keys], ["admin-laptop", "side-sa"])
        response = self.get(self.org_admin, key=admin_key.id)
        self.assertContains(response, ">All keys</a>")
        self.assertContains(response, ">My keys</a>")
        self.assertNotContains(response, ">Everyone</a>")
        make_token(self.solo, "owner-spare")
        response = self.get(self.solo)
        self.assertContains(response, 'aria-current="true" class="menu-active">All keys</a>')
        self.assertNotContains(response, ">My keys</a>")

    def matches(self, user, query: str, **params) -> list[str]:
        filters = self.get(user, key_q=query, **params).context["filters"]
        return [key.name + " · " + key.note for key in filters.matches]

    def test_model_cell_filters_the_log_on_its_model(self):
        response = self.get(self.solo, status="2xx")
        self.assertContains(
            response, f'href="?status=2xx&amp;model={CHAT}" title="Show only {CHAT}"', count=1
        )

    def test_detail_opens_only_for_a_request_on_the_page(self):
        own = Request.objects.get(path="/own")
        own.user_agent = "openai-python/1.51"
        own.save()
        self.assertNotContains(self.get(self.solo), "openai-python/1.51")
        opened = self.get(self.solo, request=own.id)
        self.assertContains(opened, "openai-python/1.51")
        self.assertContains(opened, f'data-copy="{own.id}"')
        foreign = Request.objects.get(path="/foreign")
        foreign.user_agent = "foreign-agent/9"
        foreign.save()
        self.assertNotContains(self.get(self.solo, request=foreign.id), "foreign-agent/9")

    def test_detail_lists_only_fields_with_a_value(self):
        bare = self.log(self.solo_key, "/bare", user_agent="", output_tokens=0)
        content = self.get(self.solo, request=bare.id).content.decode()
        details = content[content.index("<dl") : content.index("</dl>")]
        self.assertIn("Request ID", details)
        for label in ("Error", "User agent", "User ID", "Gateway overhead", "Output speed"):
            self.assertNotIn(label, details)
        self.assertNotIn("n/a", details)

    def test_detail_explains_a_failed_status_and_shows_the_rest(self):
        failed = self.log(
            self.solo_key,
            "/failed",
            status_code=429,
            user_id="alice",
            processing_time_ms=12,
            output_tokens=300,
            response_time_ms=2000,
        )
        content = self.get(self.solo, request=failed.id).content.decode()
        self.assertIn(
            '>Error</dt><dd class="text-[13px]">The key or the provider was over a limit.</dd>',
            content,
        )
        self.assertIn(">User ID</dt>", content)
        self.assertIn(">alice</dd>", content)
        self.assertIn("150 tok/s", content)

    def test_status_and_error_read_in_german(self):
        failed = self.log(self.solo_key, "/failed", status_code=429)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "de"
        content = self.get(self.solo, request=failed.id).content.decode()
        self.assertIn(
            '<span class="st-warn inline-flex items-center gap-1.5" title="Limit erreicht">',
            content,
        )
        self.assertIn(
            '>Fehler</dt><dd class="text-[13px]">'
            "Der Schlüssel oder der Anbieter hat ein Limit überschritten.</dd>",
            content,
        )
        self.assertNotIn("Too Many Requests", content)

    def test_gateway_overhead_shows_to_org_admins_and_admins_only(self):
        timed = self.log(self.team_key, "/timed", processing_time_ms=12)
        team_admin = self.team_admin
        for user, shown in (
            (self.member, False),
            (team_admin, False),
            (self.org_admin, True),
            (self.admin, True),
        ):
            with self.subTest(user=user.username):
                content = self.get(user, request=timed.id).content.decode()
                self.assertIn(">/timed</div>", content)
                self.assertEqual(">Gateway overhead</dt>" in content, shown)

    def test_key_column_shows_only_when_rows_may_differ_in_key(self):
        make_token(self.solo, "owner-desktop")
        side_member = self.user("side-member")
        self.join(side_member, self.side_team)
        for user, params, shown in (
            (side_member, {}, False),
            (self.member, {}, True),
            (self.other, {}, False),
            (self.solo, {}, True),
            (self.solo, {"key": self.solo_key.id}, False),
            (self.org_admin, {}, True),
            (self.org_admin, {"key": self.solo_key.id}, False),
            (self.admin, {"org": "all"}, True),
        ):
            with self.subTest(user=user.username, **params):
                content = self.get(user, **params).content.decode()
                self.assertEqual('<th rowspan="2">Key</th>' in content, shown)


class RequestsGermanTest(RequestsPageTestBase):
    def test_numbers_dates_and_durations_follow_german_conventions(self):
        big = self.log(
            self.solo_key,
            "/big",
            input_tokens=1_234_567,
            output_tokens=14_400,
            response_time_ms=5_640,
        )
        self.client.force_login(self.solo)
        content = self.client.get(
            reverse("requests"), {"request": big.id}, HTTP_ACCEPT_LANGUAGE="de"
        ).content.decode()
        self.assertIn("1.234.567", content)
        self.assertIn("5,6 s", content)
        self.assertIn("2.553 Token/s", content)
        months = r"(Jan\.|Feb\.|März|April|Mai|Juni|Juli|Aug\.|Sept\.|Okt\.|Nov\.|Dez\.)"
        self.assertRegex(content, rf">\d{{1,2}}\. {months} \d\d:\d\d<")


class RequestsRollupCountsTest(RequestsPageTestBase):
    def setUp(self):
        super().setUp()
        keys = (self.solo_key, self.others_key, self.team_key, self.side_key, self.foreign_key)
        statuses = (200, 302, 429, 404, 400, 500, 503, None)
        for i in range(40):
            self.log(
                keys[i % len(keys)],
                f"/old-{i}",
                ago=timedelta(hours=2 + 13 * i),
                status_code=statuses[i % len(statuses)],
                model=(CHAT, EMBEDDING, "not-in-the-router")[i % 3],
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

    def get(self, user, **params):
        return super().get(user, **{"span": Span.MONTH.value, **params})

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
            for model in (None, CHAT):
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
        own = served(Request.objects.filter(token=self.solo_key))
        self.assertEqual(self.tabs(self.org_admin, key=self.solo_key.id), self.raw_tabs(own))

    def test_users_and_team_admins_count_their_own_requests(self):
        team_admin = self.team_admin
        for user, key in (
            (self.solo, self.solo_key),
            (self.member, self.team_key),
            (team_admin, self.team_key),
        ):
            with self.subTest(user=user.username):
                own = served(Request.objects.filter(token=key))
                self.assertEqual(self.tabs(user), self.raw_tabs(own))


class RequestsSearchTest(RequestsPageTestBase):
    def test_search_matches_the_id_path_user_id_and_user_agent(self):
        own = Request.objects.get(path="/own")
        self.log(self.solo_key, "/agent", user_agent="Curl/8.5")
        self.log(self.solo_key, "/sent", user_id="Alice@Example.com")
        for q, paths in (
            (str(own.id), ["/own"]),
            ("OWN", ["/own"]),
            ("curl", ["/agent"]),
            (" alice@ ", ["/sent"]),
            ("nothing-like-it", []),
        ):
            with self.subTest(q=q):
                response = self.get(self.solo, q=q)
                self.assertEqual(listed(response), paths)
                self.assertEqual(response.context["log"].tabs[0].count, len(paths))

    def test_search_never_reaches_beyond_what_the_viewer_sees(self):
        foreign = Request.objects.get(path="/foreign")
        for user, params, paths in (
            (self.solo, {"q": str(foreign.id)}, []),
            (self.solo, {"q": "others"}, []),
            (self.solo, {"q": "solo@example.com"}, []),
            (self.member, {"q": "sa"}, ["/team-sa"]),
            (self.org_admin, {"q": "foreign"}, []),
            (self.other_org_admin, {"q": str(foreign.id)}, ["/foreign"]),
            (self.admin, {"q": "foreign", "org": "all"}, ["/foreign"]),
        ):
            with self.subTest(user=user.username, **params):
                self.assertEqual(listed(self.get(user, **params)), paths)

    def test_search_stays_within_the_filters_and_the_time_frame(self):
        self.log(self.solo_key, "/find-old", ago=timedelta(days=3))
        self.log(self.solo_key, "/find-failed", status_code=500)
        self.log(self.solo_key, "/find-other-model", model=EMBEDDING)
        self.assertEqual(len(listed(self.get(self.solo, q="find", span="1w"))), 3)
        for params, paths in (
            ({}, ["/find-failed", "/find-other-model"]),
            ({"status": "failed"}, ["/find-failed"]),
            ({"model": EMBEDDING}, ["/find-other-model"]),
        ):
            with self.subTest(**params):
                self.assertEqual(sorted(listed(self.get(self.solo, q="find", **params))), paths)

    def test_search_form_keeps_the_filters_and_starts_on_the_first_page(self):
        content = self.get(
            self.org_admin, model=CHAT, status="failed", sort="time", before="1,1", q="x"
        ).content.decode()
        form = content.split('role="search" class="flex', 1)[1].split("</form>", 1)[0]
        for name, value in (("model", CHAT), ("status", "failed"), ("sort", "time")):
            self.assertIn(f'name="{name}" value="{value}"', form)
        self.assertNotIn('name="before"', form)
        self.assertIn('name="q" value="x"', form)

    def test_text_search_needs_a_bounded_scan_and_an_id_search_does_not(self):
        own = Request.objects.get(path="/own")
        team_admin = self.team_admin
        for user, params, searches_text in (
            (self.org_admin, {"span": "1m"}, False),
            (self.admin, {"span": "1m", "org": "all"}, False),
            (self.org_admin, {"span": "1w"}, True),
            (self.org_admin, {"span": "1m", "key": self.solo_key.id}, True),
            (self.org_admin, {"span": "1m", "model": CHAT}, True),
            (self.org_admin, {"span": "1m", "scope": "mine"}, True),
            (self.solo, {"span": "1m"}, True),
            (team_admin, {"span": "1m"}, True),
        ):
            with self.subTest(user=user.username, **params):
                response = self.get(user, **params, q="/own")
                content = response.content.decode()
                self.assertEqual(response.context["log"].searches_text, searches_text)
                if searches_text:
                    self.assertIn('aria-label="Search requests"', content)
                    self.assertNotIn("to search text", content)
                    continue
                self.assertEqual(listed(response), [])
                self.assertIn('placeholder="Request ID" aria-label="Request ID"', content)
                self.assertIn("Narrow to 7 days or pick a key or model to search text.", content)
                self.assertNotIn("to search text", self.get(user, **params).content.decode())
                by_id = self.get(user, **params, q=str(own.id))
                self.assertEqual(listed(by_id), ["/own"])
                self.assertEqual(by_id.context["log"].tabs[0].count, 1)
                self.assertNotIn("to search text", by_id.content.decode())

    def test_a_blocked_text_search_leaves_the_org_wide_counts_alone(self):
        wide = {"span": "1m"}
        total = self.get(self.org_admin, **wide).context["log"].tabs[0].count
        self.assertEqual(self.get(self.org_admin, **wide, q="/own").context["log"].tabs[0].count, 0)
        self.assertEqual(self.get(self.org_admin, **wide).context["log"].tabs[0].count, total)
        self.assertEqual(
            self.get(self.org_admin, span="1w", q="/own").context["log"].tabs[0].count, 1
        )


class RequestsSortTest(RequestsPageTestBase):
    def setUp(self):
        super().setUp()
        Request.objects.filter(path="/own").delete()
        fields = (
            (300, 10, 5, 200, CHAT),
            (None, 1, 1, 500, EMBEDDING),
            (300, 2, 2, 429, CHAT),
            (100, 40, 0, None, EMBEDDING),
            (200, 3, 1, 404, CHAT),
        )
        for n, (ms, input_tokens, output_tokens, status, model) in enumerate(fields):
            self.log(
                self.solo_key,
                f"/sorted-{n}",
                ago=timedelta(minutes=n),
                response_time_ms=ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                status_code=status,
                model=model,
            )

    def test_each_column_sorts_both_ways_with_missing_values_last(self):
        for sort, order in (
            ("-time", [0, 1, 2, 3, 4]),
            ("time", [4, 3, 2, 1, 0]),
            ("-response", [2, 0, 4, 3, 1]),
            ("response", [3, 4, 0, 2, 1]),
            ("-tokens", [3, 0, 4, 2, 1]),
            ("tokens", [1, 2, 4, 0, 3]),
            ("model", [0, 2, 4, 1, 3]),
            ("-status", [1, 2, 4, 0, 3]),
        ):
            with self.subTest(sort=sort):
                expected = [f"/sorted-{n}" for n in order]
                self.assertEqual(listed(self.get(self.solo, sort=sort)), expected)

    def test_org_wide_30_days_sorts_by_time_only(self):
        for user, params in (
            (self.org_admin, {"span": "1m"}),
            (self.admin, {"org": "all", "span": "1m"}),
        ):
            with self.subTest(user=user.username, **params):
                response = self.get(user, sort="-response", **params)
                self.assertEqual(str(response.context["log"].sort), "-time")
                content = response.content.decode()
                self.assertRegex(content, r'class="text-right">Response\s*</th>')
                self.assertNotIn("To sort by", content)
                self.assertIn("sort=time", content)
                oldest = self.get(user, sort="time", **params).context["log"].rows[0]
                self.assertEqual(oldest.request.path, "/others")

    def test_a_short_time_frame_a_key_a_model_or_own_keys_allow_every_sort(self):
        for params in (
            {},
            {"span": "1w"},
            *(
                {"span": "1m", narrower: value}
                for narrower, value in (
                    ("model", CHAT),
                    ("key", self.solo_key.id),
                    ("scope", "mine"),
                )
            ),
        ):
            with self.subTest(**params):
                response = self.get(self.org_admin, sort="-response", **params)
                self.assertEqual(str(response.context["log"].sort), "-response")
                self.assertIn('">Response<svg', response.content.decode())

    def test_headings_mark_the_sort_and_toggle_it(self):
        content = self.get(self.solo).content.decode()
        self.assertEqual(content.count("aria-sort="), 1)
        self.assertIn('aria-sort="descending"><a', content)
        self.assertIn("?sort=time", content)
        content = self.get(self.solo, sort="-response").content.decode()
        self.assertEqual(content.count("aria-sort="), 1)
        self.assertIn('text-right" aria-sort="descending"', content)
        self.assertIn("?sort=response", content)
        content = self.get(self.solo, sort="model").content.decode()
        self.assertIn('aria-sort="ascending"', content)
        self.assertIn("?sort=-model", content)
