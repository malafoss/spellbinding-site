#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml"]
# ///
"""Render calendar.yaml into the gig list in public/index.html.

Everything between the GIGS:START and GIGS:END markers is replaced. Run after
editing calendar.yaml:

    ./scripts/build-calendar.py

The shebang runs it through uv, which resolves pyyaml from the inline metadata
below, so no virtualenv setup is needed. `uv run scripts/build-calendar.py` and
`python3 scripts/build-calendar.py` (with pyyaml installed) both work too.

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
YAML_PATH = ROOT / "calendar.yaml"
HTML_PATH = ROOT / "public" / "index.html"

START = "<!-- GIGS:START"
END = "<!-- GIGS:END -->"
INDENT = " " * 6

INSTAGRAM = "https://www.instagram.com/spellbinding.band/"


def die(msg):
    sys.exit(f"build-calendar: {msg}")


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


def load_events():
    if not YAML_PATH.exists():
        die(f"{YAML_PATH.name} not found")
    try:
        data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        die(f"{YAML_PATH.name} is not valid YAML:\n{e}")
    if not isinstance(data, dict):
        die(f"{YAML_PATH.name} must be a mapping with 'note' and 'events' keys")

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


def render(note, events):
    e = html.escape
    out = [
        f"{START} — generated from calendar.yaml by scripts/build-calendar.py.",
        f"{INDENT}     Edit the YAML and re-run; do not hand-edit this block. -->",
    ]

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

    out.append(f"{INDENT}{END}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the page is out of date, without writing",
    )
    args = ap.parse_args()

    note, events = load_events()
    block = render(note, events)

    page = HTML_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(START) + r".*?" + re.escape(END), re.S
    )
    if not pattern.search(page):
        die(
            f"markers not found in {HTML_PATH.name}. Expected a block delimited "
            f"by '{START} ... -->' and '{END}'"
        )

    updated = pattern.sub(lambda _: block, page, count=1)

    if args.check:
        if updated != page:
            sys.exit("build-calendar: public/index.html is out of date — run without --check")
        print("build-calendar: up to date")
        return

    if updated == page:
        print(f"build-calendar: no change ({len(events)} event(s))")
        return

    HTML_PATH.write_text(updated, encoding="utf-8")
    if events:
        span = f"{events[0]['date']} … {events[-1]['date']}"
        print(f"build-calendar: wrote {len(events)} event(s) ({span})")
    else:
        print("build-calendar: wrote the 'no dates announced yet' state")


if __name__ == "__main__":
    main()
