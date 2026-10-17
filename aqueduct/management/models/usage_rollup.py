from typing import ClassVar

from django.db import models

from management.models.org import Org
from management.models.token import Token

HOURLY = "org-hours"
"""The `UsageRollupState` of `OrgUsageHour` and `OrgLatencyHour`."""


class OrgUsageHour(models.Model):
    """The requests of one org's keys for one model in one local hour, counted per status group
    of `usage_report.STATUSES`. `unfinished` counts requests without a status. A key belongs to
    its service account's team's org, else to its owner's org; `org` is empty for an owner
    without a profile."""

    hour = models.DateTimeField()
    org = models.ForeignKey(Org, null=True, on_delete=models.CASCADE, related_name="+")
    model = models.CharField(max_length=255)
    ok = models.PositiveIntegerField()
    rate_limited = models.PositiveIntegerField()
    client_error = models.PositiveIntegerField()
    server_error = models.PositiveIntegerField()
    unfinished = models.PositiveIntegerField()
    input_tokens = models.PositiveBigIntegerField()
    cached_input_tokens = models.PositiveBigIntegerField()
    output_tokens = models.PositiveBigIntegerField()

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["org", "hour", "model"], name="org_usage_hour_unique")
        ]

    def __str__(self) -> str:
        return f"{self.org_id} {self.model} {self.hour:%Y-%m-%d %H:%M}"


class OrgLatencyHour(models.Model):
    """How many requests of one org, model, and local hour took a response time in `bucket`.
    See `usage_rollup.BUCKET` for the bucket bounds."""

    hour = models.DateTimeField()
    org = models.ForeignKey(Org, null=True, on_delete=models.CASCADE, related_name="+")
    model = models.CharField(max_length=255)
    bucket = models.SmallIntegerField()
    requests = models.PositiveIntegerField()

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["org", "hour", "model", "bucket"], name="org_latency_hour_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.org_id} {self.model} {self.hour:%Y-%m-%d %H:%M} bucket {self.bucket}"


class KeyUsageWindow(models.Model):
    """One key's requests for one model over the last `days` days, from the `window_start` to
    the `sealed_until` of the `UsageRollupState` named `usage_rollup.key_window_name(days)`."""

    days = models.PositiveSmallIntegerField()
    token = models.ForeignKey(Token, on_delete=models.CASCADE, related_name="+")
    model = models.CharField(max_length=255)
    requests = models.PositiveIntegerField()
    failed = models.PositiveIntegerField()
    input_tokens = models.PositiveBigIntegerField()
    cached_input_tokens = models.PositiveBigIntegerField()
    output_tokens = models.PositiveBigIntegerField()

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["days", "token", "model"], name="key_usage_window_unique"
            )
        ]

    def __str__(self) -> str:
        return f"{self.token_id} {self.model} {self.days} days"


class UsageRollupState(models.Model):
    """What one rollup holds: requests before `sealed_until` (and from `window_start`, for a key
    window), bucketed in `time_zone`. `dirty` asks the next refresh for a full rebuild. The
    refresh locks this row."""

    name = models.CharField(max_length=32, primary_key=True)
    sealed_until = models.DateTimeField(null=True)
    window_start = models.DateTimeField(null=True)
    dirty = models.BooleanField(default=False)
    time_zone = models.CharField(max_length=64)

    def __str__(self) -> str:
        return self.name
