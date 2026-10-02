"""The disclaimer exists once, and the site cannot drift from it.

A legal text that says two different things in two places is worse than one
that lives in a single place. These tests fail if the committed
`disclaimer.html` no longer matches `DISCLAIMER.md`, and if the renderer ever
starts dropping or mangling content silently — a mis-rendered legal page looks
fine and is not.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import build_disclaimer  # noqa: E402


class TestInSync(unittest.TestCase):
    def test_the_committed_page_matches_the_markdown(self):
        """Run `python tools/build_disclaimer.py` if this fails."""
        self.assertTrue(build_disclaimer.SOURCE.is_file())
        self.assertTrue(build_disclaimer.TARGET.is_file(),
                        "disclaimer.html has not been generated")
        expected = build_disclaimer.render(
            build_disclaimer.SOURCE.read_text(encoding="utf-8"))
        self.assertEqual(build_disclaimer.TARGET.read_text(encoding="utf-8"),
                         expected,
                         "disclaimer.html is stale — regenerate it")


class TestRenderer(unittest.TestCase):
    def render(self, markdown):
        return build_disclaimer.render(markdown)

    def test_headings_become_headings(self):
        out = self.render("# Title\n\n## Section\n")
        self.assertIn("<h1>Title</h1>", out)
        self.assertIn("<h2>Section</h2>", out)

    def test_bold_and_links_survive(self):
        out = self.render("**no warranty** and [LICENSE](LICENSE)\n")
        self.assertIn("<strong>no warranty</strong>", out)
        self.assertIn('<a href="LICENSE">LICENSE</a>', out)

    def test_html_in_the_source_is_escaped_not_executed(self):
        """The source is a file somebody edits; it is not a template."""
        out = self.render("A <script>alert(1)</script> line\n")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_a_numbered_list_of_prohibitions_stays_numbered(self):
        """The prohibited-use list is referred to by number in the add-on's own
        disclaimer text; losing the numbering loses the reference."""
        out = self.render("1. first\n2. second\n")
        self.assertIn("<ol>", out)
        self.assertEqual(out.count("<li"), 2)

    def test_a_bulleted_list_stays_bulleted(self):
        out = self.render("- one\n- two\n")
        self.assertIn("<ul>", out)
        self.assertEqual(out.count("<li"), 2)

    def body(self, markdown):
        """Only what is inside <main>: the stylesheet itself contains a
        `[dir="rtl"]` selector, so matching the whole document would pass no
        matter what the renderer did."""
        return self.render(markdown).split("<main>")[1]

    def test_hebrew_is_marked_right_to_left(self):
        """Hebrew rendered left to right is unreadable, and nothing errors."""
        out = self.body("## כתב ויתור\n")
        self.assertIn('dir="rtl"', out)

    def test_english_is_not_marked_right_to_left(self):
        self.assertNotIn('dir="rtl"', self.body("## Disclaimer\n"))

    def test_a_wrapped_paragraph_becomes_one_paragraph(self):
        out = self.render("one line\ncontinued here\n")
        self.assertEqual(out.count("<p>"), 1)
        self.assertIn("one line continued here", out)

    def test_a_rule_becomes_a_rule(self):
        self.assertIn("<hr>", self.render("a\n\n---\n\nb\n"))

    def test_every_prohibition_in_the_real_file_reaches_the_page(self):
        """The five numbered prohibitions are the operative part."""
        source = build_disclaimer.SOURCE.read_text(encoding="utf-8")
        out = self.body(source)
        for phrase in ("circumvent", "DRM", "copyright", "no warranty"):
            self.assertIn(phrase.lower(), out.lower(), phrase)
        self.assertGreaterEqual(out.count("<li"), 10)

    def test_the_page_names_both_languages(self):
        out = self.body(build_disclaimer.SOURCE.read_text(encoding="utf-8"))
        self.assertIn('dir="rtl"', out)
        self.assertIn("Disclaimer", out)

    def test_nothing_is_silently_dropped(self):
        """Compare word counts: a renderer that eats a clause is the failure
        mode here, and it looks like a perfectly fine page."""
        source = build_disclaimer.SOURCE.read_text(encoding="utf-8")
        text = re.sub(r"<[^>]+>", " ", self.body(source))
        source_words = len(re.findall(r"\w+", re.sub(r"[#*`>\-]", " ", source)))
        page_words = len(re.findall(r"\w+", text))
        self.assertGreater(page_words, source_words * 0.9)


if __name__ == "__main__":
    unittest.main()
