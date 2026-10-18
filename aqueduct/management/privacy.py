from django.db.models import F, Q, QuerySet
from django.http import HttpRequest
from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

from management.models import Org, PrivacyNotice
from management.request_log import chosen_org

# `html=False` escapes raw HTML, and markdown-it drops `javascript:`, `vbscript:`, `file:`, and
# `data:` link targets.
_markdown = MarkdownIt("commonmark", {"html": False}).enable("table").disable("image")


def _link_open(
    renderer: RendererHTML, tokens: list[Token], idx: int, options: OptionsDict, env: EnvType
) -> str:
    tokens[idx].attrSet("rel", "noopener noreferrer")
    return renderer.renderToken(tokens, idx, options, env)


_markdown.add_render_rule("link_open", _link_open)


def render_markdown(text: str) -> SafeString:
    return mark_safe(_markdown.render(text))  # noqa: S308 - the renderer escapes all HTML


def viewer_org(request: HttpRequest) -> Org | None:
    profile = getattr(request.user, "profile", None)
    return chosen_org(profile, request) if profile is not None else None


def notices_for(request: HttpRequest) -> QuerySet[PrivacyNotice]:
    org = viewer_org(request)
    scope = Q(org=None) | Q(org=org) if org is not None else Q(org=None)
    return PrivacyNotice.objects.filter(scope).order_by(F("org").asc(nulls_first=True))
