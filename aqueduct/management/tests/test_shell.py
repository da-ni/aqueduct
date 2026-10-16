import re
from http import HTTPStatus
from pathlib import Path
from typing import ClassVar

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template, TemplateSyntaxError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from management.nav import HELP, NAV, NAV_BOTTOM, Shell, build_shell, silk_profiling

User = get_user_model()

ROUTER_CONFIG_PATH = Path(settings.BASE_DIR).parent / "example_router_config.yaml"

FIXTURE_USER_PK_BY_ROLE = {"anonymous": None, "user": 3, "org-admin": 2, "admin": 1}


def every_page():
    return [*(page for group in NAV for page in group.pages), *NAV_BOTTOM, *HELP]


def request_as(user):
    request = RequestFactory().get("/")
    request.user = user
    return request


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    LITELLM_ROUTER_CONFIG_FILE_PATH=ROUTER_CONFIG_PATH,
)
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

                allowed = set()
                for page in every_page():
                    try:
                        url = reverse(page.url_name)
                    except NoReverseMatch:
                        continue
                    if self.client.get(url).status_code == HTTPStatus.OK:
                        allowed.add(url)

                self.assertSetEqual(shown, allowed)

    def test_admin_group_appears_only_for_staff(self):
        self.sign_in("user")
        self.assertNotIn("Admin", [s.title for s in self.shell_on(reverse("tokens")).sections])
        self.sign_in("admin")
        admin = next(s for s in self.shell_on(reverse("tokens")).sections if s.title == "Admin")
        self.assertEqual([link.label for link in admin.links], ["Admin panel"])

    def test_extra_nav_links_are_shown_signed_out(self):
        shell = self.shell_on(reverse("sso"))
        self.assertIsNone(shell.viewer)
        self.assertEqual(shell.sections, ())
        self.assertEqual(
            [(link.label, link.href) for link in shell.bottom],
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
        self.assertIn("<title>API keys · Aqueduct AI Gateway</title>", html)

    def test_signed_out_page_has_no_sidebar(self):
        page = self.client.get(reverse("sso"))
        self.assertNotContains(page, "drawer-side")
        self.assertNotContains(page, "Sign out")
        for url in settings.EXTRA_NAV_LINKS.values():
            self.assertContains(page, f'href="{url}"')

    def test_admin_keeps_a_top_bar_with_the_shared_nav(self):
        self.sign_in("admin")
        page = self.client.get(reverse("admin:index"))
        self.assertContains(page, '<header class="navbar')
        self.assertContains(page, f'href="{reverse("tokens")}"')
        self.assertContains(page, "Sign out")


class SilkProfilingAccessTest(TestCase):
    fixtures: ClassVar[list[str]] = ["gateway_data.json"]

    def test_follows_silks_own_permission_check(self):
        admin, user = User.objects.get(pk=1), User.objects.get(pk=3)
        with override_settings(SILKY_ENABLED=True):
            self.assertTrue(silk_profiling(request_as(admin)))
            self.assertFalse(silk_profiling(request_as(user)))
            self.assertFalse(silk_profiling(request_as(AnonymousUser())))
        with override_settings(SILKY_ENABLED=False):
            self.assertFalse(silk_profiling(request_as(admin)))

    @override_settings(SILKY_ENABLED=False, TOS_ENABLED=False)
    def test_unmounted_pages_are_never_reversed(self):
        shell = build_shell(request_as(User.objects.get(pk=1)))
        links = [link for section in shell.sections for link in section.links]
        labels = {link.label for link in (*links, *shell.help_links)}
        self.assertNotIn("Profiling", labels)
        self.assertNotIn("Terms of service", labels)


class IconTagTest(TestCase):
    def test_renders_a_stroked_svg(self):
        html = Template('{% load ui %}{% icon "copy" small=True %}').render(Context())
        self.assertInHTML(
            '<svg class="aq-ic sm" viewBox="0 0 24 24" aria-hidden="true">'
            '<rect x="9" y="9" width="11" height="11" rx="2"/>'
            '<path d="M5 15V6a2 2 0 012-2h9"/></svg>',
            html,
        )

    def test_unknown_icon_raises(self):
        with self.assertRaisesMessage(TemplateSyntaxError, "unknown icon 'cpy'"):
            Template('{% load ui %}{% icon "cpy" %}').render(Context())
