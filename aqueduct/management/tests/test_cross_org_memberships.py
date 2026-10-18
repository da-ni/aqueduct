from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, override_settings
from django.urls import reverse

from management.auth import OIDCBackend
from management.models import Org, Team, TeamMembership, UserProfile
from management.tests.helpers import join, make_user, seed_active_config
from management.tests.test_oauth_team_creation import BASE_SETTINGS

User = get_user_model()

BEFORE = [("management", "0021_file_batch_models")]

ORG_THEN_TEAMS = """\
class MyConfig(ConfigSnippet):
    def org_name(self, claims):
        return claims["groups"][0]

    def team_names(self, claims):
        return claims["groups"][1:]
"""


class DeleteCrossOrgMembershipsMigrationTest(TestCase):
    def test_deletes_exactly_the_memberships_in_teams_of_another_org(self):
        loader = MigrationLoader(connection)
        (delete,) = loader.get_migration("management", "0022_delete_cross_org_memberships").operations
        old = loader.project_state(BEFORE).apps
        orgs = old.get_model("management", "Org")
        teams = old.get_model("management", "Team")
        profiles = old.get_model("management", "UserProfile")
        memberships = old.get_model("management", "TeamMembership")
        users = old.get_model("auth", "User")
        north, south = orgs.objects.create(name="north"), orgs.objects.create(name="south")
        north_team = teams.objects.create(name="a", org=north)
        south_team = teams.objects.create(name="a", org=south)
        moved = profiles.objects.create(user=users.objects.create(username="moved"), org=south)
        stayed = profiles.objects.create(user=users.objects.create(username="stayed"), org=north)
        kept = {
            memberships.objects.create(user_profile=moved, team=south_team).pk,
            memberships.objects.create(user_profile=stayed, team=north_team, is_admin=True).pk,
        }
        memberships.objects.create(user_profile=moved, team=north_team, is_admin=True)
        memberships.objects.create(user_profile=stayed, team=south_team)

        delete.code(old, connection.schema_editor())

        self.assertEqual(set(TeamMembership.objects.values_list("pk", flat=True)), kept)


class OrgMoveTest(TestCase):
    def setUp(self):
        self.north = Org.objects.create(name="north")
        self.south = Org.objects.create(name="south")
        self.north_team = Team.objects.create(name="E123", org=self.north)
        self.user = make_user("ana", self.north)
        join(self.user, self.north_team, is_admin=True)

    def teams_of(self, user) -> set[Team]:
        return set(Team.objects.filter(member_profiles__user=user))

    def test_an_org_move_ends_the_memberships_of_the_old_org(self):
        south_team = Team.objects.create(name="Robotics", org=self.south)
        profile = self.user.profile
        profile.org = self.south
        profile.save()
        join(self.user, south_team)
        profile.save()
        self.assertEqual(self.teams_of(self.user), {south_team})

    @override_settings(**BASE_SETTINGS)
    def test_a_sign_in_from_another_org_moves_the_user_with_their_oauth_teams(self):
        seed_active_config(ORG_THEN_TEAMS)
        self.north_team.oauth_group_name = "E123"
        self.north_team.save()
        south_team = Team.objects.create(name="E123", org=self.south, oauth_group_name="E123")

        OIDCBackend().update_user(self.user, {"groups": ["south", "E123"]})

        self.assertEqual(UserProfile.objects.get(user=self.user).org, self.south)
        self.assertEqual(self.teams_of(self.user), {south_team})


class TeamAdminTest(TestCase):
    def setUp(self):
        self.north = Org.objects.create(name="north")
        self.south = Org.objects.create(name="south")
        self.team = Team.objects.create(name="NLP", org=self.north)
        self.member = make_user("member", self.north)
        self.foreigner = make_user("foreigner", self.south)
        join(self.member, self.team)
        admin = make_user("admin", self.north)
        admin.is_staff = admin.is_superuser = True
        admin.save()
        self.client.force_login(admin)
        self.url = reverse("admin:management_team_change", args=[self.team.pk])

    def post(self, **changes):
        """Submit the change form as rendered, with `changes` applied."""
        page = self.client.get(self.url).context
        forms = [page["adminform"].form]
        for formset in page["inline_admin_formsets"]:
            forms += [formset.formset.management_form, *formset.formset.forms]
        data = {}
        for form in forms:
            for name, field in form.fields.items():
                value = form[name].value()
                if value is None or value is False:
                    continue
                data[form.add_prefix(name)] = (
                    [str(v) for v in value] if isinstance(value, list) else str(value)
                )
        data.update(changes)
        return self.client.post(self.url, data)

    def test_refuses_a_member_from_another_org(self):
        response = self.post(**{"teammembership_set-1-user_profile": self.foreigner.profile.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "foreigner@example.com is in another organization.")
        self.assertEqual(set(self.team.member_profiles.all()), {self.member.profile})

    def test_refuses_to_move_a_team_with_members_to_another_org(self):
        response = self.post(org=self.south.pk)
        self.assertContains(response, "member@example.com is in another organization.")
        self.team.refresh_from_db()
        self.assertEqual(self.team.org, self.north)

    def test_moves_a_team_once_its_members_are_removed(self):
        response = self.post(org=self.south.pk, **{"teammembership_set-0-DELETE": "on"})
        self.assertEqual(response.status_code, 302)
        self.team.refresh_from_db()
        self.assertEqual(self.team.org, self.south)
        self.assertFalse(TeamMembership.objects.filter(team=self.team).exists())
