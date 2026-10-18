"""Checks that keep the German catalog and the templates ready for translation.

The catalog checks need GNU gettext (`xgettext`, `msgmerge`, `msgfmt`) on PATH and skip without
it. After changing a translatable string, run from `aqueduct/`:

    uv run python manage.py makemessages -l de --no-obsolete --no-location
    uv run python manage.py compilemessages -l de

then translate every new entry in `locale/de/LC_MESSAGES/django.po` and commit both files.
"""

import ast
import re
import shutil
import subprocess
import sys
from gettext import GNUTranslations
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

BASE_DIR = Path(settings.BASE_DIR)
PO = Path("locale/de/LC_MESSAGES/django.po")
MAKEMESSAGES = ("manage.py", "makemessages", "-l", "de", "--no-obsolete", "--no-location")
MO = PO.with_suffix(".mo")
GETTEXT = all(shutil.which(tool) for tool in ("xgettext", "msgmerge", "msgfmt"))
NO_GETTEXT = "GNU gettext (xgettext, msgmerge, msgfmt) is not on PATH, so the catalog is unchecked"

# msgids that may stay untranslated in the German catalog for now. A pull request that extracts
# strings but leaves their translation to a later one lists them here; the later one removes
# them. The test fails on an entry that is translated or gone, so the list only shrinks.
UNTRANSLATED: frozenset[str] = frozenset()

# Templates that still hold text outside {% translate %} and {% blocktranslate %}. An extraction
# pull request wraps a template's text and removes the template here; the test fails on a listed
# template that has no bare text left, so the list only shrinks.
UNTRANSLATED_TEMPLATES = frozenset(
    {
        "admin/management/snippet/change_list.html",
        "admin/management/snippet/test_console.html",
        "management/_model_detail.html",
        "management/batches.html",
        "management/files.html",
        "management/mcp_servers.html",
        "management/model_list.html",
        "management/vector_store_detail.html",
        "management/vector_stores.html",
    }
)

TEMPLATES = BASE_DIR / "management" / "templates"
TRANSLATED_ATTRIBUTES = frozenset(
    {"alt", "aria-label", "label", "placeholder", "title", "data-tip"}
)
HAS_WORD = re.compile(r"[^\W\d_]")
NOT_TEXT = re.compile(
    r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}"
    r"|{%\s*blocktrans(?:late)?\b.*?{%\s*endblocktrans(?:late)?\s*%}"
    r"|<script\b.*?</script>|<style\b.*?</style>"
    r"|{#.*?#}|{%.*?%}|{{.*?}}",
    re.DOTALL,
)


class _BareText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[str] = []

    def handle_data(self, data: str) -> None:
        if HAS_WORD.search(data):
            self.found.append(" ".join(data.split()))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name in TRANSLATED_ATTRIBUTES and value and HAS_WORD.search(value):
                self.found.append(f"{name}={' '.join(value.split())}")


def bare_text(source: str) -> list[str]:
    """Text a reader sees that no translate tag wraps, with each template tag cut out."""
    parser = _BareText()
    parser.feed(NOT_TEXT.sub(" ", source))
    parser.close()
    return parser.found


def management_templates() -> dict[str, str]:
    return {
        path.relative_to(TEMPLATES).as_posix(): path.read_text()
        for path in sorted(TEMPLATES.rglob("*.html"))
    }


class TemplateTextTest(SimpleTestCase):
    def test_templates_off_the_list_translate_all_their_text(self):
        bare = {
            name: found
            for name, source in management_templates().items()
            if name not in UNTRANSLATED_TEMPLATES and (found := bare_text(source))
        }
        self.assertEqual(bare, {}, "Wrap this text in {% translate %} or {% blocktranslate %}")

    def test_list_names_only_templates_with_bare_text(self):
        templates = management_templates()
        done = {
            name
            for name in UNTRANSLATED_TEMPLATES
            if name not in templates or not bare_text(templates[name])
        }
        self.assertEqual(done, set(), "Remove these from UNTRANSLATED_TEMPLATES")

    def test_finds_text_and_attributes_but_not_tags(self):
        source = (
            '{% load i18n %}<p title="Copy key">{% translate "Saved" %} {{ count }} keys</p>'
            '{% blocktranslate %}Hi {{ name }}{% endblocktranslate %}<i aria-label="{{ x }}"></i>'
        )
        self.assertEqual(bare_text(source), ["title=Copy key", "keys"])


