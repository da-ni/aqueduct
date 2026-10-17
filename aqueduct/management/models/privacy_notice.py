from typing import ClassVar

from django.db import models
from django.db.models.functions import Coalesce

from management.models.org import Org


class PrivacyNotice(models.Model):
    """Markdown shown on the Privacy page. The notice without an org is the site-wide notice
    every visitor sees. A notice with an org is an addendum its members see below it."""

    org = models.ForeignKey(
        Org,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
        help_text="Leave empty for the site-wide notice. Choose an organization to add text "
        "that only its members see below the site-wide notice.",
    )
    content = models.TextField(
        help_text="Markdown: headings, lists, emphasis, links, and tables. Raw HTML and images "
        "are not rendered."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                Coalesce("org", 0),
                name="one_privacy_notice_per_org",
                violation_error_message="This scope already has a privacy notice. Edit that one.",
            )
        ]

    def __str__(self) -> str:
        return f"Privacy notice for {self.org}" if self.org else "Site-wide privacy notice"
