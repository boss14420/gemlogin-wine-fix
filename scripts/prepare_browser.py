#!/usr/bin/env python3
"""Pre-download + install a GemLogin browser build by version.

WHY:
  Opening a profile makes the app download its bundled Windows Chrome from
  the vendor S3 bucket (~200 MB, e.g. .../chrome_win/150.zip). The app's own
  fetch aborts after a hard wall-clock timeout that is too short for a
  ~200 MB transfer on a slow link -> "Open profile failed: Browser N:
  Download of N.zip failed: Request timed out".

  This script does the same job OUTSIDE the app (no size-blind timer) and
  drops the already-extracted browser where the app checks for it:
  <state>/browser/<version>/Chrome-bin/chrome.exe. Once populated, the app
  SKIPS its own download and spawns the local chrome.exe.

HOW:
  The authoritative per-version URL comes from the vendor API the app
  itself uses:  https://app.gemlogin.vn/api/browser_versions
  (entries: {version, url, type}  -- type 0 = Windows, 1 = macOS).
  The vendor zip's top-level dir is "Chrome-bin/", so extracting the zip
  into browser/<version>/ yields exactly browser/<version>/Chrome-bin/...

USAGE:
  python3 prepare_browser.py 150            # one version
  python3 prepare_browser.py 141 150 152     # several
  python3 prepare_browser.py all            # every Windows (type 0) version
  python3 prepare_browser.py 150 --force     # re-download even if present
  python3 prepare_browser.py 150 --list      # just print version -> url

Finds the app state dir automatically (Wine prefix), or set GEM_HOME
(e.g. GEM_HOME=.../.gemlogin).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

API = "https://app.gemlogin.vn/api/browser_versions"
UA = {"User-Agent": "gemlogin-fix/1.0"}


def die(msg):
    sys.exit("prepare_browser: " + msg)


def find_state_dir():
    if os.environ.get("GEM_HOME"):
        return os.path.expanduser(os.environ["GEM_HOME"])
    cands = []
    home = os.path.expanduser("~")
    wine = os.environ.get("WINEPREFIX") or os.path.join(home, ".wine")
    pat = os.path.join(wine, "drive_c", "users", "*", ".gemlogin")
    import glob
    hits = glob.glob(pat)
    cands += sorted(hits)
    cands.append(os.path.join(home, ".gemlogin"))
    for c in cands:
        if os.path.isdir(c):
            return c
    die("no .gemlogin state dir found (Wine prefix? install the app first)")


def fetch_versions():
    """Return {version: url} for Windows (type 0) builds."""
    req = urllib.request.Request(API, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    rows = data.get("data", data if isinstance(data, list) else [])
    out = {}
    for row in rows:
        if str(row.get("type", 0)) == "0" and row.get("url"):
            out[str(row.get("version"))] = row["url"]
    return out


def head_size(url):
    req = urllib.request.Request(url, headers=dict(UA, Method="HEAD"))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            v = r.headers.get("Content-Length")
            return int(v) if v else None
    except Exception:
        return None


def download(url, dest):
    """Download to dest, preferring curl (progress + resume)."""
    if shutil.which("curl"):
        # --retry-connrefused + generous timeouts; no artificial cap.
        cmd = ["curl", "-fsSL", "--retry", "5", "--retry-connrefused",
               "-C", "-", "-o", dest, url]
        subprocess.run(cmd, check=True)
    else:
        print("  (curl not found; plain urllib download, no progress bar)")
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=60) as r, \
                open(dest, "wb") as f:
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    if os.path.getsize(dest) < 1 * 1024 * 1024:
        die(f"downloaded file suspiciously small: {dest}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if "--help" in flags or "-h" in args or (not args and not flags):
        print(__doc__.strip())
        return

    state = find_state_dir()
    browsers = os.path.join(state, "browser")
    versions = fetch_versions()

    if "--list" in flags and not args:
        for v in sorted(versions, key=lambda x: int(x) if x.isdigit() else 0):
            print(f"{v:>5}  {versions[v]}")
        return

    if "all" in args:
        targets = sorted(versions, key=lambda x: int(x) if x.isdigit() else 0)
    else:
        unknown = [v for v in args if v not in versions]
        if unknown:
            known = ", ".join(sorted(versions, key=lambda x: int(x) if x.isdigit() else 0))
            die(f"version(s) not in vendor API: {', '.join(unknown)}\n"
                f"known Windows versions: {known}")
        targets = args

    print(f"state dir: {state}")
    print(f"targets:   {', '.join(targets)}")

    for v in targets:
        url = versions[v]
        dest_dir = os.path.join(browsers, v)
        marker = os.path.join(dest_dir, "Chrome-bin", "chrome.exe")
        if os.path.exists(marker) and "--force" not in flags:
            print(f"[{v}] already present ({marker}) -- skip "
                  f"(use --force to re-download)")
            continue
        if os.path.exists(dest_dir):
            print(f"[{v}] removing existing {dest_dir}")
            shutil.rmtree(dest_dir)

        size = head_size(url)
        print(f"[{v}] download {url}" +
              (f"  ({size/1048576:.0f} MB)" if size else ""))
        zip_path = os.path.join(state, f".browser-{v}.zip.part")
        try:
            download(url, zip_path)
            if size and os.path.getsize(zip_path) != size:
                die(f"[{v}] size mismatch: got {os.path.getsize(zip_path)}, "
                    f"expected {size}")
            print(f"[{v}] extracting to {dest_dir}")
            os.makedirs(dest_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(dest_dir)
        finally:
            try:
                os.remove(zip_path)
            except OSError:
                pass

        if os.path.exists(marker):
            du = subprocess.run(["du", "-sh", dest_dir], capture_output=True, text=True)
            print(f"[{v}] OK  {marker}  ({du.stdout.split()[0] if du.stdout else '?'} total)")
        else:
            die(f"[{v}] extraction finished but {marker} is missing -- "
                f"unexpected zip layout?")

    print("done. Profiles using these browser versions will open without "
          "downloading.")


if __name__ == "__main__":
    main()
