from datetime import timedelta
from unittest import mock

from django.urls import reverse

from management.tests.test_api_keys_pages import make_token
from management.tests.test_usage_page import EMBEDDING, KEY_NAMES, UsagePageTestBase


class KeyUsagePageTestBase(UsagePageTestBase):
    def keys_page(self, user, **params):
        self.client.force_login(user)
        return self.client.get(reverse("usage_keys"), params)

    def names(self, user, **params) -> list[str]:
        response = self.keys_page(user, **params)
        self.assertEqual(response.status_code, 200)
        return [key.name for key in response.context["keys"]]


class KeyUsageAccessTest(KeyUsagePageTestBase):
    def test_signed_out_goes_to_sign_in(self):
        url = reverse("usage_keys")
        self.assertRedirects(
            self.client.get(url), f"{reverse('sso')}?next={url}", fetch_redirect_response=False
        )

    def test_user_sees_only_their_own_key(self):
        response = self.keys_page(self.solo)
        self.assert_keys(response, "solo-laptop")
        self.assertContains(response, f'href="{reverse("token_detail", args=[self.solo_key.id])}"')
        self.assertNotContains(response, "My keys")

    def test_team_member_sees_their_key_and_the_team_service_account(self):
        self.assert_keys(self.keys_page(self.member), "member-laptop", "pipeline")

    def test_org_admin_sees_their_org_but_links_no_other_users_key(self):
        response = self.keys_page(self.org_admin)
        self.assert_keys(response, "solo-laptop", "member-laptop", "pipeline")
        self.assertContains(
            response, f'href="{reverse("token_detail", args=[self.pipeline_key.id])}"'
        )
        for key in (self.solo_key, self.member_key):
            self.assertNotContains(response, reverse("token_detail", args=[key.id]))

    def test_my_keys_narrows_an_org_admin_to_their_own_keys(self):
        self.assertEqual(self.names(self.org_admin, scope="mine"), [])

    def test_admin_sees_every_org(self):
        self.assert_keys(self.keys_page(self.admin, org="all"), *KEY_NAMES)
        self.assertEqual(self.names(self.admin, org=self.other_org.id), ["foreign-laptop"])

    def test_org_admin_cannot_pick_another_org(self):
        self.assertEqual(self.names(self.other_org_admin, org=self.org.id), ["foreign-laptop"])

    def test_a_key_parameter_does_not_narrow_or_widen_the_list(self):
        self.assertEqual(self.names(self.solo, key=self.foreign_key.id), ["solo-laptop"])
        self.assertEqual(
            self.names(self.org_admin, key=self.solo_key.id),
            ["pipeline", "member-laptop", "solo-laptop"],
        )


class KeyUsageSearchTest(KeyUsagePageTestBase):
    def test_search_matches_key_names_within_what_the_viewer_sees(self):
        self.assertEqual(self.names(self.solo, q="laptop"), ["solo-laptop"])
        self.assertEqual(self.names(self.member, q="LAPTOP"), ["member-laptop"])
        self.assertEqual(self.names(self.org_admin, q="laptop"), ["member-laptop", "solo-laptop"])
        self.assertEqual(self.names(self.other_org_admin, q="solo"), [])

    def test_only_a_viewer_who_sees_the_whole_org_searches_by_owner_email(self):
        # team-admin created the pipeline key; a member must not learn that by searching.
        self.assertEqual(self.names(self.member, q="team-admin@"), [])
        self.assertEqual(self.names(self.org_admin, q="team-admin@"), ["pipeline"])
        self.assertEqual(self.names(self.org_admin, q="foreigner@"), [])
        self.assertEqual(self.names(self.admin, org="all", q="foreigner@"), ["foreign-laptop"])

    def test_search_counts_the_matching_keys_and_keeps_the_period_share(self):
        response = self.keys_page(self.org_admin, q="pipeline")
        self.assertEqual(response.context["count"], 1)
        self.assertContains(response, "1 key")
        (pipeline,) = response.context["keys"]
        self.assertEqual((pipeline.share, pipeline.width), ("53%", 100))

    def test_search_without_a_match_says_so(self):
        self.assertContains(self.keys_page(self.solo, q="nothing"), "No key that matches")


