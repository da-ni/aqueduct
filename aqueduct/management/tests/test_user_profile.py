from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase

from management.models import Org, UserProfile


class RoleTest(TestCase):
    def setUp(self):
        self.org = Org.objects.create(name="org")
        user = get_user_model().objects.create_user(username="u", email="u@example.com")
        user.groups.add(Group.objects.get(name="user"))
        self.profile = UserProfile.objects.create(user=user, org=self.org)

    def test_role_checks_read_the_groups_once(self):
        with self.assertNumQueries(1):
            for _ in range(3):
                self.assertEqual(self.profile.rank, "user")
                self.assertEqual(self.profile.group, "user")

    def test_setting_the_group_is_seen_by_the_same_profile(self):
        self.assertEqual(self.profile.rank, "user")
        self.profile.group = "admin"
        self.assertEqual(self.profile.rank, "admin")
        self.profile.group = "org-admin"
        self.assertEqual(self.profile.rank, "org-admin")

    def test_refresh_from_db_sees_groups_changed_elsewhere(self):
        self.assertEqual(self.profile.group, "user")
        other = UserProfile.objects.get(pk=self.profile.pk)
        other.user.groups.set([Group.objects.get(name="admin")])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.rank, "admin")

    def test_prefetched_groups_answer_the_role_without_a_query(self):
        self.profile.user.groups.set([Group.objects.get(name="org-admin")])
        profile = UserProfile.objects.select_related("user", "org").prefetch_related("user__groups")
        found = profile.get(pk=self.profile.pk)
        with self.assertNumQueries(0):
            self.assertEqual(found.group, "org-admin")
            self.assertEqual(found.rank, "org-admin")

    def test_the_highest_group_decides_and_no_group_ranks_as_a_user(self):
        user = self.profile.user
        user.groups.set(Group.objects.filter(name__in=["user", "org-admin", "admin"]))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.group, "admin")
        user.groups.clear()
        self.profile.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.profile.group  # noqa: B018
        self.assertEqual(self.profile.rank, "user")
