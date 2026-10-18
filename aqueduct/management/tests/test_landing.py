import re
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.test import override_settings
from django.urls import reverse

from management.tests.test_usage_page import UsagePageTestBase

OIDC = override_settings(
    OIDC_RP_CLIENT_ID="aqueduct", OIDC_OP_AUTHORIZATION_ENDPOINT="https://sso.example.com/authorize"
)


@OIDC
class LandingTest(UsagePageTestBase):
    def roles(self):
        return {"admin": self.admin, "org admin": self.org_admin, "user": self.solo}

    def sign_in(self, user, **params):
        self.client.logout()
        login = self.client.get(reverse("sso"), params)
        init = re.search(r'href="([^"]*)" class="btn btn-primary', login.content.decode())
        assert init is not None, "no login button"
        provider = self.client.get(init.group(1).replace("&amp;", "&"))
        state = parse_qs(urlparse(provider["Location"]).query)["state"][0]
        user.backend = "django.contrib.auth.backends.ModelBackend"
        with mock.patch("mozilla_django_oidc.views.auth.authenticate", return_value=user):
            return self.client.get(
                reverse("oidc_authentication_callback"), {"code": "c", "state": state}
            )

    def test_every_role_lands_on_the_overview(self):
        for role, user in self.roles().items():
            with self.subTest(role):
                landing = self.sign_in(user)
                self.assertRedirects(landing, reverse("usage"))

    def test_sign_in_returns_to_the_page_that_asked_for_it(self):
        page = f"{reverse('requests')}?span=1d"
        self.assertRedirects(self.sign_in(self.solo, next=page), page)

    def test_sign_in_ignores_a_next_outside_the_site(self):
        landing = self.sign_in(self.solo, next="https://evil.example.com/")
        self.assertRedirects(landing, reverse("usage"))

    def test_signed_in_visit_to_the_login_page_lands_on_the_overview(self):
        for role, user in self.roles().items():
            with self.subTest(role):
                self.client.force_login(user)
                self.assertRedirects(self.client.get(reverse("sso")), reverse("usage"))

    def test_signed_in_visit_to_the_login_page_follows_only_a_safe_next(self):
        self.client.force_login(self.solo)
        page = reverse("tokens")
        self.assertRedirects(self.client.get(reverse("sso"), {"next": page}), page)
        unsafe = self.client.get(reverse("sso"), {"next": "//evil.example.com/"})
        self.assertRedirects(unsafe, reverse("usage"))

    def test_site_root_opens_the_overview(self):
        self.client.force_login(self.solo)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("usage"))

    def test_django_admin_turns_away_a_signed_in_user_who_is_not_staff(self):
        self.client.force_login(self.solo)
        response = self.client.get(reverse("admin:index"), follow=True)
        self.assertEqual(response.status_code, 403)

    def test_django_admin_login_sends_signed_in_staff_on_to_the_admin(self):
        self.admin.is_staff = True
        self.admin.save()
        self.client.force_login(self.admin)
        login = self.client.get(reverse("admin_sso"), {"next": reverse("admin:index")})
        self.assertRedirects(login, reverse("admin:index"), fetch_redirect_response=False)

    def test_sign_in_restores_the_language_saved_in_the_profile(self):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
        self.solo.profile.language = "de"
        self.solo.profile.save()
        landing = self.sign_in(self.solo)
        self.assertEqual(landing.cookies[settings.LANGUAGE_COOKIE_NAME].value, "de")
        page = self.client.get(reverse("settings"), HTTP_ACCEPT_LANGUAGE="en")
        self.assertContains(page, '<html lang="de"')

    def test_sign_in_without_a_saved_language_leaves_the_browser_choice(self):
        landing = self.sign_in(self.solo)
        self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, landing.cookies)
        page = self.client.get(reverse("settings"), HTTP_ACCEPT_LANGUAGE="de")
        self.assertContains(page, '<html lang="de"')
