from datetime import UTC, date, datetime
from importlib import import_module

from django.conf import settings
from django.test import SimpleTestCase
from django.utils import translation
from django.utils.formats import get_format

from management.formatting import compact, duration, grouped, percent
from management.templatetags.ui import when

NBSP = "\xa0"


class CompactNumbersTest(SimpleTestCase):
    def test_shortens_with_one_decimal_and_carries_to_the_next_unit(self):
        cases = {
            0: "0",
            999: "999",
            1_000: "1K",
            1_049: "1K",
            1_050: "1.1K",
            131_072: "131.1K",
            999_949: "999.9K",
            999_950: "1M",
            29_331_900_000: "29.3B",
            1_500_000_000: "1.5B",
            999_950_000_000: "1T",
            2_300_000_000_000: "2.3T",
            4_000_000_000_000_000: "4000T",
            -1_500: "-1.5K",
        }
        for count, expected in cases.items():
            with self.subTest(count=count):
                self.assertEqual(compact(count), expected)

    def test_german_uses_a_decimal_comma_and_spelled_out_units(self):
        cases = {
            999: "999",
            1_500: f"1,5{NBSP}Tsd.",
            2_300_000: f"2,3{NBSP}Mio.",
            29_331_900_000: f"29,3{NBSP}Mrd.",
            4_000_000_000_000: f"4{NBSP}Bio.",
            -1_500: f"-1,5{NBSP}Tsd.",
        }
        with translation.override("de"):
            for count, expected in cases.items():
                with self.subTest(count=count):
                    self.assertEqual(compact(count), expected)


class NumberFormatsTest(SimpleTestCase):
    def test_english_and_german_write_numbers_their_own_way(self):
        cases = {
            "grouped": (lambda: grouped(1_440), "1,440", "1.440"),
            "percent": (lambda: percent(12, 100), "12%", f"12{NBSP}%"),
            "no percent of nothing": (lambda: percent(1, 0), "—", "—"),
            "seconds": (lambda: duration(5_640), "5.6 s", "5,6 s"),
            "milliseconds": (lambda: duration(120), "120 ms", "120 ms"),
        }
        for name, (show, english, german) in cases.items():
            with self.subTest(name):
                with translation.override("en"):
                    self.assertEqual(show(), english)
                with translation.override("de"):
                    self.assertEqual(show(), german)


class DateFormatsTest(SimpleTestCase):
    def test_when_follows_the_language(self):
        moment = datetime(2026, 10, 17, 16, 45, tzinfo=UTC)
        cases = {
            "date": (date(2026, 10, 17), "17 Oct 2026", "17. Okt. 2026"),
            "datetime": (moment, "17 Oct 2026 16:45", "17. Okt. 2026 16:45"),
            "recent": (moment, "17 Oct 16:45", "17. Okt. 16:45"),
        }
        for style, (value, english, german) in cases.items():
            with self.subTest(style):
                with translation.override("en"):
                    self.assertIn(f">{english}</time>", when(value, style))
                with translation.override("de"):
                    self.assertIn(f">{german}</time>", when(value, style))


class FormatModulesTest(SimpleTestCase):
    """For a name no format module defines, `get_format` returns the name, and a page then
    formats the date with the name's letters as format characters."""

    def test_every_language_defines_every_format_name(self):
        modules = {
            code: import_module(f"aqueduct.formats.{code}.formats")
            for code, _ in settings.LANGUAGES
        }
        names = {name for module in modules.values() for name in vars(module) if name.isupper()}
        for code in modules:
            for name in sorted(names):
                with self.subTest(language=code, name=name):
                    self.assertNotEqual(get_format(name, lang=code), name)
