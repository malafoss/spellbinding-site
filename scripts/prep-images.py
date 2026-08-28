#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow"]
# ///
"""Turn photos in photos-src/ into web-sized versions plus thumbnails.

    ./scripts/prep-images.py                       # anything new or changed
    ./scripts/prep-images.py photos-src/a.jpg …    # just these files
    ./scripts/prep-images.py --force               # redo everything
    ./scripts/prep-images.py --check               # report staleness, write nothing

For each photos-src/NAME.(jpg|jpeg|png|webp) it writes two files:

    public/photos/NAME.jpg          the full version the lightbox opens
    public/photos/NAME-thumb.jpg    the small square the gallery tile shows

gallery.yaml only ever names the full version — build-content.py picks up the
matching -thumb automatically for the tile.

Sources stay outside public/, so the originals are never served: only the two
derivatives are. Metadata (EXIF, IPTC) is dropped from both, since photos often
carry camera and location details that need not be published.

A full version is only re-encoded when it actually needs resizing. These files
arrived at JPEG quality 72, and re-encoding an already-compressed JPEG at a
higher quality makes it bigger without recovering anything, so when no resize
is needed the original coefficients are copied through untouched and only the
metadata is stripped.
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "photos-src"
OUT = ROOT / "public" / "photos"

SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

FULL_MAX = 1600          # long side of the version the lightbox opens
FULL_QUALITY = 82
THUMB_SIZE = 600         # square, covers a ~200px tile at 3x
THUMB_QUALITY = 78


def die(msg):
    sys.exit(f"prep-images: {msg}")


def human(n):
    return f"{n / 1024:.0f} kB" if n < 1024 * 1024 else f"{n / 1048576:.2f} MB"


def sources(only=None):
    if only:
        found = []
        for name in only:
            p = Path(name)
            if not p.is_absolute():
                p = ROOT / p
            if not p.is_file():
                die(f"{name} is not a file")
            if p.suffix.lower() not in SUFFIXES:
                die(f"{name} is not one of {', '.join(SUFFIXES)}")
            found.append(p)
        return found
    if not SRC.is_dir():
        die(f"{SRC.relative_to(ROOT)}/ not found — put the original photos there")
    found = sorted(p for p in SRC.iterdir()
                   if p.is_file() and p.suffix.lower() in SUFFIXES)
    if not found:
        die(f"no images in {SRC.relative_to(ROOT)}/ "
            f"(looking for {', '.join(SUFFIXES)})")
    return found


def stale(src, *outs):
    return any(not o.exists() or o.stat().st_mtime < src.stat().st_mtime
               for o in outs)


def save_jpeg(im, path, quality):
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    # No exif= argument, so metadata is not carried over.
    im.save(path, "JPEG", quality=quality, optimize=True, progressive=True)


def write_full(src, dest):
    """Resize if oversized; otherwise copy the pixels through untouched."""
    with Image.open(src) as im:
        w, h = im.size
        is_jpeg = src.suffix.lower() in (".jpg", ".jpeg")
        # Only transpose when a rotation flag actually asks for it: the
        # transposed result is a new image without the source's quantization
        # tables, and quality="keep" needs those to copy coefficients across.
        rotated = im.getexif().get(0x0112, 1) not in (1, None)

        if is_jpeg and not rotated and max(w, h) <= FULL_MAX:
            try:
                im.save(dest, "JPEG", quality="keep", optimize=True,
                        progressive=True)
                return (w, h), "stripped"
            except (ValueError, OSError):
                pass                          # fall through to a re-encode

        work = ImageOps.exif_transpose(im) if rotated else im
        if max(work.size) > FULL_MAX:
            work = work.copy()
            work.thumbnail((FULL_MAX, FULL_MAX), Image.LANCZOS)
        save_jpeg(work, dest, FULL_QUALITY)
        return work.size, "re-encoded"


def write_thumb(src, dest):
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        # Centre-crop to a square, matching the tile's 1:1 object-fit: cover.
        im = ImageOps.fit(im, (THUMB_SIZE, THUMB_SIZE), Image.LANCZOS,
                          centering=(0.5, 0.5))
        save_jpeg(im, dest, THUMB_QUALITY)
        return im.size


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("sources", nargs="*", metavar="FILE",
                    help="specific source images; default is all of photos-src/")
    ap.add_argument("--force", action="store_true", help="rebuild every output")
    ap.add_argument("--check", action="store_true",
                    help="report missing or stale outputs and exit 1, writing nothing")
    args = ap.parse_args()

    files = sources(args.sources)
    # Naming a file means "do this one", so do not skip it as unchanged.
    if args.sources:
        args.force = True
    OUT.mkdir(parents=True, exist_ok=True)

    if args.check:
        pending = [s.name for s in files
                   if stale(s, OUT / f"{s.stem}.jpg", OUT / f"{s.stem}-thumb.jpg")]
        if pending:
            sys.exit("prep-images: out of date — " + ", ".join(pending))
        print(f"prep-images: up to date ({len(files)} photo(s))")
        return

    src_total = full_total = thumb_total = 0
    done = skipped = 0
    # When make hands us specific files it calls us once per photo, so the
    # header and the totals would repeat for every one. Just the row then.
    terse = bool(args.sources)
    if not terse:
        print(f"{'source':<16}{'in':>9}   {'full':<30}{'thumb':<18}")
    for s in files:
        full = OUT / f"{s.stem}.jpg"
        thumb = OUT / f"{s.stem}-thumb.jpg"
        s_bytes = s.stat().st_size
        src_total += s_bytes

        if not args.force and not stale(s, full, thumb):
            full_total += full.stat().st_size
            thumb_total += thumb.stat().st_size
            skipped += 1
            print(f"{s.name:<16}{human(s_bytes):>9}   {'unchanged':<30}")
            continue

        (fw, fh), how = write_full(s, full)
        tw, th = write_thumb(s, thumb)
        full_total += full.stat().st_size
        thumb_total += thumb.stat().st_size
        done += 1
        print(f"{s.name:<16}{human(s_bytes):>9}   "
              f"{f'{fw}x{fh}  {human(full.stat().st_size)}  {how}':<30}"
              f"{f'{tw}x{th}  {human(thumb.stat().st_size)}':<18}")

    if terse:
        return
    print()
    print(f"prep-images: {done} written, {skipped} unchanged")
    print(f"  sources     {human(src_total)}  (not served)")
    print(f"  full        {human(full_total)}  (opened by the lightbox)")
    print(f"  thumbnails  {human(thumb_total)}  (what the gallery page loads)")
    if src_total:
        print(f"  the gallery grid now costs {thumb_total / src_total:.0%} "
              f"of what serving the originals did")


if __name__ == "__main__":
    main()
