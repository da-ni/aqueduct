import shlex
from collections.abc import Iterable
from datetime import UTC, datetime

from django import template
from django.contrib.messages.storage.base import Message
from django.template.defaultfilters import date as format_date
from django.template.defaulttags import querystring
from django.urls import reverse
from django.utils import dateformat
from django.utils.html import format_html, format_html_join
from django.utils.http import urlencode
from django.utils.safestring import SafeString

from management.formatting import grouped
from management.icons import Icon

register = template.Library()


@register.simple_tag
def icon(name: Icon | str, small: bool = False, extra_class: str = "") -> SafeString:
    if isinstance(name, str):
        try:
            name = Icon[name.upper()]
        except KeyError:
            raise template.TemplateSyntaxError(
                f"unknown icon {name!r}; add it to management/icons.py"
            ) from None
    classes = " ".join(filter(None, ("aq-ic", "sm" if small else "", extra_class)))
    body = format_html_join(
        "",
        "<{} {}/>",
        ((tag, format_html_join(" ", '{}="{}"', attrs)) for tag, attrs in name.value),
    )
    return format_html(
        '<svg class="{}" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">{}</svg>',
        classes,
        body,
    )


@register.simple_tag
def copy_button(
    value: str, label: str, title: str = "", size: str = "xs", extra_class: str = ""
) -> SafeString:
    classes = " ".join(filter(None, ("btn btn-ghost", f"btn-{size}", "btn-square", extra_class)))
    return format_html(
        '<button type="button" class="{}" data-copy="{}" aria-label="{}" title="{}">{}</button>',
        classes,
        value,
        label,
        title or label,
        icon(Icon.COPY, small=True),
    )


RESET_ON_FILTER = frozenset({"page", "key_q", "before", "after", "request"})


@register.simple_tag(takes_context=True)
def hidden_params(
    context: template.Context, *dropped: str, keep: Iterable[tuple[str, str]] | None = None
) -> SafeString:
    """Hidden inputs that carry a query to a GET form, minus `dropped` and paging.

    `keep` names the parameters to carry; without it, the page's own query is carried.
    """
    skip = _names(dropped) | RESET_ON_FILTER
    pairs = (
        keep
        if keep is not None
        else ((name, value) for name, values in context.request.GET.lists() for value in values)
    )
    return format_html_join(
        "",
        '<input type="hidden" name="{}" value="{}">',
        ((name, value) for name, value in pairs if name not in skip),
    )


@register.simple_tag(takes_context=True)
def query_with(context: template.Context, name: str, value: object, dropped: str = "") -> str:
    """`{% querystring %}` with a parameter name that comes from a variable."""
    return querystring(context, **{name: value}, **dict.fromkeys(_names((dropped,))))


@register.simple_tag
def url_with(viewname: str, *args: object, **query: object) -> str:
    return f"{reverse(viewname, args=args)}?{urlencode(query)}"


def _names(groups: Iterable[str]) -> set[str]:
    return {name for group in groups for name in group.split()}


@register.filter
def key_snippet(secret: str, gateway_url: str) -> str:
    return (
        f"export OPENAI_BASE_URL={shlex.quote(gateway_url)}\n"
        f"export OPENAI_API_KEY={shlex.quote(secret)}\n"
        'curl $OPENAI_BASE_URL/models -H "Authorization: Bearer $OPENAI_API_KEY"'
    )


@register.filter
def is_new_key(message: Message) -> bool:
    return message.extra_tags == "token-regenerated-key"


register.filter("grouped", grouped)


@register.filter(expects_localtime=True)
def when(value: object) -> SafeString | str:
    if not isinstance(value, datetime):
        return ""
    utc = value.astimezone(UTC)
    return format_html(
        '<time datetime="{}" title="{} UTC">{}</time>',
        utc.isoformat(),
        dateformat.format(utc, "Y-m-d H:i:s"),
        format_date(value, "j M H:i"),
    )
