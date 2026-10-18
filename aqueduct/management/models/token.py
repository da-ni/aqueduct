import hashlib
import secrets
from typing import TYPE_CHECKING, Any, Optional

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils import timezone

from gateway.config import resolve_model_alias
from management.models.exclusions import Exclusion, excluded
from management.models.mixins import LimitSet
from management.models.service_account import ServiceAccount
from management.ownership import RELATED, loaded, of_teams, owner, personal

if TYPE_CHECKING:
    from management.models import Team, UserProfile


class Token(models.Model):
    """
    Represents an authentication token (e.g., API Key), associated with a User
    and optionally a Service Account.
    """

    name = models.CharField(verbose_name="Token name", max_length=255, null=False)
    # Link to the standard Django User model
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,  # Use settings.AUTH_USER_MODEL
        on_delete=models.CASCADE,
        related_name="custom_auth_tokens",  # Changed related_name to avoid potential clashes
    )
    # This structure implies the Token is *created by* a User, potentially *for* a Service Account.
    service_account = models.OneToOneField(
        ServiceAccount, on_delete=models.CASCADE, related_name="token", null=True, blank=True
    )
    # Store hash and preview, not the original key
    key_hash = models.CharField(
        max_length=64,  # SHA-256 hash length
        unique=True,
        editable=False,
        help_text="SHA-256 hash of the token key.",
    )
    key_preview = models.CharField(
        max_length=12,  # e.g., "T0K3..."
        editable=False,
        help_text="First few characters of the original token key for display.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)  # Optional expiry

    def __str__(self) -> str:
        if self.service_account:
            return f"'{self.name}' ({self.service_account.name})"
        return f"'{self.name}'"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Ensures key_hash and key_preview are set before the first save.
        """
        if not self.pk and (not self.key_hash or not self.key_preview):
            # This check ensures _set_new_key() was called before the first save.
            raise ValueError(
                "Token cannot be saved without key_hash and key_preview. "
                "Call _set_new_key() before saving."
            )
        super().save(*args, **kwargs)

    def reach(self) -> models.Q:
        if self.service_account:
            return of_teams([self.service_account.team_id], "token__")
        return personal(self.user_id, "token__")

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    # The clean method checking for self.user is implicitly handled by the ForeignKey
    # unless null=True is added to the user field, which doesn't seem intended here.

    @staticmethod
    def _generate_secret_key(prefix: str = "sk-") -> str:
        """Generates a unique secret token key."""
        return prefix + secrets.token_urlsafe(nbytes=32)

    @staticmethod
    def _hash_key(key: str) -> str:
        """Hashes the key using SHA-256."""
        return hashlib.sha256(key.encode()).hexdigest()

    @staticmethod
    def _generate_preview(key: str, start: int = 3, end: int = 4) -> str:
        """Generates a preview string for the token key."""
        if not key:
            return ""
        return f"{key[:start]}...{key[-end:]}"

    def _set_new_key(self) -> str:
        """
        Generates a new secret key, sets the instance's hash and preview fields.
        Returns the generated secret key. Does NOT save the instance.
        """
        secret_key = self._generate_secret_key()
        self.key_hash = self._hash_key(secret_key)
        self.key_preview = self._generate_preview(secret_key)
        return secret_key

    def regenerate_key(self) -> str:
        """
        Generates a new secret key, updates the hash and preview, saves the instance,
        and returns the *new secret key*.
        """
        new_secret_key = self._set_new_key()  # Use the helper method
        self.save(update_fields=["key_hash", "key_preview"])  # Save the changes
        return new_secret_key  # Return the original new key

    def clean(self) -> None:
        """
        Model-level validation. The user-specific token limit is checked in the form.
        """
        super().clean()

    def _owner(self) -> "Team | UserProfile":
        token = self if loaded(self) else Token.objects.select_related(*RELATED).get(pk=self.pk)
        found = owner(token)
        if found is None:
            raise ObjectDoesNotExist(f"Token {self.pk} has no owner.")
        return found

    def get_limit(self) -> "LimitSet":
        """
        Determines the effective rate limits for this token, returning a LimitSet dataclass.
        Assumes database integrity for related objects.

        Hierarchy Rules:
        - Service Account Token: Uses Team limits, falls back to Org limits.
        - User Token: Uses UserProfile limits, falls back to Org limits.
        """
        found = self._owner()
        return LimitSet.from_objects(found, found.org)

    def model_exclusion_list(self) -> frozenset[str]:
        return excluded(self._owner(), Exclusion.MODELS)

    def model_excluded(self, model: str) -> bool:
        # Resolve alias to actual model name before checking exclusion
        resolved_model = resolve_model_alias(model)
        return resolved_model in self.model_exclusion_list()

    def mcp_server_exclusion_list(self) -> frozenset[str]:
        return excluded(self._owner(), Exclusion.MCP_SERVERS)

    def mcp_server_excluded(self, server_name: str) -> bool:
        return server_name in self.mcp_server_exclusion_list()

    @classmethod
    def find_by_key(cls, key_value: str) -> Optional["Token"]:
        """
        Finds a token by its original (unhashed) key value.
        Returns the Token instance or None if not found.
        """
        if not key_value:
            return None
        try:
            hashed_key = cls._hash_key(key_value)
            # Use select_related for efficiency if you often need related objects after lookup
            return cls.objects.select_related(*RELATED).get(key_hash=hashed_key)
        except cls.DoesNotExist:
            return None
