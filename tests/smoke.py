#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.9"
# dependencies = ["websocket-client"]
# ///
"""Smoke tests for the site.

    make test              or      ./tests/smoke.py
    ./tests/smoke.py -v    show every check, not just failures

Every assertion here corresponds to something that has actually broken in this
codebase. It is not coverage for its own sake: each one is a bug that shipped or
nearly shipped, and the comment says which.

Static checks run first and need nothing but Python. The rest drive real headless
Chrome over the DevTools Protocol, because the failures worth catching here are
geometry and timing, which nothing else observes.
"""

import argparse
import http.server
import json
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
PAGE = PUBLIC / "index.html"

VERBOSE = False
FAILURES = []
CHECKS = [0]


def check(ok, name, detail=""):
    CHECKS[0] += 1
    if ok:
        if VERBOSE:
            print(f"  pass  {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAILURES.append(f"{name}: {detail}" if detail else name)
        print(f"  FAIL  {name}" + (f"  — {detail}" if detail else ""))
    return ok


def section(title):
    print(f"\n{title}")


# ── static checks ────────────────────────────────────────────────────────────

def rel_luminance(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    a, b = sorted((rel_luminance(fg), rel_luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def static_checks(html):
    section("static")

    # HTML has to parse. A stray tag from a scripted edit is easy to ship.
    import html.parser as hp
    try:
        hp.HTMLParser().feed(html)
        check(True, "html parses")
    except Exception as e:                              # noqa: BLE001
        check(False, "html parses", str(e))

    # The inline script is the whole site; a syntax error is a blank page.
    try:
        js = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
        proc = subprocess.run(["node", "--check", "-"], input=js, text=True,
                              capture_output=True)
        check(proc.returncode == 0, "inline javascript parses",
              proc.stderr.strip().splitlines()[-1] if proc.returncode else "")
    except FileNotFoundError:
        print("  skip  inline javascript parses (node not installed)")

    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    if m:
        try:
            json.loads(m.group(1))
            check(True, "json-ld is valid json")
        except Exception as e:                          # noqa: BLE001
            check(False, "json-ld is valid json", str(e))
    else:
        check(False, "json-ld is valid json", "no ld+json block found")

    # Exactly one h1, and it comes before any h2.
    body = html[html.index("<body>"):]
    heads = re.findall(r"<(h[1-6])\b", body)
    check(heads.count("h1") == 1 and heads[:1] == ["h1"],
          "one h1, first in the outline", f"order={heads}")

    # Share previews are a bare text row without this, and crawlers do not
    # resolve relative URLs.
    og = dict(re.findall(r'<meta property="(og:[\w:]+)"\s+content="([^"]*)"', html))
    check("og:image" in og and og["og:image"].startswith("https://"),
          "og:image present and absolute", og.get("og:image", "missing"))

    # Contrast: body text sits on a near-black page over a moving animation.
    BG = (10, 10, 10)
    worst = None
    for mm in re.finditer(r"([#.][\w-][^{}]*?)\{([^}]*?)color:\s*rgba\((\d+),(\d+),(\d+),([\d.]+)\)",
                          html):
        r, g, b, a = (int(mm.group(3)), int(mm.group(4)), int(mm.group(5)),
                      float(mm.group(6)))
        eff = tuple(round(c * a + bg * (1 - a)) for c, bg in zip((r, g, b), BG))
        cr = contrast(eff, BG)
        sel = " ".join(mm.group(1).split())
        if worst is None or cr < worst[0]:
            worst = (cr, sel)
    check(worst and worst[0] >= 4.5, "all text meets WCAG AA (4.5:1)",
          f"worst {worst[0]:.2f}:1 on {worst[1]}" if worst else "no colours found")

    # Both URL-bar jumps came from a dynamic length above the fold.
    css = html[html.index("<style>"):html.index("</style>")]
    # Comments explain *why* dvh is avoided and would match a naive search.
    css_code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    check("dvh" not in css_code, "no dvh in the stylesheet",
          "dvh follows a mobile URL bar and shifts the layout mid-scroll")
    brand = re.search(r"#brand\s*\{([^}]*)\}", css_code)
    check(bool(brand) and not re.search(r"top:\s*\d+%", brand.group(1)),
          "#brand is not positioned by a percentage",
          "a % on a fixed element tracks the live viewport")

    # #tabs must not take its content height, or the second screenful overflows.
    tabs = re.search(r"#tabs\s*\{([^}]*)\}", css_code)
    check(bool(tabs) and "flex: 1 1 0" in tabs.group(1) and "min-height: 0" in tabs.group(1),
          "#tabs has flex: 1 1 0 and min-height: 0")

    # An author display defeats the hidden attribute.
    for sel in re.findall(r"([#.][\w-]+)\s*\{[^}]*display:\s*(?:flex|grid|block)", css_code):
        if re.search(re.escape(sel) + r"\[hidden\]", css):
            continue
        if re.search(r'id="' + sel.lstrip("#") + r'"[^>]*\shidden', html):
            check(False, f"{sel} sets display and is used with [hidden]",
                  "needs an explicit [hidden] { display: none }")

    # Generated blocks must match the YAML.
    for script, label in ((ROOT / "scripts/build-content.py", "content"),
                          (ROOT / "scripts/prep-images.py", "photos")):
        proc = subprocess.run([str(script), "--check"],
                              capture_output=True, text=True, cwd=ROOT)
        check(proc.returncode == 0, f"generated {label} is up to date",
              (proc.stdout + proc.stderr).strip()[:120])


# ── browser harness ──────────────────────────────────────────────────────────

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(PUBLIC), **kw)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Browser:
    def __init__(self):
        self.port = free_port()
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Quiet)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.dev = free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        self.proc = subprocess.Popen(
            ["google-chrome", "--headless=new", "--disable-gpu", "--hide-scrollbars",
             f"--remote-debugging-port={self.dev}", "--remote-allow-origins=*",
             "--window-size=1000,800", "--force-device-scale-factor=1", self.url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws = None
        for _ in range(60):
            time.sleep(0.5)
            try:
                tabs = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{self.dev}/json"))
                page = next(t for t in tabs if t["type"] == "page"
                            and f":{self.port}/" in t.get("url", ""))
                self.ws = websocket.create_connection(page["webSocketDebuggerUrl"],
                                                      timeout=30)
                break
            except Exception:                           # noqa: BLE001
                continue
        if not self.ws:
            raise RuntimeError("could not attach to headless Chrome")
        self.n = 0
        time.sleep(2.5)                                 # let the page settle

    def cmd(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                return msg.get("result", {})

    def ev(self, expr):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True)
        return r.get("result", {}).get("value")

    def viewport(self, w, h, mobile=False):
        self.cmd("Emulation.setDeviceMetricsOverride", width=w, height=h,
                 deviceScaleFactor=1, mobile=mobile)
        time.sleep(0.7)
        self.ev("window.dispatchEvent(new Event('resize'))")
        time.sleep(0.7)

    def reload(self):
        self.cmd("Page.reload", ignoreCache=False)
        time.sleep(3.0)

    def click(self, selector):
        pos = self.ev(
            "(()=>{const e=document.querySelector(%s); if(!e) return null;"
            "const b=e.getBoundingClientRect();"
            "return [Math.round(b.x+b.width/2),Math.round(b.y+b.height/2)].join(',')})()"
            % json.dumps(selector))
        if not pos:
            return False
        x, y = (int(v) for v in pos.split(","))
        self.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, buttons=0)
        self.cmd("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y,
                 button="left", buttons=1, clickCount=1)
        self.cmd("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y,
                 button="left", buttons=0, clickCount=1)
        return True

    def key(self, name, code):
        for t in ("keyDown", "keyUp"):
            self.cmd("Input.dispatchKeyEvent", type=t, key=name, code=name,
                     windowsVirtualKeyCode=code)

    def wheel(self, x, y, dy):
        self.cmd("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y,
                 deltaX=0, deltaY=dy)

    def close(self):
        try:
            self.ws.close()
        except Exception:                               # noqa: BLE001
            pass
        self.proc.terminate()
        self.srv.shutdown()


# Measured against the CSS viewport, never window.innerHeight: under mobile
# emulation innerHeight reported 870 where every CSS unit resolved to 844.
CSS_VH = ("(()=>{const p=document.createElement('div');"
          "p.style.cssText='position:absolute;visibility:hidden;height:100svh';"
          "document.body.appendChild(p);const h=p.getBoundingClientRect().height;"
          "p.remove();return h||window.innerHeight;})()")
ACTIVE_LABEL = ("(()=>{const l=document.querySelector('.tab-link[aria-current=\"true\"]');"
                "return l?l.textContent.trim():null})()")
PANEL_ON_SCREEN = ("(()=>{const s=document.getElementById('tabs');"
                   "const i=Math.round(s.scrollLeft/Math.max(1,s.clientWidth));"
                   "const t=[...s.querySelectorAll('.tab')][i];return t?t.id:null})()")

SIZES = [(1200, 900, False), (1000, 800, False), (390, 844, True),
         (360, 640, True), (320, 480, True), (700, 300, False)]


def initial_checks(b):
    """Must run first, before anything resizes or scrolls.

    The regression that shipped was a missing initial goToTab: the strip sat at
    Story while the markup marked Live as current. A resize handler re-centres
    the strip, so any viewport change heals it — checking this after the
    geometry sweep passed happily with the bug present.
    """
    section("first paint — before any resize can mask a bad initial state")
    panel, label = b.ev(PANEL_ON_SCREEN), b.ev(ACTIVE_LABEL)
    check(panel == "tab-live", "on load the strip is on the Live panel",
          f"panel={panel}")
    check(label == "Live", "on load the Live label is the one highlighted",
          f"label={label}")
    check(panel and label and panel.replace("tab-", "").lower() == label.lower(),
          "on load the panel and the highlight agree",
          f"panel={panel} label={label}")


def geometry_checks(b):
    section("geometry — two screenfuls, three panels, tab bar at the top")
    for w, h, mob in SIZES:
        b.viewport(w, h, mob)
        b.ev("window.scrollTo(0,0)")
        time.sleep(0.3)
        tag = f"{w}x{h}"
        vh = b.ev(CSS_VH)
        doc = b.ev("document.documentElement.scrollHeight")
        hero = b.ev("Math.round(document.getElementById('hero').getBoundingClientRect().height)")
        s2 = b.ev("Math.round(document.getElementById('screen-two').getBoundingClientRect().height)")
        strip = (b.ev("document.getElementById('tabs').scrollWidth")
                 / max(1, b.ev("document.getElementById('tabs').clientWidth")))
        # The document is exactly two screenfuls (svh + trailing spacer).
        check(abs(doc / vh - 2) < 0.03, f"[{tag}] document is two screenfuls",
              f"{doc/vh:.2f}")
        check(abs(hero - vh) < 2 and abs(s2 - vh) < 2,
              f"[{tag}] hero and screen-two are one viewport each",
              f"hero={hero} screen-two={s2} vh={round(vh)}")
        check(abs(strip - 3) < 0.03, f"[{tag}] strip is three screens wide",
              f"{strip:.2f}")
        check(not b.ev("document.documentElement.scrollWidth>window.innerWidth"),
              f"[{tag}] no horizontal page overflow")
        # The brand block must never grow into the scroll cue.
        gap = b.ev("document.getElementById('scroll-cue').getBoundingClientRect().top"
                   " - document.getElementById('brand').getBoundingClientRect().bottom")
        check(gap is not None and gap >= 0, f"[{tag}] logo clears the scroll cue",
              f"{gap:.1f}px" if gap is not None else "not measurable")
        # Scrolling to the end lands the tab bar flush with the top.
        b.ev("window.scrollTo(0,document.documentElement.scrollHeight)")
        time.sleep(0.5)
        top = b.ev("document.getElementById('tabbar').getBoundingClientRect().top")
        check(abs(top) < 2, f"[{tag}] tab bar is flush at the top at max scroll",
              f"{top:.1f}px")
        b.ev("window.scrollTo(0,0)")
        time.sleep(0.2)


def tab_checks(b):
    section("tabs — navigation by label, arrow key and page arrow")
    b.viewport(1000, 800)
    # Reload, so this starts from a genuine first paint at this size rather than
    # whatever the geometry sweep left behind.
    b.reload()
    check(b.ev(PANEL_ON_SCREEN) == "tab-live" and b.ev(ACTIVE_LABEL) == "Live",
          "after a reload it is still on Live",
          f"panel={b.ev(PANEL_ON_SCREEN)} label={b.ev(ACTIVE_LABEL)}")
    b.ev("window.scrollTo(0,document.documentElement.scrollHeight)")
    time.sleep(0.6)

    for label, sel, want in (("Story", ".tab-link[data-tab='story']", "tab-story"),
                             ("Gallery", ".tab-link[data-tab='gallery']", "tab-gallery"),
                             ("Live", ".tab-link[data-tab='live']", "tab-live")):
        b.click(sel)
        time.sleep(1.5)
        check(b.ev(PANEL_ON_SCREEN) == want and b.ev(ACTIVE_LABEL) == label,
              f"clicking {label} shows {want}",
              f"panel={b.ev(PANEL_ON_SCREEN)} label={b.ev(ACTIVE_LABEL)}")

    # Arrow keys, including surviving the ends where a focused button gets
    # disabled and would otherwise drop focus to the body.
    b.ev("document.getElementById('tab-next').focus()")
    seq = [("ArrowRight", 39, "tab-gallery"), ("ArrowRight", 39, "tab-gallery"),
           ("ArrowLeft", 37, "tab-live"), ("ArrowLeft", 37, "tab-story"),
           ("ArrowLeft", 37, "tab-story"), ("ArrowRight", 39, "tab-live")]
    for name, code, want in seq:
        b.key(name, code)
        time.sleep(1.2)
        check(b.ev(PANEL_ON_SCREEN) == want, f"{name} -> {want}",
              f"got {b.ev(PANEL_ON_SCREEN)}")

    section("pages — arrows only where they lead somewhere")
    bad = b.ev("""[...document.querySelectorAll('.tab')].filter(t=>{
      const p=[...t.querySelectorAll('.page')];
      if(p.length<2) return t.querySelectorAll('.page-arrow').length!==0;
      return !!p[0].querySelector('.page-arrow.up')
          || !!p[p.length-1].querySelector('.page-arrow.down')
          || !p[0].querySelector('.page-arrow.down');}).map(t=>t.id)""")
    check(bad == [], "page arrows are correct on every panel", f"wrong on {bad}")

    # Paging must not engage until the page itself has reached the bottom.
    multi = b.ev("""(()=>{const t=[...document.querySelectorAll('.tab')]
      .find(t=>t.querySelectorAll('.page').length>1); return t?t.id:null})()""")
    if multi:
        b.click(".tab-link[data-tab='%s']" % multi.replace("tab-", ""))
        time.sleep(1.4)
        maxs = b.ev("document.documentElement.scrollHeight-window.innerHeight")
        b.ev(f"window.scrollTo(0,{maxs // 2})")
        time.sleep(0.5)
        for _ in range(6):
            b.wheel(500, 650, 150)
            time.sleep(0.2)
        check(b.ev(f"document.getElementById('{multi}').scrollTop") == 0
              and b.ev("window.scrollY") >= maxs - 2,
              "scrolling reaches the page bottom before paging starts",
              f"tabScrollTop={b.ev(f'document.getElementById({multi!r}).scrollTop')} "
              f"windowY={b.ev('window.scrollY')} max={maxs}")
        b.click(f"#{multi} .page-arrow.down")
        time.sleep(1.5)
        check(b.ev(f"document.getElementById('{multi}').scrollTop") > 10,
              "the page-down arrow turns the page",
              f"scrollTop={b.ev(f'document.getElementById({multi!r}).scrollTop')}")


def audio_checks(b):
    section("audio — only the button, and volume follows the scroll")
    b.viewport(1000, 800)
    b.ev("window.scrollTo(0,0)")
    time.sleep(0.4)
    fetched = ("performance.getEntriesByType('resource')"
               ".some(r=>r.name.includes('spellcasting'))")
    check(not b.ev(fetched), "audio is not fetched before any interaction")

    # Nothing but the button may start playback.
    b.click("#hero")
    time.sleep(0.8)
    for _ in range(4):
        b.wheel(500, 400, 150)
        time.sleep(0.15)
    b.key("PageDown", 34)
    time.sleep(1.0)
    check(not b.ev(fetched), "clicking, wheeling and PageDown do not start audio")

    b.ev("window.scrollTo(0,0)")
    time.sleep(0.4)
    b.click("#play-toggle")
    time.sleep(2.5)
    playing = b.ev("document.getElementById('play-toggle').dataset.playing")
    if not check(playing == "true", "the play button starts playback",
                 f"data-playing={playing}"):
        return

    maxs = b.ev("document.documentElement.scrollHeight-window.innerHeight")
    b.ev("window.scrollTo(0,%d)" % maxs)
    time.sleep(2.4)
    check(b.ev("document.getElementById('play-toggle').dataset.playing") == "true",
          "playback survives scrolling to the bottom")

    # Pause fades out rather than cutting, and the icon flips at once.
    b.click("#play-toggle")
    time.sleep(0.15)
    check(b.ev("document.getElementById('play-toggle').dataset.playing") == "false",
          "the icon flips the moment pause is pressed")
    time.sleep(1.2)
    check(b.ev("document.getElementById('play-toggle').dataset.playing") == "false",
          "playback is stopped after the fade")


def lightbox_checks(b):
    section("lightbox — opens the full photo, closes three ways")
    b.viewport(1000, 800)
    b.ev("window.scrollTo(0,document.documentElement.scrollHeight)")
    time.sleep(0.6)
    b.click(".tab-link[data-tab='gallery']")
    time.sleep(2.2)
    n = b.ev("document.querySelectorAll('.shots img').length")
    if not n:
        print("  skip  lightbox (no gallery items configured)")
        return
    check(b.ev("[...document.querySelectorAll('.shots img')]"
               ".every(i=>i.complete&&i.naturalWidth>0)"),
          "every gallery thumbnail loads", f"{n} item(s)")
    # Tiles show the thumbnail and link to the full file.
    check(b.ev("document.querySelector('.shots img').getAttribute('src')").endswith("-thumb.jpg"),
          "tiles use the generated thumbnail")

    b.click("a.shot-zoom")
    time.sleep(1.6)
    check(not b.ev("document.getElementById('lightbox').hidden"),
          "clicking a tile opens the lightbox")
    check(b.ev("document.getElementById('lightbox-img').naturalWidth") > 800,
          "the lightbox shows the full-size image",
          f"naturalWidth={b.ev('document.getElementById(\"lightbox-img\").naturalWidth')}")
    check(b.ev("document.activeElement.id") == "lightbox-close",
          "focus moves to the close button")
    check(b.ev("document.body.classList.contains('zoomed')"),
          "the page content is hidden while zoomed")

    b.click("#lightbox-close")
    time.sleep(0.8)
    check(b.ev("document.getElementById('lightbox').hidden"), "the X closes it")
    check(b.ev("document.activeElement.className").find("shot-zoom") >= 0,
          "focus returns to the tile",
          f"focus={b.ev('document.activeElement.className')}")

    b.click("a.shot-zoom")
    time.sleep(1.2)
    b.key("Escape", 27)
    time.sleep(0.6)
    check(b.ev("document.getElementById('lightbox').hidden"), "Escape closes it")

    b.click("a.shot-zoom")
    time.sleep(1.2)
    b.cmd("Input.dispatchMouseEvent", type="mousePressed", x=25, y=25,
          button="left", buttons=1, clickCount=1)
    b.cmd("Input.dispatchMouseEvent", type="mouseReleased", x=25, y=25,
          button="left", buttons=0, clickCount=1)
    time.sleep(0.6)
    check(b.ev("document.getElementById('lightbox').hidden"),
          "a click on the backdrop closes it")


def main():
    global VERBOSE
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print passing checks too")
    args = ap.parse_args()
    VERBOSE = args.verbose

    started = time.time()
    static_checks(PAGE.read_text(encoding="utf-8"))

    b = None
    try:
        b = Browser()
        initial_checks(b)          # before any resize, see the docstring
        geometry_checks(b)
        tab_checks(b)
        audio_checks(b)
        lightbox_checks(b)
    except Exception as e:                              # noqa: BLE001
        check(False, "browser checks ran", f"{type(e).__name__}: {e}")
    finally:
        if b:
            b.close()

    took = time.time() - started
    print()
    if FAILURES:
        print(f"{len(FAILURES)} of {CHECKS[0]} checks failed in {took:.0f}s:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print(f"all {CHECKS[0]} checks passed in {took:.0f}s")


if __name__ == "__main__":
    main()
