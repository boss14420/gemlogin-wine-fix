#!/usr/bin/env python3
"""Downscale GemLogin's oversized app icon so the app can start under Wine.

WHY:
  The vendor ships a 3000x3000 gemlogo.png. Electron sends it to X11 as the
  _NET_WM_ICON property; 3000x3000 RGBA is ~36 MB, far beyond X's 256 KB
  per-property limit, so the X server rejects the request (BadLength) and
  the app crashes before its window appears. Windows has no such limit,
  which is why the vendor never noticed.

WHAT:
  Finds every gemlogo*.png under resources/app.asar.unpacked and resizes
  any that are bigger than 512px down to 256x256. The original file is
  preserved next to it as <name>.orig (created once). Idempotent: icons
  already <= 512px are left alone.

  NOTE: app.asar.unpacked is the only copy on disk we can edit — the app
  itself is embedded in GemLogin.exe, and swapping resources/app.asar has
  no effect. (Only the fuses allow Electron to read the .unpacked dir.)
"""
import os
import sys
import glob
from PIL import Image


def find_root():
    """Return the app.asar.unpacked dir for the installed GemLogin."""
    base = os.environ.get("WINEPREFIX") or os.path.expanduser("~/.wine")
    pats = [
        os.path.join(base, "drive_c", "users", "*",
                     "AppData", "Local", "Programs", "gemlogin",
                     "resources", "app.asar.unpacked"),
    ]
    for pat in pats:
        hits = glob.glob(pat)
        if hits:
            return hits[0]
    sys.exit("fix_icon: no app.asar.unpacked found under "
             + base + " — install the app first.")


def main():
    root = find_root()
    print("fix_icon: root =", root)
    files = []
    for dirpath, _, names in os.walk(root):
        for n in names:
            if n.startswith("gemlogo") and n.endswith(".png"):
                files.append(os.path.join(dirpath, n))
    if not files:
        sys.exit("fix_icon: no gemlogo*.png found under " + root)

    fixed = 0
    for p in sorted(files):
        im = Image.open(p)
        w, h = im.size
        if max(w, h) <= 512:
            print("  ok     ", p, f"({w}x{h})")
            continue
        bak = p + ".orig"
        if not os.path.exists(bak):
            os.replace(p, bak)
            print("  backed ", bak)
        big = Image.open(bak).convert("RGBA")
        side = min(big.width, big.height)
        x0 = (big.width - side) // 2
        y0 = (big.height - side) // 2
        big = big.crop((x0, y0, x0 + side, y0 + side))
        big = big.resize((256, 256), Image.LANCZOS)
        big.save(p)
        fixed += 1
        print(f"  fixed  {p} -> 256x256 (was {w}x{h})")
    print(f"fix_icon: {fixed} icon(s) resized")


if __name__ == "__main__":
    main()
