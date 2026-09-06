#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.9"
# ///
"""Mirror public/ into dist/ with the comments taken out of the HTML.

The page carries its own documentation: 16 kB of it, explaining why the CSS
overrides sit where they do, why nothing above the fold uses dvh, why play()
comes after createMediaElementSource(). That belongs in the repository and not
on the wire — it is 35% of what a visitor downloads gzipped.

So public/ stays the source of truth, comments and all, and this builds the
copy that ships:

    ./scripts/strip-comments.py            # write dist/
    ./scripts/strip-comments.py --check    # verify it is current (exit 1 if not)

Only *.html is rewritten. Everything else is hard-linked, so the photos and the
audio are not duplicated on disk.

Comments are removed by scanning, not by regex: a scanner knows that the //
in "https://schema.org" is inside a string and that a /* inside a template
literal is text. The page has no regex literals, and a `/` is therefore only
ever division or the start of a comment — assert_no_regex_literals() fails the
build if that ever stops being true.

Nothing here reflows or reindents. A template literal in the page carries
markup whose whitespace reaches the DOM, so the only whitespace this touches is
the indentation in front of a comment that occupied its whole line.
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "public"
OUT = ROOT / "dist"

# <style>…</style> and <script>…</script>, whose insides are not HTML.
REGION = re.compile(r"(<style[^>]*>)(.*?)(</style>)"
                    r"|(<script[^>]*>)(.*?)(</script>)", re.S | re.I)


def die(msg):
    sys.exit(f"strip-comments: {msg}")


def _scan(src, where, html=False, line=False):
    """Drop comments from src, leaving everything else byte for byte.

    html   — <!-- … --> instead of /* … */
    line   — also drop // to end of line (JavaScript, not CSS)

    Quotes are tracked so a comment opener inside a string or a template
    literal is left alone. When a comment had its line to itself the
    indentation in front of it and the newline after it go too, so removing it
    does not leave a blank line behind.
    """
    out, i, n, quote = [], 0, len(src), None

    def alone_on_its_line():
        k = len(out) - 1
        while k >= 0 and out[k] != "\n":
            if not out[k].isspace():
                return False
            k -= 1
        return True

    def drop_indent():
        while out and out[-1] != "\n" and out[-1].isspace():
            out.pop()

    while i < n:
        c = src[i]
        if quote:
            if c == "\\" and i + 1 < n:                 # an escaped quote
                out.append(src[i:i + 2])
                i += 2
                continue
            if c == quote:
                quote = None
            out.append(c)
            i += 1
            continue
        if not html and c in "'\"`":
            quote = c
            out.append(c)
            i += 1
            continue

        opener, closer = ("<!--", "-->") if html else ("/*", "*/")
        if src.startswith(opener, i):
            end = src.find(closer, i + len(opener))
            if end < 0:
                die(f"{where}: unterminated {opener} comment")
            solo = alone_on_its_line()
            i = end + len(closer)
        elif line and src.startswith("//", i):
            solo = alone_on_its_line()
            nl = src.find("\n", i)
            i = n if nl < 0 else nl
        else:
            out.append(c)
            i += 1
            continue

        if solo:
            drop_indent()
            if i < n and src[i] == "\n":
                i += 1
    return "".join(out)


def assert_no_regex_literals(js, where):
    """A regex literal would make `/` ambiguous for the scanner.

    There are none in the page today. If one is ever added, stop rather than
    silently eat it: /(\\d+)\\/\\/(x)/ starts with something a naive scan reads
    as a line comment.
    """
    stripped = _scan(js, where, line=True)
    for m in re.finditer(r"(?:[=(,:[&|!?+\-*%~]|\breturn|\btypeof)\s*/", stripped):
        line_no = stripped.count("\n", 0, m.start()) + 1
        die(f"{where}: what looks like a regex literal at line {line_no} of the "
            f"script — the scanner cannot tell it from a comment; move it into "
            f"new RegExp(…) or teach _scan() about regex literals")


def strip_page(text, where):
    """Strip comments from one HTML file, each region in its own language."""
    out, at = [], 0
    for m in REGION.finditer(text):
        out.append(_scan(text[at:m.start()], where, html=True))
        if m.group(1):                                  # <style>
            out.append(m.group(1))
            out.append(_scan(m.group(2), f"{where} <style>"))
            out.append(m.group(3))
        else:                                           # <script>
            tag, body, close = m.group(4), m.group(5), m.group(6)
            out.append(tag)
            if "json" in tag.lower():
                # JSON has no comments, and "https://…" values are everywhere.
                out.append(body)
            else:
                assert_no_regex_literals(body, f"{where} <script>")
                out.append(_scan(body, f"{where} <script>", line=True))
            out.append(close)
        at = m.end()
    out.append(_scan(text[at:], where, html=True))
    return "".join(out)


def build(check_only):
    if not SRC.is_dir():
        die("public/ not found")
    stale, wrote = [], []

    wanted = set()
    for src in sorted(SRC.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(SRC)
        wanted.add(rel)
        dst = OUT / rel

        if src.suffix.lower() in (".html", ".htm"):
            text = src.read_text(encoding="utf-8")
            new = strip_page(text, f"public/{rel}")
            # Post-condition: stripping the result again must change nothing.
            # If a second pass still finds a comment, the first pass left one
            # behind or mangled a string into looking like one.
            if strip_page(new, f"public/{rel}") != new:
                die(f"public/{rel}: stripping is not idempotent — a comment "
                    f"survived the first pass, or a string was misread")
            if not dst.exists() or dst.read_text(encoding="utf-8") != new:
                stale.append(rel)
                if not check_only:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(new, encoding="utf-8")
                    wrote.append((rel, len(text.encode()), len(new.encode())))
            continue

        # Everything else ships unchanged: hard-link it rather than copy, so
        # the photos and the audio exist once on disk.
        if not dst.exists() or not src.samefile(dst):
            stale.append(rel)
            if not check_only:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    dst.unlink()
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copy2(src, dst)

    for old in sorted(OUT.rglob("*")) if OUT.is_dir() else []:
        if old.is_file() and old.relative_to(OUT) not in wanted:
            stale.append(old.relative_to(OUT))
            if not check_only:
                old.unlink()

    if check_only:
        if stale:
            die(f"dist/ is out of date ({len(stale)} file(s), first: {stale[0]}) "
                f"— run without --check")
        print(f"strip-comments: up to date ({len(wanted)} file(s))")
        return
    if not stale:
        print(f"strip-comments: no change ({len(wanted)} file(s))")
        return
    for rel, was, now in wrote:
        print(f"strip-comments: {rel} {was:,} -> {now:,} bytes "
              f"(-{(was - now) / was:.0%})")
    print(f"strip-comments: {len(wanted)} file(s) in dist/")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="verify dist/ is current without writing")
    build(ap.parse_args().check)


if __name__ == "__main__":
    main()
