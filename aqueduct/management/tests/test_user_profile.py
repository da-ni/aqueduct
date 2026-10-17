from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
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
                self.assertFalse(self.profile.is_admin())
                self.assertFalse(self.profile.is_org_admin(self.org))

    def test_setting_the_group_is_seen_by_the_same_profile(self):
        self.assertFalse(self.profile.is_admin())
        self.profile.group = "admin"
        self.assertTrue(self.profile.is_admin())
        self.profile.group = "org-admin"
        self.assertFalse(self.profile.is_admin())
        self.assertTrue(self.profile.is_org_admin(self.org))

    def test_refresh_from_db_sees_groups_changed_elsewhere(self):
        self.assertEqual(self.profile.group, "user")
        other = UserProfile.objects.get(pk=self.profile.pk)
        other.user.groups.set([Group.objects.get(name="admin")])
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_admin())
