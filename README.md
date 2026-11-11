# Running GemLogin 6.0.0 on Linux + Wine — fix summary

**TL;DR** — The vendor only ships Windows/macOS builds. GemLogin 6.0.0
(Electron 31.7.6 / Chromium 126) *does* install and run under Wine, but two
environment differences between Windows and this Linux/Wine account break
it, both of which are fixed **in userspace, no root needed**:

| # | Symptom | Root cause | Fix |
|---|---------|-----------|-----|
| 1 | App crashes before any window appears (X `BadLength`) | Vendor ships a **3000×3000** `gemlogo.png`; Electron publishes it as the `_NET_WM_ICON` X11 property (~36 MB, over X's 256 KB limit) | Resize to **256×256** in `resources/app.asar.unpacked` (the one copy on disk Electron actually reads) |
| 2 | Window renders **solid gray** forever | App's internal UI/API server binds **port 1010**; ports **< 1024 are `EACCES`-denied** for an unprivileged Linux user, so the server never starts and the window (which loads `http://localhost:1010/`) loops on `ERR_CONNECTION_REFUSED` | Inject a hook into the Electron **main process** that redirects the bind **1010 → 49009** and rewrites every `http://localhost:1010` reference (pages, images, API, Node-side HTTP) |

The result: the real login UI renders, all images load, and the app's own
API calls succeed — verified with a live process and `curl`.

---

## 1. App architecture (what you're actually running)

- `GemLogin.exe` is the 180 MB **Electron binary with the app embedded**
  (asar integrity validation disabled; the embedded `app.asar` is what runs —
  swapping `resources/app.asar` on disk has **no effect**).
  Only the contents of `resources/app.asar.unpacked/` are read from disk.
- The **main (browser) process** starts an in-process **Express-style HTTP
  server on `http://localhost:1010`** that serves the whole web UI
  (`/assets/…`, `/images/Banner.png`, `/api/…`). The single `BrowserWindow`
  loads `http://localhost:1010/` (title "GemLogin 6.0.0", Vue app).
- `resources/standalone/` contains a *separate* standalone API (port 1011) —
  **not** what the window uses. (If it is ever started under Wine, the same
  hook redirects 1011 → 49010 automatically, see §3.)
- The UI calls the API at the **absolute** URL `http://localhost:1010/api/…`
  and references some images as **absolute** `http://localhost:1010/images/…`
  (the two broken ones: `Banner.png`, `GemLogin.png`).
- App state lives in `~/.gemlogin/` (Wine home:
  `~/.wine/drive_c/users/<you>/.gemlogin/`): `debug.log` (electron-log —
  very useful), `db.db` (SQLite), `storage.json`, `log.db`, `vpn-proxy/`.
- A single-instance guard is in effect: launching again just focuses the
  existing window.

## 2. Root causes, in detail

### 2.1 Icon crash (X `BadLength`)

Both `resources/app.asar.unpacked/resources/imgs/gemlogo.png` and
`resources/app.asar.unpacked/resources/web/assets/gemlogo-Doe0HIvu.png`
are 3000×3000. When the window is mapped, Electron sets the
`_NET_WM_ICON` atom with that bitmap; the X server rejects the oversized
property and Chromium takes the X error path — the process dies before
showing anything. Windows has no such per-atom limit, so the vendor never
hit this. (This is what originally made the app "crash on launch".)

**Fix:** downscale both to 256×256 (originals kept as `*.png.orig3000`
here / `*.orig` via `fix_icon.py`). The app's own icon rendering is
unaffected.

### 2.2 Gray window — privileged port 1010 (`EACCES`)

The window loads `http://localhost:1010/`. Under Wine the main process
tried to `listen()` on `0.0.0.0:1010` and got:

```
[hook] LISTEN attempt [1010,null]
[hook] LISTEN ERROR listen EACCES: permission denied 0.0.0.0:1010 EACCES
```

`EACCES` = "permission denied" (not `EADDRINUSE`). Port 1010 is **below
1024**, i.e. a *privileged* port. On this account (UID 1000, no sudo, no
`net_bind_service` capability) Linux refuses privileged binds to
unprivileged processes. Verified directly from the host:

| bind test | 127.0.0.1:67 | :80 | :1010 | :8080 | :54321 |
|-----------|--------------|-----|-------|-------|--------|
| host (uid 1000) | EACCES | EACCES | **EACCES** | OK | OK |

Same result from *inside* the live Electron process — so it is a
host-level policy, not a Wine quirk, and not specific to 1010: **any**
port < 1024 fails, ≥ 1024 works. On Windows the equivalent restriction
doesn't exist for regular users → vendor never noticed.

Consequence chain (proven via the app's own `~/.gemlogin/debug.log` and
CDP): `listen()` fails → no server on 1010 → window's
`http://localhost:1010/` gets `ERR_CONNECTION_REFUSED` (`-102`) forever →
splash/gray.

### 2.3 Why the redirect is userspace-only (no `setcap`/root)

- `setcap cap_net_bind_service+ep` only applies to **ELF** files;
  `GemLogin.exe` is a Windows PE. The ELF that would actually bind the
  socket is `wine` itself — setcap-ing a system file needs root (not
  available here) and would grant the privilege to *every* Wine process.
- `NODE_OPTIONS` / `--require` / `--remote-debugging-port` are all
  unavailable or unreachable in a packaged app (Node strips
  `NODE_OPTIONS` in packaged Electron apps — `node_bindings.cc(379)` —
  and the app has no supported way to pass `NODE_EXTRA…`), so the only
  reliable injection point is the **Node inspector** (`--inspect-brk`),
  which Electron always honors and which pauses at the app's
  `out/main/main.js` entry before any startup code runs.

## 3. The fix (how it works)

`scripts/gemfix_patch.js` is evaluated at the `main.js` entry breakpoint
(§5), *before* any app code runs. It is a single, self-contained IIFE
guarded by `process.env.GEMFIX`. It does four things:

1. **Redirect the server bind.** Wraps `net.Server.prototype.listen`:
   any numeric port `< 1024` is replaced with `48000 + (port - 1)`
   (1010 → **49009**; 1011 → 49010; general rule). This makes the app's
   own server start.
2. **Rewrite main-process (Node) outbound HTTP** — `http.request/get`,
   `https.request/get`, and global `fetch` — so anything the *Node* side
   dials on a privileged port goes to the redirected port.
3. **Rewrite window loads** — `BrowserWindow.loadURL/loadFile` and each
   window's `webContents.loadURL` — from `http://localhost:1010/…` to
   `http://localhost:49009/…`.
4. **Rewrite renderer-side references.** For each window it injects a
   small script into the page (`webContents.executeJavaScript`) that
   wraps `window.fetch` and `XMLHttpRequest.prototype.open`, rewrites
   `img`/`source`/`srcset`/inline `background-image`, and installs a
   `MutationObserver` so *late*-bound URLs (the two absolute `:1010`
   image URLs appear after hydration) are fixed too.

Only the **exact** prefix `http://localhost:1010` is rewritten — remote
URLs (`https://app.gemlogin.vn/…`, `https://www.svgrepo.com/…`) are left
alone (an earlier draft of the patch had a bug rewriting implicit-443
https hosts; the shipped patch matches the literal localhost prefix only).

**Why renderer-side JS instead of `session.webRequest`?**
`webRequest.onBeforeRequest` *does* fire for the image/xhr requests, but
its `redirectUrl` is **not honored** for them in this Electron build —
the request log shows the redirect, yet the `<img>` still decodes 0×0 and
`fetch('http://localhost:1010/…')` from the page still fails. The
in-page override is deterministic, so it is what the fix uses
(`webRequest` is deliberately *not* installed by the shipped patch).

**Resulting behavior** (verified live):

```
LISTEN OK {"address":"::","family":"IPv6","port":49009}
curl http://127.0.0.1:49009/            -> 200 (real HTML)
curl http://127.0.0.1:49009/api/status  -> 200
curl http://127.0.0.1:49009/images/Banner.png -> 200 image/png (407 KB)
renderer: every <img> complete=True (Banner 1914x833, GemLogin 1400x400)
```

Port mapping in use: **1010 → 49009** (main UI/API server). The generic
rule also maps 1011 → 49010 should the standalone API ever run.

## 4. File layout

```
gemlogin-fix/
├── README.md                 ← this file
├── install.sh                ← one-shot: copies scripts, fixes icon, patches desktop entry
└── scripts/
    ├── launch.sh             ← desktop "Exec": kill stale, launch --inspect-brk, inject, resume
    ├── inject.py             ← CDP driver: breakpoint at out/main/main.js:0, eval patch, resume
    ├── gemfix_patch.js       ← the actual hook (bind redirect + URL rewrites)
    ├── fix_icon.py           ← idempotent 3000→256 icon downscaler (auto-finds install)
    └── verify.sh             ← listener/API sanity check
```

Installed copies live in `~/.local/share/gemlogin-fix/` (what the desktop
entry runs).

## 5. How injection works under the hood (for the curious / debugging)

1. `launch.sh` picks a free inspector port (9241…9252 or random high port),
   launches `wine GemLogin.exe --inspect-brk=<port>`.
2. The process **pauses** at its very first statement — the entry of
   `out/main/main.js` (a `Debugger.setBreakpointByUrl` on
   `out/main/main\.js$`, line 0) — set up by `inject.py` before
   `Runtime.runIfWaitingForDebugger`.
3. `inject.py` evaluates `gemfix_patch.js` on the top call frame and
   resumes. The app then runs normally with all hooks in place; the
   inspector socket can be closed.
4. Expected `inject.py` output:
   ```
   paused frames= 16 reason= ambiguous
   patch: {"result": {"type":"string","value":"gemfix redirect ready"}}
   resumed: app running unpaused
   OK
   ```
   (`"already"` means the process was already patched — also fine.)

Gotchas that cost time (already handled by the scripts, but know them):
- **Wine prints CRLF** — the `ws://…` URL must be `tr -d '\r'`'d or the
  WS handshake fails.
- The app **renames** its Chromium children to `CrBrowserMain` /
  `CrGpuMain` / `CrRendererMain` / `network.CrUtili`. So
  `pkill -x GemLogin.exe` silently does nothing, and
  `pkill -f 'gemlogin.GemL[o]gin'` (bracket trick so *your own shell*
  doesn't match) is the reliable kill.
- **Single-instance:** if any instance survives, a new launch just
  focuses the old window and exits 0 — always verify
  `ps -eo args | grep -c '[G]emLogin.exe'` == 0 before a test.
- A *paused-then-resumed* inspector only streams `consoleAPICalled` until
  you resume; one-shot `Runtime.evaluate` always works. (Moot once
  injected — but it shaped the debugging.)

## 6. Replication — fresh machine or after reinstall

### Prerequisites
- **Wine** (a working 64-bit prefix; the prefix used here is `~/.wine`).
- The **vendor's NSIS installer** (run under `wine`; GUI or `/S` silent).
- **Python 3** with two pip packages: `websocket-client` (CDP driver) and
  `Pillow` (icon resize): `pip install --user websocket-client Pillow`.
- Coreutils/`ss`/`curl` for the sanity checks.
- A normal **unprivileged** account is fine (that's the whole point).
  If you *do* run as root / on a machine that allows privileged binds,
  the port redirect is a no-op safety net; **the icon fix is still
  required** or the app crashes on X11.

### Steps
1. **Install the app normally** with the vendor installer under Wine
   (this creates `…/AppData/Local/Programs/gemlogin/GemLogin.exe` and the
   `GemLogin.desktop` on the desktop).
2. **Run the installer** (from this folder):
   ```bash
   bash install.sh
   ```
   It: copies the scripts to `~/.local/share/gemlogin-fix/`, resizes the
   oversized icon (backs up originals as `*.orig`), and rewrites the
   desktop entry's `Exec=` to `bash ~/.local/share/gemlogin-fix/launch.sh`
   (original kept as `GemLogin.desktop.bak`).
3. **Launch** — click the desktop icon (or `bash ~/.local/share/gemlogin-fix/launch.sh`).
   Expected:
   ```
   paused frames= 16 reason= ambiguous
   patch: {"result": {"type":"string","value":"gemfix redirect ready"}}
   resumed: app running unpaused
   OK
   GemLogin: fix applied (privileged port 1010 redirected to 49009). App running.
   ```
4. **Verify** (`bash ~/.local/share/gemlogin-fix/verify.sh`):
   - `ss -tln` shows a listener on **:49009** (and **not** 1010).
   - `curl http://127.0.0.1:49009/api/status` → 200; `/images/Banner.png` → 200.
   - Window shows the **real login UI** (logo, dashboard preview image,
     form) — not flat gray.
   - `~/.gemlogin/debug.log` no longer spews `-102 ERR_CONNECTION_REFUSED`.

### After a vendor update / reinstall
Reinstalling replaces `resources/` — the 3000×3000 icon comes back.
Re-run just the icon fix (the port hook is injected at launch and is
external, so it survives):
```bash
python3 ~/.local/share/gemlogin-fix/fix_icon.py
```
The desktop entry still runs `launch.sh`, so the app keeps working; only
the icon step needs repeating (a vendor update that *adds* a new icon
asset is covered — `fix_icon.py` globs **all** `gemlogo*.png` under
`app.asar.unpacked` and resizes anything > 512px).

## 7. Troubleshooting

| Symptom | Cause / action |
|---|---|
| Launcher: "no GemLogin.exe found" | App not installed (or non-default `WINEPREFIX`). Set `WINEPREFIX` to your prefix and/or install first. |
| `could not reach the inspector (no ws:// URL in log)` | Inspect log: `cat /tmp/gemlogin_insp.log`. Usually a Wine/`wine64` resolution issue or a prefix permission problem. |
| `inject.py` exits non-zero but app still works | Look for the `[gemfix] …` lines in `/tmp/gemlogin_insp.log` and `ss -tln \| grep 49009`. Historically the driver's state machine could mis-timeout after the patch actually applied; the listener + `[gemfix]` lines are the source of truth. |
| Window gray again after a reinstall | 3000px icon restored → `python3 ~/.local/share/gemlogin-fix/fix_icon.py`; also confirm the desktop entry still points at `launch.sh`. |
| `EACCES` on *other* low ports in the app | Covered — the redirect is generic for **any** bind < 1024 (`48000 + p - 1`). |
| App opens but the *login/API* misbehaves | Check `~/.gemlogin/debug.log`. The local API is on 49009 (`verify.sh`). Remote endpoints (`app.gemlogin.vn`) are untouched by the fix and need normal internet access. |
| `db.db` is 0 bytes / `SQLITE_ERROR: no such table: profiles` in debug.log | Known cosmetic symptom of a first boot on a fresh `~/.gemlogin`; the UI and local API still come up. If the login screen never appears, delete `~/.gemlogin/db.db` and relaunch. |
| Port 49009 already taken | `launch.sh` picks the inspector port freely; if **49009** itself is occupied by something else, kill whatever holds it or change the `NEW`/`wp()` mapping consistently in `gemfix_patch.js` (it's one constant pair: `PRIV='http://localhost:1010'`, `NEW='http://localhost:49009'`). |

## 8. What was ruled out (so you don't re-litigate)

- **Preload / sandbox / bytenode** — red herring. With `--inspect-brk`
  the renderer's `bytecode-loader.cjs` loads fine (it logs the Node `vm`
  deprecation warning from *inside* the app). The gray window was never a
  preload problem.
- **`NODE_OPTIONS` / `gemfix.js`** — stripped by packaged Electron
  (`node_bindings.cc(379)`); cannot be used for injection.
- **`--remote-debugging-port`** — unreachable from the host in this
  setup; the Node **inspector** is the reliable path.
- **Editing `resources/app.asar`** — no effect (app is embedded in the
  exe; only `.unpacked` is read from disk).
- **`setcap` / root** — unavailable and would only fix the *bind*, not
  the absolute `:1010` references in the UI (images/API), which is why the
  userspace redirect is the right shape of fix.
- **`session.webRequest.onBeforeRequest` redirect** — fires but is not
  honored for these sub-resource requests in this Electron build; replaced
  by the in-page override (§3.4).

## 9. Evidence trail (short version)

1. NSIS installer ran clean (GUI and `/S`).
2. X `BadLength` on startup → traced to 3000×3000 `gemlogo.png` →
   `_NET_WM_ICON` → downscale to 256 fixed the *crash*.
3. Window appeared but **solid gray** (exactly one color: 204,204,204).
4. `~/.gemlogin/debug.log`: endless `renderer.failLoad -102
   ERR_CONNECTION_REFUSED http://localhost:1010/`; `ss -tln` empty;
   `curl :1010` = 000 → **the 1010 server never starts**.
5. CDP hooks on `net.Server.listen` at the `main.js` breakpoint:
   `LISTEN attempt [1010,null]` → `EACCES`.
6. Host + in-process bind matrix → **port < 1024 ⇒ EACCES** (uid 1000,
   no sudo, no caps) — a host policy, deterministic.
7. Port is **hardcoded** in the compiled main bundle (no env/config knob).
8. Redirect 1010→49009 + URL rewrites → `LISTEN OK :49009`, UI renders,
   all images 200, API 200. Ship.
