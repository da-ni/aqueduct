from enum import Enum
from http import HTTPStatus

from django.db.models import Count, Q
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

from management.icons import Icon

FAILED = Q(status_code__gte=HTTPStatus.BAD_REQUEST)


class Outcome(Enum):
    _value_: str
    label: Promise
    icon: Icon
    tone: str
    matches: Q
    meaning: Promise | None

    OK = ("2xx", _("OK"), Icon.OK, "st-ok", Q(status_code__lt=HTTPStatus.BAD_REQUEST), None)
    RATE_LIMITED = (
        "429",
        _("Rate-limited"),
        Icon.WARNING,
        "st-warn",
        Q(status_code=HTTPStatus.TOO_MANY_REQUESTS),
        _("The key or the provider was over a limit."),
    )
    CLIENT_ERROR = (
        "4xx",
        _("Client error"),
        Icon.OFF,
        "st-off",
        FAILED
        & Q(status_code__lt=HTTPStatus.INTERNAL_SERVER_ERROR)
        & ~Q(status_code=HTTPStatus.TOO_MANY_REQUESTS),
        _("The request was not valid, for example an unknown model or a malformed body."),
    )
    SERVER_ERROR = (
        "5xx",
        _("Server error"),
        Icon.FAILED,
        "st-err",
        Q(status_code__gte=HTTPStatus.INTERNAL_SERVER_ERROR),
        _("The model or the provider failed or timed out."),
    )

    def __new__(
        cls, codes: str, label: Promise, icon: Icon, tone: str, matches: Q, meaning: Promise | None
    ) -> "Outcome":
        outcome = object.__new__(cls)
        outcome._value_ = codes
        outcome.label = label
        outcome.icon = icon
        outcome.tone = tone
        outcome.matches = matches
        outcome.meaning = meaning
        return outcome

    @property
    def codes(self) -> str:
        return self._value_

    @property
    def column(self) -> str:
        return self.name.lower()

    @property
    def failed(self) -> bool:
        return self is not Outcome.OK

    @classmethod
    def of(cls, status_code: int | None) -> "Outcome | None":
        if status_code is None:
            return None
        if status_code < HTTPStatus.BAD_REQUEST:
            return cls.OK
        if status_code == HTTPStatus.TOO_MANY_REQUESTS:
            return cls.RATE_LIMITED
        if status_code < HTTPStatus.INTERNAL_SERVER_ERROR:
            return cls.CLIENT_ERROR
        return cls.SERVER_ERROR


OUTCOME_COUNTS = {o.name: Count("id", filter=o.matches) for o in Outcome}
