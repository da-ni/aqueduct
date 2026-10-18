from django.db.models import F, Q, QuerySet
from django.http import HttpRequest
from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

from management.access import WholeOrg
from management.models import PrivacyNotice
from management.org_choice import viewer_of

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


def notices_for(request: HttpRequest) -> QuerySet[PrivacyNotice]:
    viewer = viewer_of(request)
    chosen = viewer.chosen if viewer is not None else None
    scope = Q(org=None) | Q(org=chosen.org) if isinstance(chosen, WholeOrg) else Q(org=None)
    return PrivacyNotice.objects.filter(scope).order_by(F("org").asc(nulls_first=True))
