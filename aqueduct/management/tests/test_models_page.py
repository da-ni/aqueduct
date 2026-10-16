import json
import re
import shlex
import tempfile
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlsplit

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import resolve, reverse

from gateway.config import get_router_config
from management.model_catalog import (
    MODES,
    OTHER_MODE,
    Catalog,
    Category,
    Scope,
    mode_for,
    parse_card,
)
from management.models import Org, Team, Token, UserProfile

User = get_user_model()

ROUTER_CONFIG_PATH = Path(settings.BASE_DIR).parent / "example_router_config.yaml"
EXAMPLE_ENTRIES = yaml.safe_load(ROUTER_CONFIG_PATH.read_text())["model_list"]

RICH_ENTRY = {
    "model_name": "rich",
    "litellm_params": {"model": "hosted_vllm/meta-llama/Llama-3.3-70B"},
    "model_info": {
        "aliases": ["main"],
        "max_tokens": 131072,
        "max_output_tokens": 16384,
        "output_vector_size": 1024,
        "supported_modalities": ["text", "image"],
        "supported_output_modalities": ["text"],
        "supports_vision": True,
        "supports_function_calling": True,
        "supports_response_schema": True,
        "supports_reasoning": True,
        "supports_web_search": True,
        "supports_pdf_input": True,
        "supports_audio_input": True,
        "supports_audio_output": True,
        "supports_prompt_caching": True,
        "supports_native_streaming": True,
        "request_limit_multiplier": 0.5,
        "deprecation_date": "2026-11-30",
    },
}

GATEWAY = "http://testserver/v1"


def entry(name="m", **model_info):
    return {
        "model_name": name,
        "litellm_params": {"model": f"openai/{name}"},
        "model_info": model_info,
    }


def personal(*excluded):
    return Scope(None, frozenset(excluded))


def team(name, *excluded):
    return Scope(name, frozenset(excluded))


def curl_args(snippet: str) -> list[str]:
    return shlex.split(snippet.replace("\\\n", " "))


class ModelCardTest(SimpleTestCase):
    def test_example_config_shows_no_metadata_it_does_not_have(self):
        for raw in EXAMPLE_ENTRIES:
            with self.subTest(model=raw["model_name"]):
                card = parse_card(raw)
                self.assertEqual(card.specs, ())
                self.assertIsNone(card.context)
                self.assertIsNone(card.deprecation_date)
                self.assertEqual(card.served_model, f"openai/{raw['model_name']}")
                expected = ("2x request limit",) if card.name == "text-embedding-ada-002" else ()
                self.assertEqual(card.capabilities, expected)

    def test_rich_entry_shows_every_cell(self):
        card = parse_card(RICH_ENTRY)
        self.assertEqual(
            [(spec.label, spec.value) for spec in card.specs],
            [
                ("Context window", "131K"),
                ("Max output tokens", "16K"),
                ("Dimensions", "1,024"),
                ("Input", "Text, image"),
                ("Output", "Text"),
            ],
        )
        self.assertEqual(card.context, "131K")
        self.assertEqual(
            card.capabilities,
            (
                "Vision",
                "Tools",
                "JSON schema",
                "Reasoning",
                "Web search",
                "PDF input",
                "Audio input",
                "Audio output",
                "Prompt caching",
                "Streaming",
                "0.5x request limit",
            ),
        )
        self.assertEqual(card.served_model, "hosted_vllm/meta-llama/Llama-3.3-70B")
        self.assertEqual(str(card.deprecation_date), "2026-11-30")

    def test_wrongly_typed_values_drop_out(self):
        card = parse_card(
            entry(
                max_tokens="128k",
                max_output_tokens=True,
                output_vector_size=-1,
                supported_modalities="text",
                supports_vision="yes",
                request_limit_multiplier="2",
                deprecation_date="soon",
            )
        )
        self.assertEqual((card.specs, card.capabilities, card.context), ((), (), None))
        self.assertIsNone(card.deprecation_date)

    def test_explicit_context_wins_over_max_tokens(self):
        card = parse_card(entry(max_input_tokens=1_047_576, max_tokens=8192))
        self.assertEqual(card.context, "1M")

    def test_embedding_context_is_labelled_max_input_tokens(self):
        card = parse_card(entry(mode="embedding", max_tokens=8191))
        self.assertEqual(card.specs[0].label, "Max input tokens")
        self.assertEqual(card.specs[0].value, "8K")


