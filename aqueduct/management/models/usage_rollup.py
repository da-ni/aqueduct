from typing import ClassVar

from django.db import models

from management.models.org import Org
from management.models.token import Token

HOURLY = "org-hours"


class OrgUsageHour(models.Model):
    hour = models.DateTimeField()
    org = models.ForeignKey(
        Org, null=True, on_delete=models.DO_NOTHING, related_name="+", db_constraint=False
    )
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
    hour = models.DateTimeField()
    org = models.ForeignKey(
        Org, null=True, on_delete=models.DO_NOTHING, related_name="+", db_constraint=False
    )
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
    days = models.PositiveSmallIntegerField()
    window_start = models.DateTimeField()
    sealed_until = models.DateTimeField()
    token = models.ForeignKey(
        Token, on_delete=models.DO_NOTHING, related_name="+", db_constraint=False
    )
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
    name = models.CharField(max_length=32, primary_key=True)
    sealed_until = models.DateTimeField(null=True)
    dirty = models.BooleanField(default=False)
    changes = models.PositiveBigIntegerField(default=0)
    time_zone = models.CharField(max_length=64)

    def __str__(self) -> str:
        return self.name
