import re
from http import HTTPStatus
from typing import ClassVar

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render
from django.template import Context, Template, TemplateSyntaxError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import NoReverseMatch, include, path, reverse
from tos.models import TermsOfService

from aqueduct.urls import urlpatterns as aqueduct_urlpatterns
from gateway.tests.utils.base import ROUTER_CONFIG_PATH
from management.nav import HELP, NAV, NAV_BOTTOM, Shell, build_shell

User = get_user_model()


FIXTURE_USER_PK_BY_ROLE = {"anonymous": None, "user": 3, "org-admin": 2, "admin": 1}


def every_page():
    return [*(page for group in NAV for page in group.pages), *NAV_BOTTOM, *HELP]


def labels(shell: Shell) -> set[str]:
    links = [link for section in shell.sections for link in section.links]
    return {link.label for link in (*links, *shell.bottom, *shell.help_links)}


def request_as(user):
    request = RequestFactory().get("/")
    request.user = user
    request.session = SessionStore()
    return request


def forbidden(request):
    raise PermissionDenied


def with_messages(request):
    messages.success(request, "Saved.")
    messages.error(request, "Could not save.")
    messages.success(request, "sk-secret", extra_tags="token-regenerated-key")
    return render(request, "management/base.html")


urlpatterns = [
    path("forbidden/", forbidden),
    path("with-messages/", with_messages),
    path("silk/", include(([path("", lambda request: HttpResponse(), name="summary")], "silk"))),
    path("terms-of-service/", include("tos.urls")),
    *aqueduct_urlpatterns,
]


