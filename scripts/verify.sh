#!/bin/bash
# Quick health check for the running GemLogin fix.
echo "procs: $(ps -eo args | grep -c '[G]emLogin.exe')"
echo "listeners:"
ss -tln | grep -E ':(49009|49010|1010|1011)\b' || echo "  (none)"
code=$(curl -s -m 4 -o /dev/null -w '%{http_code}' http://127.0.0.1:49009/api/status)
echo "api/status -> $code"
code=$(curl -s -m 4 -o /dev/null -w '%{http_code}' http://127.0.0.1:49009/images/Banner.png)
echo "images/Banner.png -> $code"
case "$code" in 2*) : ;; *) echo "  ^ expected 200 — if 000, the app is not patched/running";; esac
