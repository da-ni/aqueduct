from enum import Enum
from http import HTTPStatus

from django.db.models import Q

FAILED = Q(status_code__gte=HTTPStatus.BAD_REQUEST)


class Outcome(Enum):
    _value_: str
    matches: Q

    OK = ("2xx", Q(status_code__lt=HTTPStatus.BAD_REQUEST))
    RATE_LIMITED = ("429", Q(status_code=HTTPStatus.TOO_MANY_REQUESTS))
    CLIENT_ERROR = (
        "4xx",
        FAILED
        & Q(status_code__lt=HTTPStatus.INTERNAL_SERVER_ERROR)
        & ~Q(status_code=HTTPStatus.TOO_MANY_REQUESTS),
    )
    SERVER_ERROR = ("5xx", Q(status_code__gte=HTTPStatus.INTERNAL_SERVER_ERROR))

    def __new__(cls, codes: str, matches: Q) -> "Outcome":
        outcome = object.__new__(cls)
        outcome._value_ = codes
        outcome.matches = matches
        return outcome

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
