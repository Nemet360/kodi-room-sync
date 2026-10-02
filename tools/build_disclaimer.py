#!/usr/bin/env python3
"""Render DISCLAIMER.md into disclaimer.html for the GitHub Pages site.

The disclaimer exists in two places that people actually read — the Markdown
file in the repository and the page on the site — and a legal text that says two
different things is worse than one that is only in one place. So there is one
source and one generator, and `tools/tests/test_build_disclaimer.py` fails if
the committed HTML has drifted from the Markdown.

The Markdown subset handled here is exactly what DISCLAIMER.md uses: `#`/`##`
headings, `**bold**`, `[text](url)`, backtick code, ordered and unordered lists,
paragraphs, and `---`. Anything else is passed through escaped — a silent
mis-render of a legal page is the failure this avoids, so unknown syntax should
look wrong rather than disappear.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "DISCLAIMER.md"
TARGET = ROOT / "disclaimer.html"

HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Room Watch Sync - Disclaimer</title>
<meta name="robots" content="index,follow">
<!-- Generated from DISCLAIMER.md by tools/build_disclaimer.py. Do not edit by
     hand: a legal text that exists in two versions is worse than one. -->
<style>
  :root { color-scheme: dark light; --ink:#e8e6e3; --dim:#9a958f; --bg:#16181c;
          --line:#2c3038; --accent:#7fb4ff; }
  body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.7
         ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  main { max-width:48rem; margin:0 auto; padding:2.5rem 1.25rem 5rem; }
  h1 { font-size:1.8rem; line-height:1.2; }
  h2 { font-size:1.15rem; margin-top:2.2rem; border-top:1px solid var(--line);
       padding-top:1.3rem; }
  a { color:var(--accent); }
  code { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
         font-size:.9em; background:#12141a; border:1px solid var(--line);
         border-radius:4px; padding:.05em .35em; }
  li { margin:.4rem 0; }
  hr { border:0; border-top:1px solid var(--line); margin:2.5rem 0; }
  .back { color:var(--dim); font-size:.9rem; }
  [dir="rtl"] { text-align:right; }
</style>
</head>
<body>
<main>
<p class="back"><a href="./">&larr; Room Watch Sync</a></p>
"""

FOOT = """</main>
</body>
</html>
"""

_HEBREW = re.compile(r"[֐-׿]")


def _inline(text: str) -> str:
    """Escape first, then re-introduce the handful of allowed tags."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', out)
    return out


def _dir_attr(text: str) -> str:
    """Hebrew sections must render right to left or they are unreadable."""
    return ' dir="rtl"' if _HEBREW.search(text) else ""


def render(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    parts = [HEAD]
    paragraph: list[str] = []
    list_kind = ""

    def flush_paragraph():
        if paragraph:
            text = " ".join(paragraph).strip()
            parts.append("<p%s>%s</p>\n" % (_dir_attr(text), _inline(text)))
            paragraph.clear()

    def flush_list():
        nonlocal list_kind
        if list_kind:
            parts.append("</%s>\n" % list_kind)
            list_kind = ""

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        if stripped.startswith("> "):
            # The blockquote at the top of the file is a note about the two
            # languages; keep it, visibly set apart.
            flush_paragraph()
            flush_list()
            body = _inline(stripped[2:])
            parts.append('<p class="back">%s</p>\n' % body)
            continue
        if stripped == "---":
            flush_paragraph()
            flush_list()
            parts.append("<hr>\n")
            continue

        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            text = heading.group(2)
            parts.append("<h%d%s>%s</h%d>\n"
                         % (level, _dir_attr(text), _inline(text), level))
            continue

        ordered = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        if ordered or bullet:
            flush_paragraph()
            kind = "ol" if ordered else "ul"
            if list_kind != kind:
                flush_list()
                parts.append("<%s>\n" % kind)
                list_kind = kind
            text = (ordered or bullet).group(2 if ordered else 1)
            parts.append("<li%s>%s</li>\n" % (_dir_attr(text), _inline(text)))
            continue

        if list_kind:
            # A continuation line inside a list item.
            parts[-1] = parts[-1].replace("</li>\n", " " + _inline(stripped) + "</li>\n")
            continue

        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    parts.append(FOOT)
    return "".join(parts)


def main() -> int:
    if not SOURCE.is_file():
        print("DISCLAIMER.md is missing", file=sys.stderr)
        return 2
    rendered = render(SOURCE.read_text(encoding="utf-8"))
    current = TARGET.read_text(encoding="utf-8") if TARGET.is_file() else ""
    if current == rendered:
        print("disclaimer.html is current")
        return 0
    TARGET.write_text(rendered, encoding="utf-8")
    print("wrote %s (%d bytes)" % (TARGET.name, len(rendered)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