class ModeTest(SimpleTestCase):
    def test_every_spelling_maps_to_its_mode(self):
        for mode in MODES:
            for spelling in mode.spellings:
                with self.subTest(spelling=spelling):
                    self.assertIs(mode_for(spelling), mode)
                    self.assertIs(mode_for(f" {spelling.upper()} "), mode)

    def test_singular_and_plural_agree(self):
        for singular in ("embedding", "audio_transcription", "image_generation"):
            with self.subTest(singular=singular):
                self.assertIs(mode_for(singular), mode_for(f"{singular}s"))
                self.assertIsNot(mode_for(singular), OTHER_MODE)

    def test_missing_mode_is_chat(self):
        for raw in (None, ""):
            self.assertEqual(mode_for(raw).label, "Chat")

    def test_unknown_mode_is_other_with_no_snippet(self):
        card = parse_card(entry(mode="rerank"))
        self.assertEqual(card.mode.category, Category.OTHER)
        self.assertIsNone(card.curl(GATEWAY))

    def test_snippet_targets_the_gateway_route_for_its_mode(self):
        for mode in MODES:
            with self.subTest(mode=mode.label):
                card = parse_card(entry("model-x", mode=mode.spellings[0]))
                url = curl_args(card.curl(GATEWAY))[1]
                self.assertTrue(url.startswith(GATEWAY))
                match = resolve(urlsplit(url).path)
                self.assertEqual(match.namespace, "gateway")
                self.assertEqual(match.url_name, f"v1_{mode.endpoint.removeprefix('gateway:')}")

    def test_snippet_sends_the_model_name(self):
        for mode in MODES:
            with self.subTest(mode=mode.label):
                name = 'it\'s "quoted"'
                args = curl_args(parse_card(entry(name, mode=mode.spellings[0])).curl(GATEWAY))
                if mode.form:
                    self.assertIn(f"model={name}", args)
                else:
                    self.assertEqual(json.loads(args[args.index("-d") + 1])["model"], name)


class AvailabilityTest(SimpleTestCase):
    def test_no_scopes_means_no_badge(self):
        card = parse_card(entry("m"))
        self.assertEqual((card.off_for, card.off_badge), ((), ""))

    def test_badge_names_the_first_switched_off_scope(self):
        cases = [
            ((personal("m"),), "Off for you"),
            ((personal("m"), team("Whale", "m")), "Off for you"),
            ((personal(), team("Whale", "m")), "Off for Whale"),
            ((personal(), team("Orca", "m"), team("Whale", "m")), "Off for Orca +1"),
            ((personal("m"), team("Orca", "m"), team("Whale")), "Off for you +1"),
        ]
        for scopes, badge in cases:
            with self.subTest(badge=badge):
                self.assertEqual(parse_card(entry("m"), scopes).off_badge, badge)

    def test_notice_lists_where_it_is_off_and_on(self):
        card = parse_card(entry("m"), (personal(), team("Orca", "m"), team("Whale", "m")))
        self.assertEqual(card.off_names, "Orca service accounts and Whale service accounts")
        self.assertEqual(card.on_names, "your personal keys")

    def test_an_excluded_alias_switches_nothing_off(self):
        card = parse_card(entry("m", aliases=["a"]), (personal("a"),))
        self.assertEqual(card.off_for, ())


