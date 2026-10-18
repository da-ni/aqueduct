from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, modify_settings, override_settings
from django.urls import include, path, reverse
from tos.models import TermsOfService

from aqueduct.urls import urlpatterns as aqueduct_urlpatterns
from management.models import Org, PrivacyNotice, UserGroup, UserProfile
from management.tests.test_api_keys_pages import PAGES

User = get_user_model()

urlpatterns = [path("terms-of-service/", include("tos.urls")), *aqueduct_urlpatterns]

PLACEHOLDER = "No data protection information has been published yet."
LINK = "Data protection"


@PAGES
class PrivacyPageTest(TestCase):
    def setUp(self):
        self.org = Org.objects.create(name="Informatics")
        self.other_org = Org.objects.create(name="Elsewhere")
        self.member = self.user("member")
        self.outsider = self.user("outsider", org=self.other_org)

    def user(self, name: str, group: UserGroup = UserGroup.USER, org: Org | None = None):
        user = User.objects.create_user(username=name, email=f"{name}@example.com")
        UserProfile.objects.create(user=user, org=org or self.org).group = group
        return user

    def page(self, user=None, **params):
        if user is not None:
            self.client.force_login(user)
        response = self.client.get(reverse("privacy"), params)
        self.assertEqual(response.status_code, 200)
        return response

    def footer_links(self, response) -> set[str]:
        return {link.label for link in response.context["shell"].help_links}

    def test_signed_out_visitors_read_the_site_wide_notice(self):
        PrivacyNotice.objects.create(content="# Data we keep\n\nWe log **requests**.")
        response = self.page()
        self.assertContains(response, "<h1>Data we keep</h1>", html=True)
        self.assertContains(response, "<strong>requests</strong>", html=True)
        self.assertContains(response, "Last updated")
        self.assertNotContains(response, PLACEHOLDER)

    def platform_admin(self):
        admin = self.user("admin", UserGroup.ADMIN)
        admin.is_staff = admin.is_superuser = True
        admin.save()
        return admin

    def test_every_visitor_finds_the_link_with_or_without_a_notice(self):
        org_admin = self.user("org-admin", UserGroup.ORG_ADMIN)
        admin = self.platform_admin()
        for has_notice in (False, True):
            if has_notice:
                PrivacyNotice.objects.create(content="Site-wide.")
            for user in (None, self.member, org_admin, admin):
                with self.subTest(has_notice=has_notice, user=user):
                    self.client.logout()
                    self.assertIn(LINK, self.footer_links(self.page(user)))
            self.client.logout()
            self.assertContains(self.client.get(reverse("sso")), f'href="{reverse("privacy")}"')

    def test_without_a_notice_only_platform_admins_get_the_hint_to_add_one(self):
        add_url = reverse("admin:management_privacynotice_add")
        for user in (None, self.member, self.user("org-admin", UserGroup.ORG_ADMIN)):
            with self.subTest(user=user):
                self.client.logout()
                response = self.page(user)
                self.assertContains(response, PLACEHOLDER)
                self.assertNotContains(response, add_url)
        response = self.page(self.platform_admin())
        self.assertContains(response, PLACEHOLDER)
        self.assertContains(response, f'href="{add_url}"')
        PrivacyNotice.objects.create(content="Site-wide.")
        self.assertNotContains(self.page(), add_url)

    def test_german_visitors_read_datenschutzinformation(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "de"
        response = self.page()
        self.assertContains(response, "Datenschutzinformation</h1>")
        self.assertContains(
            response, "Es wurden noch keine Datenschutzinformationen veröffentlicht."
        )
        self.assertIn("Datenschutz", self.footer_links(response))

    def test_markdown_cannot_inject_html_or_script_links(self):
        PrivacyNotice.objects.create(
            content=(
                "<script>alert(1)</script>\n\n"
                '<a href="https://example.org" onclick="steal()">raw</a>\n\n'
                "[js](javascript:alert(1)) [data](data:text/html;base64,PHNjcmlwdD4=)\n\n"
                "![pixel](https://tracker.example/p.png)\n\n"
                "[contact](https://example.org/privacy)"
            )
        )
        response = self.page()
        content = response.content.decode()
        self.assertNotIn("<script>alert", content)
        self.assertNotIn('onclick="', content)
        self.assertNotIn('href="javascript:', content)
        self.assertNotIn('href="data:', content)
        self.assertNotIn('src="https://tracker', content)
        self.assertContains(
            response,
            '<a href="https://example.org/privacy" rel="noopener noreferrer">contact</a>',
            html=True,
        )

    def test_org_addendum_shows_only_to_that_orgs_members(self):
        PrivacyNotice.objects.create(content="Site-wide text.")
        PrivacyNotice.objects.create(org=self.org, content="Informatics keeps logs 30 days.")
        member = self.page(self.member)
        self.assertContains(member, "Site-wide text.")
        self.assertContains(member, "For members of Informatics")
        self.assertContains(member, "Informatics keeps logs 30 days.")
        for user in (None, self.outsider):
            with self.subTest(user=user):
                self.client.logout()
                response = self.page(user)
                self.assertContains(response, "Site-wide text.")
                self.assertNotContains(response, "Informatics keeps logs")

    def test_admins_see_the_addendum_of_the_org_they_switched_to(self):
        admin = self.user("admin", UserGroup.ADMIN)
        PrivacyNotice.objects.create(org=self.other_org, content="Elsewhere addendum.")
        self.assertNotContains(self.page(admin), "Elsewhere addendum.")
        self.assertContains(self.page(org=self.other_org.id), "Elsewhere addendum.")
        self.assertNotContains(self.page(org="all"), "Elsewhere addendum.")

    def test_one_notice_per_scope(self):
        PrivacyNotice.objects.create(content="Site-wide.")
        PrivacyNotice.objects.create(org=self.org, content="Addendum.")
        for org in (None, self.org):
            with self.subTest(org=org), self.assertRaises(ValidationError):
                PrivacyNotice(org=org, content="Second.").full_clean()
        PrivacyNotice(org=self.other_org, content="Other addendum.").full_clean()

    @override_settings(ROOT_URLCONF=__name__)
    @modify_settings(MIDDLEWARE={"append": "management.middleware.UserAgreementMiddleware"})
    def test_users_can_read_it_before_accepting_the_terms(self):
        TermsOfService.objects.create(active=True, content="Terms.")
        PrivacyNotice.objects.create(content="Site-wide.")
        self.client.force_login(self.member)
        self.assertRedirects(
            self.client.get(reverse("settings")),
            f"{reverse('tos_check_tos')}?next={reverse('settings')}",
            fetch_redirect_response=False,
        )
        self.assertContains(self.client.get(reverse("privacy")), "Site-wide.")
        self.assertContains(self.client.get(reverse("tos_check_tos")), reverse("privacy"))
