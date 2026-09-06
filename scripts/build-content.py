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

Each tab is one screenful. Content longer than that becomes several pages that
snap vertically inside the tab: story.yaml lists its pages explicitly, while
the gig list and the gallery chunk automatically on `per_page`.

Text supports a small, safe Markdown subset — links, bold, italic, code, and
"- item" bullet lists — see md() and md_blocks() below. Use an `html:` block
when raw markup is genuinely needed.

Use --check to verify the page is up to date without writing (exit 1 if not).
"""

import argparse
import datetime as dt
import html
import json
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

I = " " * 8              # indent of a .page inside .tab
INSTAGRAM = "https://www.instagram.com/spellbinding.band/"
SITE = "https://spellbinding.band"

# Schemes a link in Markdown text may use.
SAFE_SCHEMES = ("http://", "https://", "mailto:", "/", "#", "photos/")


def die(msg):
    sys.exit(f"build-content: {msg}")


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


# ── inline markup ────────────────────────────────────────────────────────────

def check_href(url, where):
    if not url.startswith(SAFE_SCHEMES):
        die(
            f"{where}: link target {url!r} is not allowed. Use http://, "
            f"https://, mailto:, or a path inside public/"
        )
    return url


def md(text, where):
    """Escape, then apply a small Markdown subset.

    Escaping first is the whole point: whatever is in the YAML cannot inject
    markup, and only the patterns below become HTML.
    """
    out = html.escape(str(text))
    # [label](target)
    def link(m):
        label, url = m.group(1), html.unescape(m.group(2))
        check_href(url, where)
        return f'<a href="{html.escape(url)}">{label}</a>'
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![*\w])\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return out


# A list item: "-" or "*" at the start of a line, then a space, then content.
# A line like "*italic* first" is not a marker: the marker needs the space.
LIST_ITEM = re.compile(r"^[ \t]*[-*][ \t]+(.*)$")


def md_blocks(text, where, indent):
    """Render one text block into paragraphs and bullet lists.

    Lines are gathered into runs: consecutive "- item" lines become one <ul>,
    everything else becomes a <p>, and a blank line ends whichever run it
    follows. Only the marker is consumed here — every character that reaches
    the page still goes through md(), which escapes before it substitutes, so
    a YAML file still cannot inject markup.

    Multi-line text needs a literal block scalar (text: |-), not a folded one
    (text: >-): folding joins the lines with spaces, leaving no line starts for
    a marker to sit at.
    """
    out, para, items = [], [], []

    def flush_para():
        if para:
            out.append(f"{indent}<p>{md(' '.join(para), where)}</p>")
            para.clear()

    def flush_items():
        if items:
            out.append(f"{indent}<ul>")
            out.extend(f"{indent}  <li>{md(it, where)}</li>" for it in items)
            out.append(f"{indent}</ul>")
            items.clear()

    for line in str(text).split("\n"):
        m = LIST_ITEM.match(line)
        if m:
            flush_para()
            item = m.group(1).strip()
            if not item:
                die(f"{where}: empty list item — drop the stray marker")
            items.append(item)
        elif line.strip():
            flush_items()
            para.append(line.strip())
        else:
            flush_para()
            flush_items()
    flush_para()
    flush_items()
    if not out:
        die(f"{where}: text is empty")
    return out


def asset(src, where):
    """A path inside public/, verified to exist."""
    src = str(src).lstrip("/")
    if not (PUBLIC / src).exists():
        die(f"{where}: public/{src} does not exist")
    return src


# ── blocks (story pages) ─────────────────────────────────────────────────────

def render_blocks(blocks, indent, where):
    e = html.escape
    out = [f'{indent}<div class="prose">']
    for n, block in enumerate(blocks, 1):
        at = f"{where} block {n}"
        if not isinstance(block, dict):
            die(f"{at} must be a mapping, e.g. '- text: …'")
        if "text" in block:
            out.extend(md_blocks(block["text"], at, indent + "  "))
        elif "html" in block:
            out.append(f"{indent}  {block['html']}")
        elif "image" in block:
            if not block.get("alt"):
                die(f"{at}: an image needs alt text")
            src = asset(block["image"], at)
            out.append(f"{indent}  <figure>")
            out.append(
                f'{indent}    <img src="{e(src)}" alt="{e(str(block["alt"]))}" loading="lazy">'
            )
            if block.get("caption"):
                out.append(
                    f'{indent}    <figcaption>{md(block["caption"], at)}</figcaption>'
                )
            out.append(f"{indent}  </figure>")
        else:
            die(f"{at}: unknown block. Use text:, image: or html:")
    out.append(f"{indent}</div>")
    return out


def load_story():
    data = load_yaml(STORY_YAML, "'heading' and 'pages' keys")
    heading = str(data.get("heading") or "Story")

    pages = data.get("pages")
    if pages is None:
        # Older shape: a flat list of paragraphs is one page of text blocks.
        paras = data.get("paragraphs") or []
        if not isinstance(paras, list):
            die("story.yaml: 'paragraphs' must be a list")
        pages = [{"blocks": [{"text": p} for p in paras]}] if paras else []
    if not isinstance(pages, list):
        die("story.yaml: 'pages' must be a list")

    clean = []
    for i, page in enumerate(pages, 1):
        if not isinstance(page, dict) or "blocks" not in page:
            die(f"story.yaml: pages[{i}] must be a mapping with a 'blocks' list")
        blocks = page["blocks"]
        if not isinstance(blocks, list) or not blocks:
            die(f"story.yaml: pages[{i}].blocks must be a non-empty list")
        clean.append(blocks)
    return heading, clean


def render_story(heading, pages):
    e = html.escape
    out = [
        "<!-- STORY:START — generated from story.yaml by scripts/build-content.py.",
        f"{I}     Edit the YAML and re-run; do not hand-edit this block. -->",
    ]
    if not pages:
        out.append(f'{I}<div class="page"><div class="wrap">')
        out.append(f'{I}  <h2 id="story-title">{e(heading)}</h2>')
        out.append(f'{I}  <p class="note">Nothing here yet.</p>')
        out.append(f"{I}</div></div>")
    else:
        for i, blocks in enumerate(pages, 1):
            out.append(f'{I}<div class="page"><div class="wrap">')
            if i == 1:
                out.append(f'{I}  <h2 id="story-title">{e(heading)}</h2>')
            out.extend(render_blocks(blocks, I + "  ", f"story.yaml page {i}"))
            out.append(f"{I}</div></div>")
    out.append(f"{I}<!-- STORY:END -->")
    return "\n".join(out)


# ── gigs ─────────────────────────────────────────────────────────────────────

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
        die(f'{where}: time was read as the number {raw}. Quote it, e.g. time: "20:00"')
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(raw).strip())
    if not m:
        die(f'{where}: time {raw!r} is not HH:MM, e.g. time: "20:00"')
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        die(f"{where}: time {raw!r} is not a valid 24-hour time")
    return f"{hh:02d}:{mm:02d}"


def load_events():
    data = load_yaml(CALENDAR_YAML, "'note' and 'events' keys")

    heading = str(data.get("heading") or "Spellbinding.band LIVE")
    note = data.get("note") or ""
    if not isinstance(note, str):
        die("calendar.yaml: 'note' must be a string")
    per_page = data.get("per_page") or 0
    if not isinstance(per_page, int) or per_page < 0:
        die("calendar.yaml: 'per_page' must be a whole number (0 = no paging)")

    raw = data.get("events") or []
    if not isinstance(raw, list):
        die("calendar.yaml: 'events' must be a list")

    events = []
    for i, ev in enumerate(raw, 1):
        where = f"calendar.yaml: events[{i}]"
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

        events.append({
            "date": date,
            "time": parse_time(ev.get("time"), where),
            "venue": str(ev["venue"]),
            "city": str(ev["city"]),
            "status": str(ev["status"]) if ev.get("status") else "",
            "url": str(ev["url"]) if ev.get("url") else "",
        })

    events.sort(key=lambda e: (e["date"], e["time"] or ""))
    return heading, note, per_page, events


def render_gig(ev, indent, where):
    e = html.escape
    d = ev["date"]
    venue = e(ev["venue"])
    if ev["url"]:
        venue = f'<a href="{e(check_href(ev["url"], where))}">{venue}</a>'
    stamp = d.isoformat() + (f"T{ev['time']}" if ev["time"] else "")
    at = f'<span class="at">{ev["time"]}</span>' if ev["time"] else ""
    return [
        f'{indent}<li class="gig">',
        f'{indent}  <time datetime="{stamp}"><span class="dow">{d:%a}</span>'
        f"{d.day:02d} {d:%b} {d.year}{at}</time>",
        f"{indent}  <div>",
        f'{indent}    <span class="venue">{venue}</span>',
        f'{indent}    <span class="city">{e(ev["city"])}</span>',
        f"{indent}  </div>",
        (f'{indent}  <span class="status">{e(ev["status"])}</span>'
         if ev["status"] else f"{indent}  <span></span>"),
        f"{indent}</li>",
    ]


def gigs_jsonld(events, indent):
    """One MusicEvent per gig, so the calendar is machine-readable.

    The rows are already semantic HTML — the <time datetime> carries the same
    stamp this does — but only schema.org makes a listing legible as *events*
    to a search engine's rich results, or to a crawler that wants the date and
    the venue rather than the sentence around them.

    `status:` in calendar.yaml is free text, so it is deliberately not mapped
    to eventStatus: deciding that "Loppuunmyyty" means SoldOut would be
    inventing a meaning the field does not carry. Nor is a timezone invented —
    startDate is the same local time the page shows, which schema.org allows.
    """
    items = []
    for n, ev in enumerate(events, 1):
        where = f"calendar.yaml: events[{n}]"
        item = {
            "@type": "MusicEvent",
            "name": f"Spellbinding — {ev['venue']}, {ev['city']}",
            "startDate": ev["date"].isoformat()
                         + (f"T{ev['time']}" if ev["time"] else ""),
            "location": {
                "@type": "Place",
                "name": ev["venue"],
                "address": {"@type": "PostalAddress",
                            "addressLocality": ev["city"]},
            },
            "performer": {"@type": "MusicGroup", "name": "Spellbinding",
                          "url": SITE},
            "image": f"{SITE}/share.jpg",
        }
        if ev["url"]:
            item["url"] = check_href(ev["url"], where)
        items.append(item)

    body = json.dumps({"@context": "https://schema.org", "@graph": items},
                      ensure_ascii=False, indent=2)
    # JSON does not escape "<", so a venue named "</script>" would end the
    # block early and spill the rest into the document as markup.
    body = body.replace("<", "\\u003c")
    out = [f'{indent}<script type="application/ld+json">']
    out += [f"{indent}{line}" for line in body.splitlines()]
    out.append(f"{indent}</script>")
    return out


def render_gigs(heading, note, per_page, events):
    e = html.escape
    out = [
        "<!-- GIGS:START — generated from calendar.yaml by scripts/build-content.py.",
        f"{I}     Edit the YAML and re-run; do not hand-edit this block. -->",
    ]
    chunks = chunk(events, per_page)
    for i, group in enumerate(chunks, 1):
        out.append(f'{I}<div class="page"><div class="wrap">')
        if i == 1:
            out.append(f'{I}  <h2 id="live-title">{e(heading)}</h2>')
            if note:
                out.append(f'{I}  <p class="note">{md(note, "calendar.yaml note")}</p>')
        if not group:
            out.append(f'{I}  <p class="note">Päivämääriä ei ole vielä julkaistu. Seuraa')
            out.append(f'{I}    <a href="{INSTAGRAM}">@spellbinding.band</a>')
            out.append(f"{I}    ilmoituksia.</p>")
        else:
            out.append(f'{I}  <ul class="gigs">')
            for n, ev in enumerate(group, 1):
                out.extend(render_gig(ev, I + "    ", f"calendar.yaml: events[{n}]"))
            out.append(f"{I}  </ul>")
        out.append(f"{I}</div></div>")
    if events:
        out.extend(gigs_jsonld(events, I))
    out.append(f"{I}<!-- GIGS:END -->")
    return "\n".join(out)


# ── gallery ──────────────────────────────────────────────────────────────────

def load_gallery():
    data = load_yaml(GALLERY_YAML, "'heading', 'note' and 'items' keys")
    heading = str(data.get("heading") or "Gallery")
    note = data.get("note") or ""
    if not isinstance(note, str):
        die("gallery.yaml: 'note' must be a string")
    per_page = data.get("per_page") or 0
    if not isinstance(per_page, int) or per_page < 0:
        die("gallery.yaml: 'per_page' must be a whole number (0 = no paging)")

    raw = data.get("items")
    if raw is None:
        raw = data.get("images") or []      # older key
    if not isinstance(raw, list):
        die("gallery.yaml: 'items' must be a list")

    items = []
    for i, it in enumerate(raw, 1):
        where = f"gallery.yaml: items[{i}]"
        if not isinstance(it, dict):
            die(f"{where} must be a mapping")
        if not it.get("alt"):
            die(f"{where}: needs alt text describing the image")
        kind = "video" if it.get("video") else "image"
        if kind == "video":
            if not it.get("poster"):
                die(f"{where}: a video needs a 'poster' image to show as its tile")
            src = asset(it["poster"], where)
            href = check_href(str(it["video"]), where)
        else:
            if not it.get("image"):
                die(f"{where}: needs either 'image:' or 'video:'")
            src = asset(it["image"], where)
            href = check_href(str(it["href"]), where) if it.get("href") else ""
        # scripts/prep-images.py writes NAME-thumb.jpg beside NAME.jpg; use it
        # for the tile when it is there, and keep the full file for the zoom.
        stem, dot, ext = src.rpartition(".")
        thumb = f"{stem}-thumb.{ext}" if dot else ""
        if not thumb or not (PUBLIC / thumb).exists():
            thumb = src
        items.append({
            "kind": kind,
            "src": src,
            "thumb": thumb,
            "href": href,
            "alt": str(it["alt"]),
            "text": str(it["text"]) if it.get("text") else "",
        })
    return heading, note, per_page, items


PLAY_SVG = ('<svg viewBox="0 0 24 24" aria-hidden="true">'
            '<path d="M8 5v14l11-7z"/></svg>')


def render_gallery(heading, note, per_page, items):
    e = html.escape
    out = [
        "<!-- GALLERY:START — generated from gallery.yaml by scripts/build-content.py.",
        f"{I}     Edit the YAML and re-run; do not hand-edit this block. -->",
    ]
    for i, group in enumerate(chunk(items, per_page), 1):
        out.append(f'{I}<div class="page"><div class="wrap">')
        if i == 1:
            out.append(f'{I}  <h2 id="gallery-title">{e(heading)}</h2>')
            if note:
                out.append(f'{I}  <p class="note">{md(note, "gallery.yaml note")}</p>')
        if not group:
            if not note:
                out.append(f'{I}  <p class="note">No photos yet.</p>')
        else:
            out.append(f'{I}  <ul class="shots">')
            for n, it in enumerate(group, 1):
                where = f"gallery.yaml: items[{n}]"
                img = (f'<img src="{e(it["thumb"])}" alt="{e(it["alt"])}" loading="lazy">')
                badge = (f'<span class="play-badge"><span>{PLAY_SVG}</span></span>'
                         if it["kind"] == "video" else "")
                frame = f'<span class="frame">{img}{badge}</span>'
                out.append(f"{I}    <li><figure>")
                if it["href"]:
                    rel = ' target="_blank" rel="noopener"' if it["kind"] == "video" else ""
                    out.append(f'{I}      <a href="{e(it["href"])}"{rel}>{frame}</a>')
                elif it["kind"] == "image":
                    # Links to the file itself, so it still opens without
                    # scripting; the page intercepts the click for a lightbox.
                    out.append(f'{I}      <a class="shot-zoom" href="{e(it["src"])}">{frame}</a>')
                else:
                    out.append(f"{I}      {frame}")
                if it["text"]:
                    out.append(f'{I}      <figcaption>{md(it["text"], where)}</figcaption>')
                out.append(f"{I}    </figure></li>")
            out.append(f"{I}  </ul>")
        out.append(f"{I}</div></div>")
    out.append(f"{I}<!-- GALLERY:END -->")
    return "\n".join(out)


# ── page assembly ────────────────────────────────────────────────────────────

def chunk(seq, per_page):
    """Split into pages; always at least one page, even when empty."""
    if not seq:
        return [[]]
    if not per_page:
        return [list(seq)]
    return [list(seq[i:i + per_page]) for i in range(0, len(seq), per_page)]


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
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the page is out of date, without writing")
    args = ap.parse_args()

    s_heading, s_pages = load_story()
    c_heading, note, c_per, events = load_events()
    g_heading, g_note, g_per, items = load_gallery()

    page = HTML_PATH.read_text(encoding="utf-8")
    updated = page
    updated = replace_block(updated, "STORY", render_story(s_heading, s_pages))
    updated = replace_block(updated, "GIGS",
                            render_gigs(c_heading, note, c_per, events))
    updated = replace_block(updated, "GALLERY",
                            render_gallery(g_heading, g_note, g_per, items))

    if args.check:
        if updated != page:
            sys.exit("build-content: public/index.html is out of date — run without --check")
        print("build-content: up to date")
        return

    gig_pages = len(chunk(events, c_per))
    gal_pages = len(chunk(items, g_per))
    summary = (f"story {len(s_pages)} page(s), "
               f"{len(events)} event(s) over {gig_pages} page(s), "
               f"{len(items)} item(s) over {gal_pages} page(s)")

    if updated == page:
        print(f"build-content: no change ({summary})")
        return

    HTML_PATH.write_text(updated, encoding="utf-8")
    print(f"build-content: wrote {summary}")


if __name__ == "__main__":
    main()
