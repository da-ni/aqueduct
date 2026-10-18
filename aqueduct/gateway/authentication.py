import logging
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend
from django.http import HttpRequest

from management.models import Token

if TYPE_CHECKING:
    from django.contrib.auth.models import User as AuthUser


logger = logging.getLogger("aqueduct")
User = get_user_model()


def token_from_request(request: HttpRequest) -> str | None:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        # No valid header, authentication cannot proceed with this backend.
        logger.debug("TokenAuthenticationBackend: No 'Bearer ' auth header found.")
        return None

    try:
        token_key = auth_header.split(" ")[1]
        if not token_key:
            raise IndexError
    except IndexError:
        logger.warning("Authentication failed: Empty or badly formatted Bearer token in header.")
        return None
    else:
        logger.debug(
            "TokenAuthenticationBackend: Attempting to authenticate with token starting with %s",
            Token._generate_preview(token_key),
        )
        return token_key


def rejection(token: Token) -> str | None:
    if token.is_expired:
        return "has expired"
    if not token.service_account_id and not token.user.is_active:
        return "belongs to a deactivated user"
    return None


def usable_token(token_key: str) -> Token | None:
    token_instance = Token.find_by_key(token_key)

    if not token_instance:
        logger.warning(
            "Authentication failed: Invalid token provided (starts with %s). "
            "Token.find_by_key returned None.",
            Token._generate_preview(token_key),
        )
        return None

    reason = rejection(token_instance)
    if reason:
        logger.warning(
            "Authentication failed: Token %s (%s) %s.",
            token_instance.name,
            token_instance.key_preview,
            reason,
        )
        return None

    return token_instance


class TokenAuthenticationBackend(BaseBackend):
    """
    Authenticates against a Bearer token provided in the Authorization header,
    using the aqueduct.management.models.Token model.
    """

    def authenticate(self, request: HttpRequest | None, **kwargs: object) -> "AuthUser | None":
        """
        Authenticates the request based on the 'Authorization: Bearer <token>' header.

        Args:
            request: The HttpRequest object.
            **kwargs: Allows for Django's internal flexibility but not used directly here.

        Returns:
            The User object associated with the token if authentication is successful.
            None otherwise.
        """
        logger.debug("TokenAuthenticationBackend: authenticate method called.")
        if request is None:
            return None
        token_key = token_from_request(request)
        if not token_key:
            return None

        token_instance = usable_token(token_key)
        if token_instance is None:
            return None
        logger.info(
            "Authentication successful via token: %s (%s) for user %s",
            token_instance.name,
            token_instance.key_preview,
            token_instance.user.email,
        )
        return token_instance.user

    def get_user(self, user_id: int) -> "AuthUser | None":
        return User.objects.filter(pk=user_id, is_active=True).first()
