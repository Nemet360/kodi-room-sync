# SPDX-License-Identifier: GPL-2.0-or-later
"""Hebrew/English auto-detection: `detect_language`, `resolve_language`, and
the providers that build a URL from whichever language came out of them.

The failure mode this guards against is not "detection is wrong" in the
abstract — it is "a fixed install with `autocomplete_lang=en` quietly starts
behaving differently", or "the Google/TMDb URL gets `hl=auto`/`language=auto`
sent to the real API because the sentinel leaked past the point meant to
resolve it".
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib", "vendor"))
sys.path.insert(0, os.path.dirname(__file__))

import kodistubs  # noqa: E402

kodistubs.install()

import AutoCompletion  # noqa: E402


class TestDetectLanguage(unittest.TestCase):
    def test_the_examples_from_the_spec(self):
        cases = {
            "brea": "en",
            "שובר": "he",
            "breaking": "en",
            "breaking bad": "en",
            "שובר שורות": "he",
            "game of thrones": "en",
            "משחקי הכס": "he",
            "matrix 2": "en",
            "מטריקס 2": "he",
        }
        for query, expected in cases.items():
            self.assertEqual(AutoCompletion.detect_language(query), expected, query)

    def test_mixed_text_is_hebrew_if_any_hebrew_character_is_present(self):
        """A single Hebrew character anywhere in the string is enough — the
        detector reads the whole query, not just its first word."""
        self.assertEqual(AutoCompletion.detect_language("שובר bad"), "he")
        self.assertEqual(AutoCompletion.detect_language("bad שובר"), "he")

    def test_numbers_and_punctuation_never_flip_the_result(self):
        self.assertEqual(AutoCompletion.detect_language("matrix 2!?"), "en")
        self.assertEqual(AutoCompletion.detect_language("מטריקס 2!?"), "he")
        self.assertEqual(AutoCompletion.detect_language("123 456"), "en")

    def test_empty_or_none_is_english_rather_than_an_exception(self):
        """A query comes from a live keyboard; an empty string arrives, and
        must not be the thing that turns a normal search into a traceback."""
        self.assertEqual(AutoCompletion.detect_language(""), "en")
        self.assertEqual(AutoCompletion.detect_language(None), "en")


class TestResolveLanguage(unittest.TestCase):
    def setUp(self):
        kodistubs.settings["autocomplete_lang"] = "en"

    def test_auto_detects_from_the_query(self):
        kodistubs.settings["autocomplete_lang"] = "auto"
        self.assertEqual(AutoCompletion.resolve_language("שובר"), "he")
        self.assertEqual(AutoCompletion.resolve_language("breaking"), "en")

    def test_auto_is_matched_case_and_space_insensitively(self):
        """A value typed by hand into an advanced setting, or carried over
        from an older skin, should not silently stop auto-detecting."""
        for value in ("Auto", "AUTO", " auto ", "auto"):
            kodistubs.settings["autocomplete_lang"] = value
            self.assertEqual(AutoCompletion.resolve_language("שובר"), "he")

    def test_a_fixed_language_is_returned_unchanged_no_matter_what_was_typed(self):
        """This is the backward-compatibility guarantee: an existing install
        with a real language code set must behave byte-for-byte as before -
        Hebrew text typed into an `en`-fixed install still resolves to `en`."""
        for fixed in ("en", "fr", "he", "ja"):
            kodistubs.settings["autocomplete_lang"] = fixed
            self.assertEqual(AutoCompletion.resolve_language("שובר"), fixed)
            self.assertEqual(AutoCompletion.resolve_language("breaking"), fixed)

    def test_an_empty_setting_does_not_crash_and_is_not_treated_as_auto(self):
        kodistubs.settings["autocomplete_lang"] = ""
        self.assertEqual(AutoCompletion.resolve_language("שובר"), "")


class TestProvidersUseTheResolvedLanguage(unittest.TestCase):
    """The sentinel "auto" must never reach a real request URL."""

    def test_google_hl_parameter(self):
        url = AutoCompletion.GoogleProvider(language="he").build_url("x")
        self.assertIn("hl=he", url)
        self.assertNotIn("auto", url)

    def test_tmdb_language_parameter(self):
        url = AutoCompletion.TmdbProvider(language="he").build_url("x")
        self.assertIn("language=he", url)
        self.assertNotIn("auto", url)

    def test_bing_is_untouched_because_it_never_used_the_language_setting(self):
        """Bing's build_url never referenced self.language before this change
        and must not start now - the spec says explicitly not to touch a
        provider that doesn't already use the setting."""
        url = AutoCompletion.BingProvider(language="he").build_url("x")
        self.assertNotIn("he", url)
        self.assertNotIn("language", url)

    def test_local_dict_provider_keeps_its_own_separate_setting(self):
        """LocalDictProvider has always read `autocomplete_lang_local`, a
        different setting, and must keep doing so even though it now also
        receives a `language` kwarg it does not ask for."""
        kodistubs.settings["autocomplete_lang_local"] = "fr"
        provider = AutoCompletion.LocalDictProvider(language="he")
        self.assertEqual(provider.language, "fr")

    def test_a_provider_built_with_no_language_kwarg_falls_back_to_the_setting(self):
        """Nothing in this codebase constructs a provider this way anymore,
        but the class is still a public contract on its own."""
        kodistubs.settings["autocomplete_lang"] = "de"
        self.assertEqual(AutoCompletion.GoogleProvider().language, "de")


if __name__ == "__main__":
    unittest.main()
