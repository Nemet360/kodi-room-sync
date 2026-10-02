"""Labels, in both languages, matched against what the code and the settings
screen actually ask for.

Kodi does not complain about a missing string — it renders the raw number. So a
setting added without its label ships as a screen reading "30018", and a
Hebrew file that fell one string behind shows English in the middle of a Hebrew
menu. Neither produces an error anywhere, which is why this is a test.
"""
import os
import re
import unittest
import xml.etree.ElementTree as ET

ADDON = os.path.join(os.path.dirname(__file__), "..")
LANGUAGES = ("en_gb", "he_il")


def read(*parts):
    with open(os.path.join(ADDON, *parts), "r", encoding="utf-8") as handle:
        return handle.read()


def po_ids(language):
    return set(re.findall(r'msgctxt "#(\d+)"',
                          read("resources", "language",
                               "resource.language." + language, "strings.po")))


def settings_label_ids():
    return set(re.findall(r'label="(\d+)"', read("resources", "settings.xml")))


def code_string_ids():
    used = set(re.findall(r"getLocalizedString\((\d+)\)", read("service.py")))
    used |= set(re.findall(r"\bt\((\d+)\)", read("default.py")))
    return used


class TestSettingsXml(unittest.TestCase):
    def test_it_is_well_formed(self):
        ET.parse(os.path.join(ADDON, "resources", "settings.xml"))

    def test_every_setting_the_code_reads_exists_on_the_screen(self):
        """A setting read but never offered is one nobody can change, and
        `getSetting` on an unknown id returns an empty string that then reads as
        'not configured' forever."""
        declared = set(re.findall(r'setting id="([a-z_]+)"',
                                  read("resources", "settings.xml")))
        service = read("service.py")
        for name in re.findall(r'setting(?:_int|_bool)?\("([a-z_]+)"', service):
            self.assertIn(name, declared, "%s is read but not declared" % name)


class TestTranslations(unittest.TestCase):
    def test_every_label_on_the_screen_exists_in_both_languages(self):
        for language in LANGUAGES:
            missing = sorted(settings_label_ids() - po_ids(language))
            self.assertEqual(missing, [], "%s is missing %s" % (language, missing))

    def test_every_string_the_code_asks_for_exists_in_both_languages(self):
        for language in LANGUAGES:
            missing = sorted(code_string_ids() - po_ids(language))
            self.assertEqual(missing, [], "%s is missing %s" % (language, missing))

    def test_the_two_languages_carry_the_same_ids(self):
        """A Hebrew file one string behind shows English inside a Hebrew menu,
        and Kodi says nothing about it."""
        self.assertEqual(po_ids("en_gb"), po_ids("he_il"))

    def test_the_hebrew_file_is_actually_translated(self):
        """A copied .po with English values is worse than none: it looks done."""
        hebrew = read("resources", "language", "resource.language.he_il",
                      "strings.po")
        translated = re.findall(r'msgstr "([^"]+)"', hebrew)
        with_hebrew = [line for line in translated
                       if any("֐" <= ch <= "ת" for ch in line)]
        self.assertGreater(len(with_hebrew), len(translated) * 0.8)

    def test_no_id_is_declared_twice(self):
        for language in LANGUAGES:
            ids = re.findall(r'msgctxt "#(\d+)"',
                             read("resources", "language",
                                  "resource.language." + language, "strings.po"))
            self.assertEqual(len(ids), len(set(ids)))


class TestAddonXml(unittest.TestCase):
    def test_it_is_well_formed_and_carries_the_disclaimer(self):
        root = ET.parse(os.path.join(ADDON, "addon.xml")).getroot()
        disclaimers = root.findall(".//disclaimer")
        self.assertGreaterEqual(len(disclaimers), 2)
        text = " ".join(d.text or "" for d in disclaimers)
        self.assertIn("no warranty", text.lower())
        for language in ("en_GB", "he_IL"):
            self.assertTrue(any(d.get("lang") == language for d in disclaimers))

    def test_the_licence_is_mit(self):
        root = ET.parse(os.path.join(ADDON, "addon.xml")).getroot()
        self.assertEqual(root.findtext(".//license"), "MIT")


if __name__ == "__main__":
    unittest.main()
