#!/bin/bash
# GemLogin launcher — applies the privileged-port fix (port 1010 -> 49009)
# by injecting a patch into the Electron main process over the Node inspector
# before the app's startup code runs, then leaves the app running normally.
#
# Background: on this unprivileged Linux/Wine account, ports < 1024 cannot be
# bound (EACCES). GemLogin's internal UI/API server binds port 1010, so it
# never starts and the window stays gray. The injected hook redirects that
# bind (and every http://localhost:1010 reference) to port 49009.

set -u
WINEPREFIX="${WINEPREFIX:-$HOME/.wine}"
# Auto-detect the installed GemLogin.exe (any wine user name)
EXE=""
for d in "$WINEPREFIX"/drive_c/users/*/AppData/Local/Programs/gemlogin/GemLogin.exe; do
  if [ -e "$d" ]; then EXE="$d"; break; fi
done
[ -z "$EXE" ] && { echo "GemLogin: no GemLogin.exe found under $WINEPREFIX — install the app first."; exit 1; }
FIXDIR="$HOME/.local/share/gemlogin-fix"
PATCH="$FIXDIR/gemfix_patch.js"
INJECT="$FIXDIR/inject.py"
LOG="${TMPDIR:-/tmp}/gemlogin_insp.log"

# 1) stop any running instance (Chromium renames its procs; match by exe path)
pkill -9 -f 'gemlogin\.Ge[m]Login' 2>/dev/null
sleep 2

# 2) pick a free inspector port in the user range
pick_free_port() {
  for base in 9241 9242 9243 9244 9245 9251 9252; do
    if ! ss -tln 2>/dev/null | awk '{print $4}' | grep -q ":$base\$"; then
      echo "$base"; return
    fi
  done
  # fall back to a random high port
  echo $(( 48000 + RANDOM % 2000 ))
}
PORT="$(pick_free_port)"

# 3) launch under the inspector (pauses at startup until we resume)
: > "$LOG"
nohup wine "$EXE" --inspect-brk="$PORT" > "$LOG" 2>&1 &
WINEPID=$!

# 4) wait for the inspector websocket URL (Wine prints CRLF -> strip \r)
WSURL=""
for _ in $(seq 1 45); do
  WSURL="$(grep -a 'Debugger listening' "$LOG" 2>/dev/null | grep -o 'ws://[^ ]*' | tr -d '\r' | head -1)"
  [ -n "$WSURL" ] && break
  if ! kill -0 "$WINEPID" 2>/dev/null; then break; fi
  sleep 1
done
if [ -z "$WSURL" ]; then
  echo "GemLogin: could not reach the inspector (no ws:// URL in log)."
  echo "GemLogin: the un-patched app may still be running but likely stays gray."
  exit 1
fi

# 5) inject the patch and resume
python3 "$INJECT" "$WSURL" "$PATCH" 30
RC=$?
if [ $RC -ne 0 ]; then
  echo "GemLogin: injection failed (exit $RC); app left un-patched."
  exit $RC
fi

echo "GemLogin: fix applied (privileged port 1010 redirected to 49009). App running."
exit 0
