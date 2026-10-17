from django.test import SimpleTestCase

from management.key_activity import compact


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
