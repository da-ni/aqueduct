import shlex
from datetime import UTC, date, datetime

from django import template
from django.forms import BoundField
from django.template.defaultfilters import date as format_date
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from management.deletion import deletion
from management.icons import Icon
from management.key_activity import MISSING, Expiry
from management.models import FileObject, ServiceAccount, Team, Token
from management.privacy import render_markdown

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
        f"export OPENAI_BASE_URL={shlex.quote(gateway_url)}\n"
        f"export OPENAI_API_KEY={shlex.quote(secret)}\n"
        'curl $OPENAI_BASE_URL/models -H "Authorization: Bearer $OPENAI_API_KEY"'
    )


@register.filter
def widget(field: BoundField) -> SafeString:
    classes = field.field.widget.attrs.get("class", "")
    if not (field.errors and classes):
        return field.as_widget()
    component = classes.split()[0]
    return field.as_widget(attrs={"class": f"{classes} {component}-error"})


WHEN_FORMATS = {"date": "j M Y", "datetime": "j M Y H:i", "recent": "j M H:i"}


@register.filter(expects_localtime=True)
def when(value: object, style: str = "datetime") -> SafeString | str:
    if not isinstance(value, date):
        return ""
    if style == "relative":
        if not isinstance(value, datetime):
            raise template.TemplateSyntaxError("when:'relative' needs a datetime, not a date")
        short = ago(value, timezone.now())
    elif style in WHEN_FORMATS:
        short = format_date(value, WHEN_FORMATS[style])
    else:
        raise template.TemplateSyntaxError(
            f"unknown when style {style!r}; use date, datetime, recent, or relative"
        )
    return _time(value, short)


AGO_UNITS = (
    ("year", 365 * 86400),
    ("month", 30 * 86400),
    ("week", 7 * 86400),
    ("day", 86400),
    ("hour", 3600),
    ("minute", 60),
)


def ago(value: datetime, now: datetime) -> str:
    """How long ago, or how soon, `value` is, in one unit rounded half up: "2 days ago" for 1
    day and 18 hours, "1 day ago" for 23 hours and 40 minutes, "in 3 hours"."""
    seconds = (now - value).total_seconds()
    for index, (name, size) in enumerate(AGO_UNITS):
        if abs(seconds) >= size:
            unit, count = name, int(abs(seconds) / size + 0.5)
            if index and count >= round(AGO_UNITS[index - 1][1] / size):
                unit, count = AGO_UNITS[index - 1][0], 1
            label = f"{count}\xa0{unit}{'' if count == 1 else 's'}"
            return f"{label} ago" if seconds > 0 else f"in {label}"
    return "just now"


@register.filter
def dash(value: object) -> object:
    """`value`, or a dash when it is missing. Unlike `default`, it keeps 0 and False, which are
    values, not gaps."""
    return MISSING if value is None or value == "" else value


@register.simple_tag
def confirm(
    title: str, detail: str = "", action: str = "Delete", dismiss: str = "Cancel"
) -> SafeString:
    """Attributes that make a link or a submit button ask `title` in the shared confirm dialog
    first. `dismiss` labels the button that backs out, for when "Cancel" would read as the
    action. See `management/_confirm_dialog.html`."""
    attrs = format_html(
        'data-confirm="{}" data-confirm-detail="{}" data-confirm-action="{}"', title, detail, action
    )
    if dismiss != "Cancel":
        attrs = format_html('{} data-confirm-dismiss="{}"', attrs, dismiss)
    return attrs


@register.simple_tag
def confirm_delete(obj: Token | ServiceAccount | Team | FileObject) -> SafeString:
    """`confirm` with what deleting `obj` takes with it, the question its confirm page asks."""
    question = deletion(obj)
    return confirm(question.title, question.detail, question.action)


@register.filter
def markdown(text: str) -> SafeString:
    return render_markdown(text)


@register.filter
def expiry(value: Expiry) -> SafeString:
    label = _time(value.at, value.label) if value.at else value.label
    return format_html('<span class="{}">{}</span>', value.tone, label)


def _time(value: date, short: str) -> SafeString:
    if isinstance(value, datetime):
        utc = value.astimezone(UTC)
        machine, full = utc.isoformat(), utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        machine = full = value.isoformat()
    return format_html('<time datetime="{}" title="{}">{}</time>', machine, full, short)