class KeyUsageOrderTest(KeyUsagePageTestBase):
    def setUp(self):
        super().setUp()
        for hours in (2, 3):
            self.log(self.solo_key, timedelta(hours=hours), 200, input_tokens=10)

    def test_keys_sort_by_tokens_by_default(self):
        self.assertEqual(self.names(self.org_admin), ["pipeline", "member-laptop", "solo-laptop"])

    def test_keys_sort_by_requests_or_name(self):
        self.assertEqual(
            self.names(self.org_admin, sort="requests"),
            ["solo-laptop", "pipeline", "member-laptop"],
        )
        self.assertEqual(
            self.names(self.org_admin, sort="name"), ["member-laptop", "pipeline", "solo-laptop"]
        )
        self.assertEqual(
            self.names(self.org_admin, sort="bogus"), ["pipeline", "member-laptop", "solo-laptop"]
        )

    def test_filters_narrow_the_list(self):
        self.log(self.member_key, timedelta(days=10), 200, input_tokens=99_000)
        self.log(self.member_key, timedelta(hours=1), 200, model=EMBEDDING, input_tokens=5)
        self.assertEqual(self.names(self.org_admin, span="1m")[0], "member-laptop")
        self.assertEqual(self.names(self.org_admin, model=EMBEDDING), ["member-laptop"])
        self.assertEqual(self.names(self.org_admin, span="1h"), [])


@mock.patch("management.views.usage.PAGE_SIZE", 2)
class KeyUsagePagingTest(KeyUsagePageTestBase):
    def setUp(self):
        super().setUp()
        for n in range(3):
            key = make_token(self.solo, f"spare-{n}")
            self.log(key, timedelta(hours=1), 200, input_tokens=10 - n)

    def test_pages_hold_page_size_keys_in_order(self):
        self.assertEqual(self.names(self.solo), ["solo-laptop", "spare-0"])
        self.assertEqual(self.names(self.solo, page=2), ["spare-1", "spare-2"])
        response = self.keys_page(self.solo, page=2)
        self.assertEqual((response.context["count"], response.context["pages"]), (4, 2))
        self.assertContains(response, "page 2 of 2")

    def test_a_page_past_the_end_or_unreadable_shows_the_nearest_page(self):
        self.assertEqual(self.names(self.solo, page=99), ["spare-1", "spare-2"])
        self.assertEqual(self.names(self.solo, page="x"), ["solo-laptop", "spare-0"])
        self.assertEqual(self.names(self.solo, page="0"), ["solo-laptop", "spare-0"])

    def test_bars_scale_to_the_busiest_key_on_every_page(self):
        widths = [key.width for key in self.keys_page(self.solo, page=2).context["keys"]]
        self.assertEqual(widths, [1, 1])

    def test_paging_keeps_the_filters_and_a_filter_change_goes_back_to_page_one(self):
        response = self.keys_page(self.solo, page=1, span="1m", sort="name", q="spare")
        self.assertContains(response, 'href="?page=2&amp;span=1m&amp;sort=name&amp;q=spare"')
        response = self.keys_page(self.solo, page=2, span="1m")
        self.assertContains(response, 'href="?span=1d"')


class KeyUsageMatchesOverviewTest(KeyUsagePageTestBase):
    def test_first_rows_are_the_overviews_top_keys(self):
        for n in range(12):
            key = make_token(self.org_admin, f"busy-{n}")
            self.log(key, timedelta(hours=1), 200, input_tokens=100 * (n + 1), output_tokens=n)
        for params in ({}, {"span": "1m"}, {"model": EMBEDDING}, {"scope": "mine"}):
            top = self.page(self.org_admin, **params).context["keys"]
            listed = self.keys_page(self.org_admin, **params).context["keys"]
            self.assertEqual(listed[: len(top)], top, params)

    def test_overview_links_to_all_keys_with_its_filters(self):
        response = self.page(self.org_admin, span="1m", model=EMBEDDING, scope="mine")
        url = f"{reverse('usage_keys')}?scope=mine&amp;model={EMBEDDING}&amp;span=1m"
        self.assertContains(response, f'href="{url}">View all keys')
