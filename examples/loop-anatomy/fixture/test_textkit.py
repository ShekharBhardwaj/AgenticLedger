import unittest

from textkit import parse_duration, slugify, truncate, word_count


class SlugifyTests(unittest.TestCase):
    def test_lowercases_and_joins(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_trims_punctuation_at_the_edges(self):
        self.assertEqual(slugify("  Hello, World!  "), "hello-world")

    def test_keeps_digits(self):
        self.assertEqual(slugify("Release 2 of 3"), "release-2-of-3")


class WordCountTests(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(word_count("one two three"), 3)

    def test_any_whitespace(self):
        self.assertEqual(word_count("one   two\nthree\t four"), 4)

    def test_empty(self):
        self.assertEqual(word_count(""), 0)


class TruncateTests(unittest.TestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(truncate("short", 10), "short")

    def test_result_fits_the_limit(self):
        out = truncate("a fairly long sentence", 10)
        self.assertEqual(len(out), 10)
        self.assertTrue(out.endswith("..."))
        self.assertEqual(out, "a fairl...")


class ParseDurationTests(unittest.TestCase):
    def test_hours_only(self):
        self.assertEqual(parse_duration("2h"), 7200)

    def test_all_parts(self):
        self.assertEqual(parse_duration("1h30m15s"), 5415)

    def test_minutes_only(self):
        self.assertEqual(parse_duration("45m"), 2700)


if __name__ == "__main__":
    unittest.main()