class CatalogTest(SimpleTestCase):
    catalog = Catalog(tuple(parse_card(raw) for raw in EXAMPLE_ENTRIES))

    def test_lists_only_categories_present(self):
        self.assertEqual(
            self.catalog.categories,
            (Category.CHAT, Category.EMBEDDINGS, Category.AUDIO, Category.IMAGES),
        )
        self.assertEqual(Catalog((parse_card(entry()),)).categories, (Category.CHAT,))

    def test_context_column_only_when_some_model_has_one(self):
        self.assertFalse(self.catalog.has_context)
        self.assertTrue(Catalog((parse_card(entry(max_tokens=8192)),)).has_context)

    def test_search_matches_names_and_aliases_within_a_category(self):
        names = [card.name for card in self.catalog.search(None, "STT")]
        self.assertEqual(names, ["whisper-1"])
        images = [card.name for card in self.catalog.search(Category.IMAGES, "")]
        self.assertEqual(images, ["dall-e-2", "gpt-image-1"])
        self.assertEqual(self.catalog.search(Category.CHAT, "image"), ())

    def test_find_by_name_or_exact_alias(self):
        self.assertEqual(self.catalog.find("tts").name, "gpt-4o-mini-tts")
        self.assertEqual(self.catalog.find("whisper-1").name, "whisper-1")
        self.assertIsNone(self.catalog.find("TTS"))

    def test_parse_unknown_category_is_all(self):
        self.assertEqual(Category.parse("images"), Category.IMAGES)
        for raw in ("", "Images", "video"):
            self.assertIsNone(Category.parse(raw))


def rows(response) -> list[str]:
    return re.findall(r'#model-detail"[^>]*>([^<]+)</a>', response.content.decode())


