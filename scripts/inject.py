#!/usr/bin/env python3
"""Lean CDP injector: attach to GemLogin (--inspect-brk), install the
privileged-port redirect patch at the out/main/main.js entry breakpoint,
resume, then exit. The app keeps running once resumed."""
import json, sys, time
import websocket

WS = sys.argv[1]
PATCH = open(sys.argv[2]).read()
WS_TIMEOUT = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

ws = websocket.create_connection(WS, timeout=WS_TIMEOUT)
ws.settimeout(WS_TIMEOUT)
_id = [0]

def send(method, params=None):
    _id[0] += 1
    ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
    return _id[0]

labels = {}
def expect(rid, label):
    labels[rid] = label

send("Debugger.enable")
labels[_id[0]] = "enable"
send("Debugger.setBreakpointByUrl",
     {"urlRegex": r"out/main/main\.js$", "lineNumber": 0, "columnNumber": 0})
labels[_id[0]] = "setbp"
send("Runtime.runIfWaitingForDebugger")
labels[_id[0]] = "runif"

state = "wait-pause"
deadline = time.time() + 40
while state != "done" and time.time() < deadline:
    try:
        m = json.loads(ws.recv())
    except websocket.WebSocketTimeoutException:
        print("TIMEOUT", state); sys.exit(3)
    method = m.get("method")
    if m.get("id") in labels:
        label = labels.pop(m["id"])
        res = m.get("result", m.get("error"))
        if label == "patch":
            print("patch:", json.dumps(res)[:200])
            send("Debugger.resume"); labels[_id[0]] = "resume"
        elif label == "resume" and state == "patched":
            print("resumed: app running unpaused")
            state = "done"
        continue
    if method == "Debugger.paused" and state == "wait-pause":
        top = m["params"]["callFrames"][0]
        print("paused frames=", len(m["params"]["callFrames"]), "reason=", m["params"].get("reason"))
        send("Debugger.evaluateOnCallFrame",
             {"callFrameId": top["callFrameId"], "expression": PATCH, "returnByValue": True})
        labels[_id[0]] = "patch"
        state = "patched"
    elif method == "Runtime.exceptionThrown":
        print("EXC:", json.dumps(m["params"].get("exceptionDetails", {}))[:200])

if state == "done":
    try:
        ws.close()
    except Exception:
        pass
    print("OK")
    sys.exit(0)
else:
    print("INJECT INCOMPLETE state=", state)
    sys.exit(2)