class StyleNumbersTest(SimpleTestCase):
    """German formats 12.5 as 12,5, which is no CSS number."""

    def test_numbers_in_style_attributes_are_unlocalized(self):
        localized = {
            f"{path.relative_to(BASE_DIR)}: {variable}"
            for path in BASE_DIR.glob("*/templates/**/*.html")
            for style in re.findall(r'style="[^"]*"', path.read_text())
            for variable in re.findall(r"{{.*?}}", style)
            if "|unlocalize" not in variable
        }
        self.assertEqual(localized, set(), "Add |unlocalize from {% load l10n %}")


def po_entries(po: str) -> list[tuple[str, list[str], bool]]:
    """(msgid, msgstrs, fuzzy) of every entry but the header."""
    entries = []
    for block in po.split("\n\n"):
        fields: dict[str, str] = {}
        key = ""
        for line in block.strip().splitlines():
            if line.startswith('"'):
                fields[key] += ast.literal_eval(line)
            elif not line.startswith("#"):
                key, _, value = line.partition(" ")
                fields[key] = ast.literal_eval(value)
        if fields.get("msgid"):
            msgstrs = [value for name, value in fields.items() if name.startswith("msgstr")]
            fuzzy = any(line.startswith("#,") and "fuzzy" in line for line in block.splitlines())
            entries.append((fields["msgid"], msgstrs, fuzzy))
    return entries


def without_creation_date(po: str) -> str:
    return re.sub(r'"POT-Creation-Date: .*\\n"\n', "", po)


def catalog(mo: Path) -> dict:
    with mo.open("rb") as file:
        return GNUTranslations(file)._catalog  # type: ignore[attr-defined]


class GermanCatalogTest(SimpleTestCase):
    maxDiff = None

    def setUp(self):
        self.po = (BASE_DIR / PO).read_text()

    def test_every_entry_is_translated_and_none_is_fuzzy(self):
        entries = po_entries(self.po)
        fuzzy = {msgid for msgid, _, is_fuzzy in entries if is_fuzzy}
        untranslated = {msgid for msgid, msgstrs, _ in entries if not all(msgstrs)}
        self.assertEqual(fuzzy, set(), "Check these guesses by msgmerge and drop `#, fuzzy`")
        self.assertEqual(untranslated - UNTRANSLATED, set(), "Translate these entries")
        self.assertEqual(UNTRANSLATED - untranslated, set(), "Remove these from UNTRANSLATED")

    @skipUnless(GETTEXT, NO_GETTEXT)
    def test_catalog_holds_exactly_the_strings_of_the_source(self):
        with TemporaryDirectory() as tmp:
            copy = Path(tmp) / "aqueduct"
            shutil.copytree(
                BASE_DIR,
                copy,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "staticfiles", ".mypy_cache", "*.sqlite3", "media"
                ),
            )
            subprocess.run(
                [
                    sys.executable,
                    "manage.py",
                    "makemessages",
                    "-l",
                    "de",
                    "--no-obsolete",
                    "--no-location",
                ],
                cwd=copy,
                check=True,
                capture_output=True,
            )
            fresh = (copy / PO).read_text()
        self.assertEqual(
            without_creation_date(self.po),
            without_creation_date(fresh),
            "The catalog is stale. Run makemessages as the docstring of this module shows.",
        )

    @skipUnless(GETTEXT, NO_GETTEXT)
    def test_compiled_catalog_matches_the_catalog(self):
        with TemporaryDirectory() as tmp:
            mo = Path(tmp) / "django.mo"
            subprocess.run(
                ["msgfmt", "--check-format", "-o", mo, BASE_DIR / PO],
                check=True,
                capture_output=True,
            )
            self.assertEqual(
                catalog(BASE_DIR / MO),
                catalog(mo),
                "The .mo is stale. Run compilemessages as the docstring of this module shows.",
            )
