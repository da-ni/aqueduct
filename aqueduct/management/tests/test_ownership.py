from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.sql.datastructures import Join
from django.test import TestCase
from django.utils import timezone

from management import usage_rollup as rollup
from management.models import Org, ServiceAccount, Team, Token, UsageRollupState, UserProfile
from management.ownership import RELATED, attribution, key_org, of_orgs, org_id
from management.tests.helpers import make_token, make_user

User = get_user_model()


class Attribution(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.north = Org.objects.create(name="north")
        cls.south = Org.objects.create(name="south")
        cls.north_team = Team.objects.create(name="north-a", org=cls.north)
        cls.south_team = Team.objects.create(name="south-a", org=cls.south)
        cls.member = make_user("member", cls.north)
        cls.southerner = make_user("southerner", cls.south)
        cls.moved = make_user("moved", cls.north)
        cls.orphan = User.objects.create_user(username="orphan")
        cls.member_key = make_token(cls.member, "member-laptop")
        cls.southern_key = make_token(cls.southerner, "southern-laptop")
        cls.orphan_key = make_token(cls.orphan, "orphan-laptop")
        cls.north_bot = ServiceAccount.objects.create(name="north-bot", team=cls.north_team)
        cls.north_key = make_token(cls.member, "north-bot", service_account=cls.north_bot)
        cls.south_bot = ServiceAccount.objects.create(name="south-bot", team=cls.south_team)
        cls.south_key = make_token(cls.moved, "south-bot", service_account=cls.south_bot)

    def org_of(self, token: Token) -> int | None:
        return Token.objects.annotate(o=key_org()).values_list("o", flat=True).get(pk=token.pk)


class AttributionAgreementTest(Attribution):
    def test_the_coalesce_the_filter_and_the_python_form_name_the_same_org(self):
        tokens = list(Token.objects.select_related(*RELATED))
        expected = {
            self.member_key.pk: self.north.pk,
            self.southern_key.pk: self.south.pk,
            self.orphan_key.pk: None,
            self.north_key.pk: self.north.pk,
            self.south_key.pk: self.south.pk,
        }
        annotated = dict(Token.objects.annotate(o=key_org()).values_list("id", "o"))
        self.assertEqual(annotated, expected)
        with self.assertNumQueries(0):
            self.assertEqual({token.pk: org_id(token) for token in tokens}, expected)
        for ids in ({self.north.pk}, {self.south.pk}, {self.north.pk, self.south.pk}):
            with self.subTest(orgs=ids):
                in_sql = set(Token.objects.filter(of_orgs(ids)).values_list("id", flat=True))
                self.assertEqual(in_sql, {pk for pk, org in expected.items() if org in ids})


class AttributionColumnsTest(TestCase):
    def test_the_watched_columns_cover_every_join_of_the_coalesce(self):
        query = Token.objects.annotate(o=key_org()).query
        str(query)
        by_table = {model._meta.db_table: model for model in apps.get_models()}
        joined = set()
        for join in query.alias_map.values():
            if not isinstance(join, Join):
                continue
            parent = by_table[query.alias_map[join.parent_alias].table_name]
            child = by_table[join.table_name]
            for parent_column, child_column in join.join_cols:
                if child_column == child._meta.pk.column:
                    joined.add((parent, parent_column))
                else:
                    joined.add((child, child_column))
        watched = {
            (model, column) for model, columns in attribution().items() for column in columns
        }
        self.assertTrue(joined)
        self.assertLessEqual(joined, watched)

    def test_the_watched_columns_are_the_old_list_plus_the_profiles_user(self):
        self.assertEqual(
            attribution(),
            {
                Token: {"user_id", "service_account_id"},
                ServiceAccount: {"team_id"},
                Team: {"org_id"},
                UserProfile: {"org_id", "user_id"},
            },
        )


class AttributionMoveTest(Attribution):
    def dirty(self) -> bool:
        return UsageRollupState.objects.get(name=rollup.HOURLY).dirty

    def test_every_move_that_changes_a_keys_org_dirties_the_rollups(self):
        def move_team():
            self.north_team.org = self.south
            self.north_team.save()

        def move_service_account():
            self.north_bot.team = self.south_team
            self.north_bot.save()

        def move_profile():
            profile = self.member.profile
            profile.org = self.south
            profile.save()

        def give_key_away():
            self.member_key.user = self.southerner
            self.member_key.save()

        def delete_profile():
            self.member.profile.delete()

        def create_profile():
            UserProfile.objects.create(user=self.orphan, org=self.north)

        for move, key, now_in in (
            (move_team, self.north_key, self.south),
            (move_service_account, self.north_key, self.south),
            (move_profile, self.member_key, self.south),
            (give_key_away, self.member_key, self.south),
            (delete_profile, self.member_key, None),
            (create_profile, self.orphan_key, self.north),
        ):
            with self.subTest(move=move.__name__), transaction.atomic():
                rollup.refresh(timezone.now())
                self.assertFalse(self.dirty())
                self.assertNotEqual(self.org_of(key), now_in and now_in.pk)
                with self.captureOnCommitCallbacks(execute=True):
                    move()
                self.assertEqual(self.org_of(key), now_in and now_in.pk)
                self.assertTrue(self.dirty())
                transaction.set_rollback(True)

    def test_renaming_a_team_leaves_the_rollups_clean(self):
        rollup.refresh(timezone.now())
        self.north_team.name = "renamed"
        with self.captureOnCommitCallbacks(execute=True):
            self.north_team.save()
        self.assertFalse(self.dirty())