@override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=ROUTER_CONFIG_PATH)
class ShellTest(TestCase):
    fixtures: ClassVar[list[str]] = ["gateway_data.json"]

    def sign_in(self, role):
        self.client.logout()
        if FIXTURE_USER_PK_BY_ROLE[role] is not None:
            self.client.force_login(User.objects.get(pk=FIXTURE_USER_PK_BY_ROLE[role]))

    def shell_on(self, url) -> Shell:
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.context["shell"]

    def home(self, role):
        return reverse("sso") if FIXTURE_USER_PK_BY_ROLE[role] is None else reverse("tokens")

    def test_nav_shows_exactly_the_pages_the_viewer_may_open(self):
        extra = set(settings.EXTRA_NAV_LINKS.values())
        mounted, unmounted = set(), set()
        for page in every_page():
            try:
                mounted.add(reverse(page.url_name))
            except NoReverseMatch:
                unmounted.add(page.label)
        for role in FIXTURE_USER_PK_BY_ROLE:
            with self.subTest(role=role):
                self.sign_in(role)
                shell = self.shell_on(self.home(role))
                links = [link for section in shell.sections for link in section.links]
                shown = {
                    link.href
                    for link in (*links, *shell.bottom, *shell.help_links)
                    if link.href not in extra
                }
                allowed = {
                    url for url in mounted if self.client.get(url).status_code == HTTPStatus.OK
                }

                self.assertSetEqual(shown, allowed)
                self.assertSetEqual(labels(shell) & unmounted, set())

    @override_settings(TOS_ENABLED=True, SILKY_ENABLED=True)
    def test_flags_without_their_routes_do_not_break_the_page(self):
        self.sign_in("admin")
        shown = labels(self.shell_on(reverse("tokens")))
        self.assertNotIn("Terms of service", shown)
        self.assertNotIn("Profiling", shown)

    def test_admin_group_appears_only_for_org_admins_and_staff(self):
        self.sign_in("user")
        self.assertNotIn("Admin", [s.title for s in self.shell_on(reverse("tokens")).sections])
        self.sign_in("admin")
        admin = next(s for s in self.shell_on(reverse("tokens")).sections if s.title == "Admin")
        self.assertEqual([link.label for link in admin.links], ["Admin panel"])

    @override_settings(AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"])
    def test_staff_without_superuser_get_the_admin_panel(self):
        admin_url = reverse("admin:index")
        user = User.objects.get(pk=FIXTURE_USER_PK_BY_ROLE["user"])
        self.client.force_login(user)
        page = self.client.get(reverse("tokens"))
        self.assertNotContains(page, ">Admin</li>")
        self.assertNotContains(page, f'href="{admin_url}"')
        self.assertEqual(self.client.get(admin_url).status_code, HTTPStatus.FOUND)

        user.is_staff = True
        user.save()
        page = self.client.get(reverse("tokens"))
        self.assertContains(page, ">Admin</li>")
        self.assertContains(page, f'href="{admin_url}"')
        self.assertEqual(self.client.get(admin_url).status_code, HTTPStatus.OK)

    def test_extra_nav_links_are_shown_signed_out(self):
        shell = self.shell_on(reverse("sso"))
        self.assertIsNone(shell.viewer)
        self.assertEqual(shell.sections, ())
        self.assertEqual(
            [(link.label, link.href) for link in shell.help_links],
            list(settings.EXTRA_NAV_LINKS.items()),
        )

    def test_current_page_follows_url_families(self):
        self.sign_in("admin")
        cases = {
            reverse("tokens"): "API keys",
            reverse("token_create"): "API keys",
            reverse("team", kwargs={"id": 1}): "Teams",
            reverse("usage"): "Overview",
            reverse("admin:index"): "Admin panel",
        }
        for url, label in cases.items():
            with self.subTest(url=url):
                self.assertEqual(self.shell_on(url).current.label, label)

    def test_viewer_shows_email_and_role(self):
        self.sign_in("org-admin")
        viewer = self.shell_on(reverse("tokens")).viewer
        self.assertEqual((viewer.email, viewer.role), ("someone@example.com", "Org Admin"))

    def test_user_without_profile_still_gets_the_frame(self):
        self.client.force_login(User.objects.create_user("no-profile", "np@example.com"))
        self.assertIsNone(self.shell_on(reverse("models")).viewer.role)

    def test_gateway_url_is_the_openai_base_url(self):
        self.sign_in("user")
        self.assertEqual(self.shell_on(reverse("tokens")).gateway_url, "http://testserver/v1")

    def test_sidebar_links_to_django_admin_only_for_staff(self):
        admin_url = reverse("admin:index")
        self.sign_in("user")
        page = self.client.get(reverse("tokens"))
        self.assertContains(page, 'class="drawer-side')
        self.assertNotContains(page, f'href="{admin_url}"')
        self.assertNotContains(page, ">Admin</li>")
        self.sign_in("admin")
        page = self.client.get(reverse("tokens"))
        self.assertContains(page, f'href="{admin_url}"')
        self.assertContains(page, ">Admin</li>")

    def test_current_page_is_marked_and_titled(self):
        self.sign_in("user")
        html = self.client.get(reverse("token_create")).content.decode()
        current = re.findall(r'<a href="([^"]*)"[^>]*aria-current="page"', html)
        self.assertEqual(current, [reverse("tokens")])
        self.assertIn("<title>New API key · API keys · Aqueduct AI Gateway</title>", html)

    def test_signed_out_page_has_no_sidebar(self):
        page = self.client.get(reverse("sso"))
        self.assertNotContains(page, "drawer-side")
        self.assertNotContains(page, "Sign out")
        for url in settings.EXTRA_NAV_LINKS.values():
            self.assertContains(page, f'href="{url}"', count=1)

    def test_help_links_live_in_the_footer_and_the_gateway_url_only_signed_in(self):
        self.sign_in("user")
        page = self.client.get(reverse("tokens"))
        self.assertEqual(page.context["shell"].bottom, ())
        for url in settings.EXTRA_NAV_LINKS.values():
            self.assertContains(page, f'href="{url}"', count=1)
        self.assertContains(page, "http://testserver/v1")
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("sso")), "http://testserver/v1")

    def test_contact_icons_show_unless_their_url_is_empty(self):
        links = {
            "Matrix support channel": "https://matrix.to/#/#support:example.org",
            "GitHub repository": "https://github.com/TU-Wien-dataLAB/aqueduct",
        }
        for role in ("anonymous", "user"):
            self.sign_in(role)
            with self.subTest(role=role):
                with self.settings(MATRIX_SUPPORT_URL=links["Matrix support channel"]):
                    page = self.client.get(self.home(role))
                for label, url in links.items():
                    self.assertContains(page, f'href="{url}"', count=1)
                    self.assertContains(page, f"<span>{label}</span>", count=1)
                with self.settings(MATRIX_SUPPORT_URL="", GITHUB_REPOSITORY_URL=""):
                    page = self.client.get(self.home(role))
                for label in links:
                    self.assertNotContains(page, label)

    def test_admin_keeps_a_top_bar_with_the_shared_nav(self):
        self.sign_in("admin")
        page = self.client.get(reverse("admin:index"))
        self.assertContains(page, '<header class="navbar')
        self.assertContains(page, f'href="{reverse("tokens")}"')
        self.assertContains(page, "Sign out")


