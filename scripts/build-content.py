#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml"]
# ///
"""Render story.yaml, calendar.yaml and gallery.yaml into public/index.html.

Each file fills one marked block in the page — STORY, GIGS and GALLERY. Run
after editing any of them:

    ./scripts/build-content.py

The shebang runs it through uv, which resolves pyyaml from the inline metadata
below, so no virtualenv setup is needed. `uv run scripts/build-content.py` and
`python3 scripts/build-content.py` (with pyyaml installed) both work too.

Use --check to verify the page is up to date without writing (exit 1 if not).
"""

import argparse
import datetime as dt
import html
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = ROOT / "public" / "index.html"
PUBLIC = ROOT / "public"

CALENDAR_YAML = ROOT / "calendar.yaml"
STORY_YAML = ROOT / "story.yaml"
GALLERY_YAML = ROOT / "gallery.yaml"

INDENT = " " * 10

# Roughly one screenful of prose; past this the Story panel scrolls internally.
STORY_SOFT_LIMIT = 1200

INSTAGRAM = "https://www.instagram.com/spellbinding.band/"


def die(msg):
    sys.exit(f"build-content: {msg}")


def parse_time(raw, where):
    """Normalise an event time to "HH:MM", or None when not given.

    YAML 1.1 reads an unquoted 20:00 as the sexagesimal integer 1200, while
    09:05 stays a string because the leading zero blocks that rule. Both forms
    are accepted here so the file behaves the same either way.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        die(f"{where}: time {raw!r} is not a time")
    if isinstance(raw, dt.time):
        return f"{raw.hour:02d}:{raw.minute:02d}"
    if isinstance(raw, int):
        if 0 <= raw <= 23 * 60 + 59:
            return f"{raw // 60:02d}:{raw % 60:02d}"
        die(
            f'{where}: time was read as the number {raw}. Quote it, '
            f'e.g. time: "20:00"'
        )
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(raw).strip())
    if not m:
        die(f'{where}: time {raw!r} is not HH:MM, e.g. time: "20:00"')
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        die(f"{where}: time {raw!r} is not a valid 24-hour time")
    return f"{hh:02d}:{mm:02d}"


def load_yaml(path, what):
    if not path.exists():
        die(f"{path.name} not found")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        die(f"{path.name} is not valid YAML:\n{e}")
    if not isinstance(data, dict):
        die(f"{path.name} must be a mapping with {what}")
    return data


def load_events():
    data = load_yaml(CALENDAR_YAML, "'note' and 'events' keys")

    note = data.get("note") or ""
    if not isinstance(note, str):
        die("'note' must be a string")

    raw = data.get("events")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        die("'events' must be a list")

    events = []
    for i, ev in enumerate(raw, 1):
        where = f"events[{i}]"
        if not isinstance(ev, dict):
            die(f"{where} must be a mapping")
        missing = [k for k in ("date", "venue", "city") if not ev.get(k)]
        if missing:
            die(f"{where} is missing required field(s): {', '.join(missing)}")

        date = ev["date"]
        if isinstance(date, dt.datetime):
            date = date.date()
        if not isinstance(date, dt.date):
            try:
                date = dt.date.fromisoformat(str(date))
            except ValueError:
                die(f"{where}: date {ev['date']!r} is not a YYYY-MM-DD date")

        events.append(
            {
                "date": date,
                "time": parse_time(ev.get("time"), where),
                "venue": str(ev["venue"]),
                "city": str(ev["city"]),
                "status": str(ev["status"]) if ev.get("status") else "",
                "url": str(ev["url"]) if ev.get("url") else "",
            }
        )

    events.sort(key=lambda e: (e["date"], e["time"] or ""))
    return note, events


def banner(name, src):
    return [
        f"<!-- {name}:START — generated from {src} by scripts/build-content.py.",
        f"{INDENT}     Edit the YAML and re-run; do not hand-edit this block. -->",
    ]


def load_story():
    data = load_yaml(STORY_YAML, "'heading' and 'paragraphs' keys")
    heading = str(data.get("heading") or "Story")
    raw = data.get("paragraphs") or []
    if not isinstance(raw, list):
        die("story.yaml: 'paragraphs' must be a list")
    paras = []
    for i, para in enumerate(raw, 1):
        if not isinstance(para, (str, int, float)):
            die(f"story.yaml: paragraphs[{i}] must be text")
        text = " ".join(str(para).split())      # fold YAML's wrapped lines
        if text:
            paras.append(text)
    return heading, paras


def render_story(heading, paras):
    e = html.escape
    out = banner("STORY", "story.yaml")
    out.append(f'{INDENT}<h2 id="story-title">{e(heading)}</h2>')
    if not paras:
        out.append(f'{INDENT}<p class="note">Nothing here yet.</p>')
    else:
        out.append(f'{INDENT}<div class="story">')
        for para in paras:
            out.append(f"{INDENT}  <p>{e(para)}</p>")
        out.append(f"{INDENT}</div>")
    out.append(f"{INDENT}<!-- STORY:END -->")
    return "\n".join(out)


def load_gallery():
    data = load_yaml(GALLERY_YAML, "'heading', 'note' and 'images' keys")
    heading = str(data.get("heading") or "Gallery")
    note = data.get("note") or ""
    if not isinstance(note, str):
        die("gallery.yaml: 'note' must be a string")
    raw = data.get("images") or []
    if not isinstance(raw, list):
        die("gallery.yaml: 'images' must be a list")
    images = []
    for i, img in enumerate(raw, 1):
        where = f"gallery.yaml: images[{i}]"
        if not isinstance(img, dict):
            die(f"{where} must be a mapping")
        missing = [k for k in ("src", "alt") if not img.get(k)]
        if missing:
            die(f"{where} is missing required field(s): {', '.join(missing)}")
        src = str(img["src"]).lstrip("/")
        # Fail here rather than ship a page with a broken image in it.
        if not (PUBLIC / src).exists():
            die(f"{where}: public/{src} does not exist")
        images.append({
            "src": src,
            "alt": str(img["alt"]),
            "caption": str(img["caption"]) if img.get("caption") else "",
        })
    return heading, note, images


def render_gallery(heading, note, images):
    e = html.escape
    out = banner("GALLERY", "gallery.yaml")
    out.append(f'{INDENT}<h2 id="gallery-title">{e(heading)}</h2>')
    if note:
        out.append(f'{INDENT}<p class="note">{e(note)}</p>')
    if images:
        out.append(f'{INDENT}<ul class="shots">')
        for img in images:
            out.append(f"{INDENT}  <li><figure>")
            out.append(f'{INDENT}    <img src="{e(img["src"])}" alt="{e(img["alt"])}" loading="lazy">')
            if img["caption"]:
                out.append(f'{INDENT}    <figcaption>{e(img["caption"])}</figcaption>')
            out.append(f"{INDENT}  </figure></li>")
        out.append(f"{INDENT}</ul>")
    elif not note:
        out.append(f'{INDENT}<p class="note">No photos yet.</p>')
    out.append(f"{INDENT}<!-- GALLERY:END -->")
    return "\n".join(out)


def render(note, events):
    e = html.escape
    out = banner("GIGS", "calendar.yaml")

    if note:
        out.append(f'{INDENT}<p class="note">{e(note)}</p>')

    if not events:
        out.append(f'{INDENT}<p class="note">No dates announced yet. Follow')
        out.append(f'{INDENT}  <a href="{INSTAGRAM}">@spellbinding.band</a>')
        out.append(f"{INDENT}  for announcements.</p>")
    else:
        out.append(f'{INDENT}<ul class="gigs">')
        for ev in events:
            d = ev["date"]
            venue = e(ev["venue"])
            if ev["url"]:
                venue = f'<a href="{e(ev["url"])}">{venue}</a>'
            out.append(f'{INDENT}  <li class="gig">')
            stamp = d.isoformat() + (f"T{ev['time']}" if ev["time"] else "")
            at = f'<span class="at">{ev["time"]}</span>' if ev["time"] else ""
            out.append(
                f'{INDENT}    <time datetime="{stamp}">'
                f'<span class="dow">{d:%a}</span>'
                f"{d.day:02d} {d:%b} {d.year}{at}</time>"
            )
            out.append(f"{INDENT}    <div>")
            out.append(f'{INDENT}      <span class="venue">{venue}</span>')
            out.append(f'{INDENT}      <span class="city">{e(ev["city"])}</span>')
            out.append(f"{INDENT}    </div>")
            if ev["status"]:
                out.append(f'{INDENT}    <span class="status">{e(ev["status"])}</span>')
            else:
                out.append(f"{INDENT}    <span></span>")
            out.append(f"{INDENT}  </li>")
        out.append(f"{INDENT}</ul>")

    out.append(f"{INDENT}<!-- GIGS:END -->")
    return "\n".join(out)


def replace_block(page, name, block):
    pattern = re.compile(
        re.escape(f"<!-- {name}:START") + r".*?" + re.escape(f"<!-- {name}:END -->"),
        re.S,
    )
    if not pattern.search(page):
        die(f"{name} markers not found in {HTML_PATH.name}")
    return pattern.sub(lambda _: block, page, count=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the page is out of date, without writing",
    )
    args = ap.parse_args()

    s_heading, paras = load_story()
    note, events = load_events()
    g_heading, g_note, images = load_gallery()

    page = HTML_PATH.read_text(encoding="utf-8")
    updated = page
    updated = replace_block(updated, "STORY", render_story(s_heading, paras))
    updated = replace_block(updated, "GIGS", render(note, events))
    updated = replace_block(updated, "GALLERY", render_gallery(g_heading, g_note, images))

    if args.check:
        if updated != page:
            sys.exit("build-content: public/index.html is out of date — run without --check")
        print("build-content: up to date")
        return

    chars = sum(len(p) for p in paras)
    summary = (
        f"story {len(paras)} para(s)/{chars} chars, "
        f"{len(events)} event(s), {len(images)} photo(s)"
    )
    if chars > STORY_SOFT_LIMIT:
        print(
            f"build-content: warning — story is {chars} characters; past about "
            f"{STORY_SOFT_LIMIT} the Story panel scrolls instead of fitting a screen"
        )

    if updated == page:
        print(f"build-content: no change ({summary})")
        return

    HTML_PATH.write_text(updated, encoding="utf-8")
    print(f"build-content: wrote {summary}")


if __name__ == "__main__":
    main()
