import shlex

from django import template
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


@register.filter
def key_snippet(secret: str, gateway_url: str) -> str:
    return (
        f"export AQUEDUCT_API_KEY={shlex.quote(secret)}\n"
        f"curl {shlex.quote(gateway_url + '/models')} \\\n"
        '  -H "Authorization: Bearer $AQUEDUCT_API_KEY"'
    )
