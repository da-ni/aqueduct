import re
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.test import override_settings
from django.urls import reverse

from management.tests.helpers import RoleWorld

OIDC = override_settings(
    OIDC_RP_CLIENT_ID="aqueduct", OIDC_OP_AUTHORIZATION_ENDPOINT="https://sso.example.com/authorize"
)

UNSAFE_NEXT = (
    "https://evil.example",
    "//evil.example",
    "/\\evil.example",
    "http:evil.example",
    "javascript:alert(1)",
    "",
)


@OIDC
class LandingTest(RoleWorld):
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
        landing = self.sign_in(self.solo, next="https://evil.example")
        self.assertRedirects(landing, reverse("usage"))

    def test_signed_in_visit_to_the_login_page_lands_on_the_overview(self):
        for role, user in self.roles().items():
            with self.subTest(role):
                self.client.force_login(user)
                self.assertRedirects(self.client.get(reverse("sso")), reverse("usage"))

    def test_signed_in_visit_to_the_login_page_follows_a_next_on_this_site(self):
        self.client.force_login(self.solo)
        page = f"{reverse('requests')}?span=1d&status=500"
        self.assertRedirects(
            self.client.get(reverse("sso"), {"next": page}), page, fetch_redirect_response=False
        )

    def test_signed_in_visit_to_the_login_page_never_fails_on_a_bare_next(self):
        self.client.force_login(self.solo)
        for bare in ("foo", "?x", "#frag"):
            with self.subTest(next=bare):
                response = self.client.get(reverse("sso"), {"next": bare})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], bare)

    def test_signed_in_visit_to_the_login_page_ignores_a_next_that_leaves_the_site(self):
        self.client.force_login(self.solo)
        for unsafe in UNSAFE_NEXT:
            with self.subTest(next=unsafe):
                response = self.client.get(reverse("sso"), {"next": unsafe})
                self.assertRedirects(response, reverse("usage"), fetch_redirect_response=False)

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
