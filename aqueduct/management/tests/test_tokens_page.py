"""Tests for the per-window request-usage indicators on the API key detail page.

Covers the ``_rate_usage.html`` include driven by
``gateway.rate_limiting.get_per_token_usage`` on ``TokenDetailView``.
"""

from typing import ClassVar

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from gateway.rate_limiting import check_and_reserve
from management.models import LimitSet

User = get_user_model()


class TokensPageTest(TestCase):
    fixtures: ClassVar[list[str]] = ["gateway_data.json"]

    def setUp(self) -> None:
        cache.clear()

    def tearDown(self) -> None:
        cache.clear()

    def test_page_renders_usage_progress_bars(self):
        user = User.objects.get(pk=1)
        profile = user.profile

        # Default multipliers (hour=60, day=1440) so the hour/day caps are
        # derived from the per-minute cap: 10/min -> 600/hour, 14400/day.
        # 6 reserved requests then read as min 60%, hour 1%, day ~0% — verifying
        # that the coarser windows scale independently of the per-minute cap.
        profile.requests_per_minute = 10
        profile.hourly_limit_multiplier = None  # -> settings default 60
        profile.daily_limit_multiplier = None  # -> settings default 1440
        profile.save(
            update_fields=[
                "requests_per_minute",
                "hourly_limit_multiplier",
                "daily_limit_multiplier",
            ]
        )

        token_id = 1  # "My Token" from the fixture
        limits = LimitSet.from_objects(profile, profile.org)
        for _ in range(6):
            allowed, _ = check_and_reserve(limits, token_id, model=None)
            self.assertTrue(allowed)

        self.client.force_login(user)
        resp = self.client.get(reverse("token_detail", args=[1]))

        self.assertEqual(resp.status_code, 200)
        # The hour and day windows each render a progress bar with the used fill.
        self.assertTemplateUsed(resp, "management/_rate_usage.html")
        self.assertContains(resp, "progress-primary", count=2)
        # Bar fill values are clamped percentages of the derived caps.
        self.assertContains(resp, 'value="1"')  # hour: 6/600 = 1%
        self.assertContains(resp, 'value="0"')  # day:  6/14400 ~0%
        # Per-window used/limit text reflects the derived caps.
        self.assertContains(resp, "6/600")  # hour
        self.assertContains(resp, "6/14,400")  # day (intcomma)
        # The per-minute bar is not rendered.
        self.assertNotContains(resp, "6/10")

    def test_page_says_no_limit_when_no_request_limit(self):
        """No limit at all -> no progress bar, and the limits read "No limits"."""
        user = User.objects.get(pk=1)
        self.client.force_login(user)
        resp = self.client.get(reverse("token_detail", args=[1]))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '<p class="muted text-sm">No limits</p>', html=True)
        self.assertNotContains(resp, "Requests per minute")
        self.assertNotContains(resp, "None")
        # No progress bar is rendered at all (every window has no request cap).
        self.assertNotContains(resp, "progress-primary")
        self.assertNotContains(resp, "progress-neutral")
        self.assertNotContains(resp, "<progress")