def selected(response) -> str:
    return re.search(r"<h2[^>]*>([^<]+)</h2>", response.content.decode()).group(1)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
    LITELLM_ROUTER_CONFIG_FILE_PATH=ROUTER_CONFIG_PATH,
)
class ModelsPageTest(TestCase):
    fixtures: ClassVar[list[str]] = ["gateway_data.json"]

    def setUp(self):
        self.org = Org.objects.get(name="E060")
        self.whale = Team.objects.get(name="Whale")

    def page(self, user_pk=3, **params):
        self.client.force_login(User.objects.get(pk=user_pk))
        response = self.client.get(reverse("models"), params)
        self.assertEqual(response.status_code, 200)
        return response

    def switch_off(self, holder, *models, merge=True):
        holder.excluded_models = list(models)
        holder.merge_exclusion_lists = merge
        holder.save()

    def test_signed_out_goes_to_sign_in(self):
        response = self.client.get(reverse("models"))
        self.assertRedirects(
            response, f"{reverse('sso')}?next={reverse('models')}", fetch_redirect_response=False
        )

    def test_user_sees_every_model_and_no_switch_off(self):
        response = self.page()
        self.assertEqual(rows(response), [raw["model_name"] for raw in EXAMPLE_ENTRIES])
        self.assertEqual(selected(response), "gpt-4.1-nano")
        self.assertNotContains(response, "Off for")
        self.assertNotContains(response, "Switched off")
        self.assertContains(response, "curl http://testserver/v1/chat/completions")

    def test_team_member_sees_the_team_switch_off(self):
        self.switch_off(self.whale, "whisper-1")
        response = self.page(model="whisper-1")
        self.assertContains(response, "Off for Whale", count=1)
        self.assertContains(response, "Switched off for Whale service accounts.")
        self.assertContains(response, "Still available for your personal keys.")
        self.assertNotContains(response, "in admin</a>")

    def test_non_member_does_not_see_the_team_switch_off(self):
        self.switch_off(self.whale, "whisper-1")
        outsider = User.objects.create_user("outsider", "outsider@example.com")
        UserProfile.objects.create(user=outsider, org=self.org)
        response = self.page(user_pk=outsider.pk, model="whisper-1")
        self.assertNotContains(response, "Off for")
        self.assertNotContains(response, "Switched off")

    def test_org_switch_off_reaches_only_scopes_that_merge(self):
        self.switch_off(self.org, "dall-e-2")
        self.assertContains(self.page(), "Off for you")
        self.switch_off(UserProfile.objects.get(user_id=3), merge=False)
        self.assertContains(self.page(), "Off for Whale")
        self.switch_off(self.whale, merge=False)
        self.assertNotContains(self.page(), "Off for")

    def test_staff_get_an_admin_link_per_switched_off_scope(self):
        self.switch_off(self.whale, "whisper-1")
        self.switch_off(UserProfile.objects.get(user_id=1), "whisper-1")
        response = self.page(user_pk=1, model="whisper-1")
        self.assertContains(
            response, "Switched off for your personal keys and Whale service accounts."
        )
        for url in (
            reverse("admin:management_team_change", args=[self.whale.pk]),
            reverse("admin:auth_user_change", args=[1]),
        ):
            self.assertContains(response, f'href="{url}"')

    def test_type_tab_lists_only_that_type(self):
        response = self.page(type="images")
        self.assertEqual(rows(response), ["dall-e-2", "gpt-image-1"])
        self.assertEqual(selected(response), "dall-e-2")
        self.assertContains(response, 'aria-current="page">Images</a>')

    def test_unknown_type_lists_every_model(self):
        self.assertEqual(len(rows(self.page(type="video"))), len(EXAMPLE_ENTRIES))

    def test_search_matches_an_alias(self):
        response = self.page(q="stt")
        self.assertEqual(rows(response), ["whisper-1"])
        self.assertEqual(selected(response), "whisper-1")

    def test_search_without_a_match_says_so(self):
        response = self.page(q="no-such-model")
        self.assertEqual(rows(response), [])
        self.assertContains(response, "No model matches.")

    def test_model_accepts_an_alias(self):
        self.assertEqual(selected(self.page(model="tts")), "gpt-4o-mini-tts")

    def test_unknown_model_selects_the_first_listed(self):
        self.assertEqual(selected(self.page(type="audio", model="nope")), "gpt-4o-mini-tts")

    def test_deep_link_to_a_filtered_out_model_still_shows_it(self):
        response = self.page(type="images", model="whisper-1")
        self.assertEqual(rows(response), ["dall-e-2", "gpt-image-1"])
        self.assertEqual(selected(response), "whisper-1")
        self.assertContains(response, "curl http://testserver/v1/audio/transcriptions")

    def test_a_literal_api_key_never_reaches_the_page(self):
        raw = {**EXAMPLE_ENTRIES[0], "litellm_params": {"model": "openai/x", "api_key": "sk-leak"}}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as config:
            yaml.safe_dump({"model_list": [raw]}, config)
            self.addCleanup(get_router_config.cache_clear)
            get_router_config.cache_clear()
            with override_settings(LITELLM_ROUTER_CONFIG_FILE_PATH=config.name):
                response = self.page()
        self.assertContains(response, raw["model_name"])
        self.assertNotContains(response, "sk-leak")

    def test_switch_offs_match_what_the_gateway_enforces(self):
        self.switch_off(self.org, "dall-e-2")
        self.switch_off(UserProfile.objects.get(user_id=1), "gpt-4.1-nano", "tts")
        self.switch_off(self.whale, "whisper-1")
        catalog = self.page(user_pk=1).context["catalog"]
        for team, token in ((None, Token.objects.get(pk=1)), ("Whale", Token.objects.get(pk=2))):
            outcomes = set()
            for card in catalog.cards:
                page_off = any(scope.team == team for scope in card.off_for)
                outcomes.add(page_off)
                for key in (card.name, *card.aliases):
                    with self.subTest(token=token.name, key=key):
                        self.assertEqual(page_off, token.model_excluded(key))
            self.assertEqual(outcomes, {True, False})
