from collections.abc import Callable
from dataclasses import dataclass

from django.conf import settings
from django.contrib import admin
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from silk.config import SilkyConfig

from management.icons import Icon

Access = Callable[[HttpRequest], bool]


def signed_in(request: HttpRequest) -> bool:
    return request.user.is_authenticated


def django_admin(request: HttpRequest) -> bool:
    return admin.site.has_permission(request)


def org_admin(request: HttpRequest) -> bool:
    profile = getattr(request.user, "profile", None)
    return profile is not None and profile.is_org_admin(profile.org)


def mounted(url_name: str) -> bool:
    try:
        reverse(url_name)
    except NoReverseMatch:
        return False
    return True


def silk_profiling(request: HttpRequest) -> bool:
    return mounted("silk:summary") and SilkyConfig().SILKY_PERMISSIONS(request.user)


def terms_of_service(request: HttpRequest) -> bool:
    return mounted("tos")


@dataclass(frozen=True)
class Page:
    label: str
    url_name: str
    icon: Icon
    access: Access = signed_in
    url_families: tuple[str, ...] = ()
    external: bool = False

    def is_active(self, view_name: str) -> bool:
        return any(
            view_name == family or view_name.startswith((f"{family}_", f"{family}:"))
            for family in self.url_families or (self.url_name,)
        )


@dataclass(frozen=True)
class Group:
    title: str | None
    pages: tuple[Page, ...]


NAV: tuple[Group, ...] = (
    Group(
        None,
        (
            Page("Overview", "usage", Icon.OVERVIEW),
            Page("Models", "models", Icon.MODELS),
            Page("MCP servers", "mcp_servers", Icon.MCP),
            Page("API keys", "tokens", Icon.KEY, url_families=("tokens", "token")),
            Page("Requests", "requests", Icon.REQUESTS),
            Page("Teams", "org", Icon.TEAMS, url_families=("org", "team", "service_account")),
        ),
    ),
    Group(
        "Resources",
        (
            Page("Files", "files", Icon.FILE, url_families=("files", "file")),
            Page("Batches", "batches", Icon.BATCH),
            Page(
                "Vector stores",
                "vector_stores",
                Icon.VECTOR_STORE,
                url_families=("vector_stores", "vector_store"),
            ),
        ),
    ),
    Group(
        "Admin",
        (
            Page("Organization", "organization", Icon.ORGANIZATION, access=org_admin),
            Page(
                "Admin panel",
                "admin:index",
                Icon.ADMIN,
                access=django_admin,
                url_families=("admin",),
                external=True,
            ),
            Page(
                "Profiling",
                "silk:summary",
                Icon.PROFILING,
                access=silk_profiling,
                url_families=("silk",),
                external=True,
            ),
        ),
    ),
)
NAV_BOTTOM: tuple[Page, ...] = (Page("Settings", "settings", Icon.SETTINGS),)
HELP: tuple[Page, ...] = (Page("Terms of service", "tos", Icon.FILE, access=terms_of_service),)


@dataclass(frozen=True)
class Link:
    label: str
    href: str
    icon: Icon
    active: bool = False
    external: bool = False


@dataclass(frozen=True)
class Section:
    title: str | None
    links: tuple[Link, ...]


@dataclass(frozen=True)
class Viewer:
    email: str
    role: str | None


@dataclass(frozen=True)
class Shell:
    sections: tuple[Section, ...]
    bottom: tuple[Link, ...]
    help_links: tuple[Link, ...]
    viewer: Viewer | None
    gateway_url: str

    @property
    def current(self) -> Link | None:
        nav = (link for section in self.sections for link in section.links)
        return next((link for link in (*nav, *self.bottom, *self.help_links) if link.active), None)


def gateway_url(request: HttpRequest) -> str:
    return f"{request.scheme}://{request.get_host()}/v1"


def build_shell(request: HttpRequest) -> Shell:
    view_name = request.resolver_match.view_name if request.resolver_match else ""

    def visible(pages: tuple[Page, ...]) -> tuple[Link, ...]:
        allowed = [page for page in pages if page.access(request)]
        return tuple(
            Link(
                page.label,
                reverse(page.url_name),
                page.icon,
                page.is_active(view_name),
                page.external,
            )
            for page in allowed
        )

    extra = tuple(
        Link(name, url, Icon.LINK, external=True) for name, url in settings.EXTRA_NAV_LINKS.items()
    )
    return Shell(
        sections=tuple(
            Section(group.title, links) for group in NAV if (links := visible(group.pages))
        ),
        bottom=extra + visible(NAV_BOTTOM),
        help_links=extra + visible(HELP),
        viewer=_viewer(request),
        gateway_url=gateway_url(request),
    )


def _viewer(request: HttpRequest) -> Viewer | None:
    user = request.user
    if not user.is_authenticated:
        return None
    try:
        role: str | None = user.profile.group.label
    except (ObjectDoesNotExist, ValidationError):
        role = None
    return Viewer(email=user.email, role=role)
