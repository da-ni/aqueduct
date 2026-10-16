from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

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
    return format_html(
        '<svg class="{}" viewBox="0 0 24 24" aria-hidden="true">{}</svg>',
        classes,
        mark_safe(name.value),  # noqa: S308 -- constant markup from management/icons.py
    )
