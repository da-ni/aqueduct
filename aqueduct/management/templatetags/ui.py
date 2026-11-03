import shlex
from collections.abc import Iterable

from django import template
from django.contrib.messages.storage.base import Message
from django.template.defaulttags import querystring
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

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


@register.simple_tag(takes_context=True)
def hidden_params(context: template.Context, *dropped: str) -> SafeString:
    """Hidden inputs that carry the page's query to a GET form, minus `dropped`."""
    skip = _names(dropped)
    return format_html_join(
        "",
        '<input type="hidden" name="{}" value="{}">',
        (
            (name, value)
            for name, values in context.request.GET.lists()
            for value in values
            if name not in skip
        ),
    )


@register.simple_tag(takes_context=True)
def query_with(context: template.Context, name: str, value: object, dropped: str = "") -> str:
    """`{% querystring %}` with a parameter name that comes from a variable."""
    return querystring(context, **{name: value}, **dict.fromkeys(_names((dropped,))))


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