@override_settings(ROOT_URLCONF=__name__)
class MountedFlagPagesTest(TestCase):
    fixtures: ClassVar[list[str]] = ["gateway_data.json"]

    def test_follow_their_access_checks_once_mounted(self):
        self.assertLessEqual(
            {"Profiling", "Terms of service"},
            labels(build_shell(request_as(User.objects.get(pk=1)))),
        )
        for user in (User.objects.get(pk=3), AnonymousUser()):
            with self.subTest(user=user):
                shown = labels(build_shell(request_as(user)))
                self.assertIn("Terms of service", shown)
                self.assertNotIn("Profiling", shown)


@override_settings(ROOT_URLCONF=__name__)
class TermsPagesTest(TestCase):
    fixtures: ClassVar[list[str]] = ["gateway_data.json"]

    def test_terms_sit_in_the_shell_and_the_confirmation_loads_its_assets(self):
        TermsOfService.objects.create(active=True, content="<p>Be kind.</p>")
        self.client.force_login(User.objects.get(pk=FIXTURE_USER_PK_BY_ROLE["user"]))
        terms = self.client.get(reverse("tos"))
        self.assertTemplateUsed(terms, "management/_sidebar.html")
        self.assertContains(terms, "Be kind.")
        confirmation = self.client.get(reverse("tos_check_tos"))
        self.assertEqual(confirmation.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(confirmation, "management/_cdn.html")


class IconTagTest(TestCase):
    def test_renders_a_stroked_svg(self):
        html = Template('{% load ui %}{% icon "copy" small=True %}').render(Context())
        self.assertInHTML(
            '<svg class="aq-ic sm" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">'
            '<rect x="9" y="9" width="11" height="11" rx="2"/>'
            '<path d="M5 15V6a2 2 0 012-2h9"/></svg>',
            html,
        )

    def test_unknown_icon_raises(self):
        with self.assertRaisesMessage(TemplateSyntaxError, "unknown icon 'cpy'"):
            Template('{% load ui %}{% icon "cpy" %}').render(Context())


@override_settings(ROOT_URLCONF=__name__)
class ErrorPagesTest(TestCase):
    fixtures: ClassVar[list[str]] = ["gateway_data.json"]

    def test_render_in_the_shell_for_anonymous_and_signed_in_visitors(self):
        cases = (
            (
                "/aqueduct/management/no-such-page/",
                HTTPStatus.NOT_FOUND,
                "Page not found",
                "This page doesn't exist or you can't open it.",
            ),
            (
                "/forbidden/",
                HTTPStatus.FORBIDDEN,
                "Access denied",
                "You don't have access. Ask your org admin.",
            ),
        )
        for role in ("anonymous", "user"):
            if FIXTURE_USER_PK_BY_ROLE[role] is not None:
                self.client.force_login(User.objects.get(pk=FIXTURE_USER_PK_BY_ROLE[role]))
            for url, status, heading, body in cases:
                with self.subTest(url=url, role=role):
                    page = self.client.get(url)
                    self.assertEqual(page.status_code, status)
                    html = page.content.decode()
                    self.assertIn(f"<title>{heading} · Aqueduct AI Gateway</title>", html)
                    self.assertIn(f">{heading}</h1>", html)
                    self.assertIn(f"<p>{body}</p>", html)
                    self.assertIn(f'href="{reverse("usage")}">Go to Overview</a>', html)
                    self.assertEqual("drawer-side" in html, role != "anonymous")


@override_settings(ROOT_URLCONF=__name__)
class MessagesTest(TestCase):
    def test_success_leaves_by_itself_errors_stay_and_the_new_key_is_not_a_toast(self):
        html = self.client.get("/with-messages/").content.decode()
        page, toasts = html.split('<div class="fixed bottom-4')
        alerts = {m[1]: m[0] for m in re.finditer(r'<div class="alert alert-(\w+)[^>]*>', toasts)}
        self.assertEqual(set(alerts), {"success", "error"})
        self.assertIn("data-autodismiss", alerts["success"])
        self.assertNotIn("data-autodismiss", alerts["error"])
        self.assertEqual(toasts.count('aria-label="Dismiss"'), 2)
        self.assertNotIn("sk-secret", toasts)
        self.assertIn("sk-secret", page)
