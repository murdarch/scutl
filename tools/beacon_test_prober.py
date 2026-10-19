"""A SERVABLE mock uptime prober for fresh-agent acceptance of the
uptime-monitoring recipe.

Why this exists (bead cst-mdiz.7, pattern of cst-mdiz.6/.13/.18/.19):
the recipe is bench-green (beacon slices, incl. the cst-mdiz.15.1
hardened regrade), but its bench twin injects in-process rails across
the LocalRail/ProberRail seams. That proves the Manager; a fresh agent
integrating the recipe drives the REAL `beacon` CLI, whose live
bindings speak an UptimeRobot-v3-shaped bearer REST (plus the v2
response_times surface the deafness byte actually rides — v3's
lastCheckedAt is null on every surface observed live, cst-2din). This
module serves that wire for real, byte-faithful to the live quirks:

  - v3 bearer REST: anon gets 401 {"message": "Invalid token."}
  - v3 lastCheckedAt is ALWAYS null — the verifier must ride v2
  - v2 getMonitors (form POST) honors the start/end window: evidence
    older than the asked window is simply absent, exactly how a frozen
    monitor goes quiet in the real rail

No real prober anywhere; no real targets. The server also plays each
scenario's watched SERVICE (the local face): GET /<scn>/target/<tid>
serves (or refuses to serve) the sentinel and the freshness serial, so
`beacon probe` exercises real HTTP too.

Usage
-----
    python3 tools/beacon_test_prober.py [--port 8445] [--host 127.0.0.1]

(stdlib only — any python3 runs it; no venv needed). Per scenario, point
the component at the scenario's v3 base and register the scenario's
target:

    beacon admin configure ... --prober-api-base http://127.0.0.1:8445/clean/v3
    beacon register web --url http://127.0.0.1:8445/clean/target/web \
        --sentinel sn-clean-web-live-01 --cadence-seconds 300 \
        --local-cadence-seconds 300

Use ONE state dir per scenario (SCUTL_BEACON_STATE) — scenarios are
independent worlds and a shared ledger would mix their evidence. Any
non-empty bearer key is accepted (except the `revoked-key` scenario);
put any placeholder in prober_api.key.

GET / lists every scenario and the behavior a correct operator must
show. GET /<scn>/account is the unauthenticated ground-truth console
(what actually stands in the "prober account") for the acceptance
record.

Two cells need no scenario here, by design:
  - prober-dark: point --prober-api-base at a closed port
    (http://127.0.0.1:1/v3) — connection refused is exactly the
    OSError the live rail classifies ProberUnreachable.
  - cap refusal: configure --max-targets 1 and register twice.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SCENARIOS: dict[str, dict] = {
    "clean": {
        "expect": "Target green from both vantage points: local probe ok "
                  "(200 + sentinel + fresh serial), prober UP with fresh "
                  "evidence. verify classifies up-corroborated; report "
                  "escalate=false; coverage full."},
    "corpse-proxy": {
        "expect": "The 200-from-the-grave cell: the target answers 200 "
                  "with a cheerful parking page — no sentinel, no serial "
                  "— and the prober's keyword check sees it DOWN. Local "
                  "probe must record ok=false (a bare 200 is a label on "
                  "the jar); verify classifies down-confirmed; "
                  "escalate=true."},
    "unreachable": {
        "expect": "The recipe's headline split: local probe green, prober "
                  "DOWN with a quoted incident. verify classifies "
                  "unreachable (the process lives, customers cannot reach "
                  "it); local health never argues it away; escalate=true."},
    "internal-down": {
        "expect": "The inverse split: prober UP and fresh, local target "
                  "serves 503 without the sentinel. verify classifies "
                  "internal-down; neither vantage overrules the other; "
                  "escalate=true."},
    "stale-serial": {
        "expect": "Freshness is the local half of the content wall: the "
                  "target serves the sentinel but a serial a full hour "
                  "old. Local probe ok=false (serial_fresh false) even "
                  "though the sentinel matched; with the prober UP this "
                  "reconciles internal-down; escalate=true."},
    "prober-deaf": {
        "expect": "The stale-green cell the whole recipe rides: the v3 "
                  "label stays UP but evidence FROZE at registration — "
                  "the v2 window returns nothing newer. verify must read "
                  "timestamps, not labels: prober-deaf, coverage "
                  "degraded, escalate=true. Never 'up'.",
        "freeze": True},
    "paused-silent": {
        "expect": "Someone paused the monitor out-of-band after the first "
                  "read (silencing wearing paperwork). The label keeps "
                  "its last word; verify classifies prober-deaf with the "
                  "paused-monitor breach quoted; never silently resumed "
                  "or re-upserted; escalate=true.",
        "pause_after_reads": 1},
    "config-drift": {
        "expect": "After registration the monitor's config drifts (URL "
                  "and cadence mutate at the prober). verify hard-fails "
                  "'watching the wrong door' quoting ledger vs monitor; "
                  "re-registering the same spec REFUSES (drift is "
                  "evidence, never laundered by a re-upsert); "
                  "escalate=true.",
        "drift_after_reads": 1},
    "foreign-monitor": {
        "expect": "A monitor named legacy-billing-page stands in the "
                  "account that the ledger never registered. verify "
                  "breaches foreign-monitor (leaked key or shared "
                  "custody) — an alarm, never an adoption; the operator "
                  "must not delete or adopt it; escalate=true.",
        "foreign": True},
    "zombie": {
        "expect": "Deregistration (with its scoped token) runs — but the "
                  "prober 'accepts' the DELETE and keeps the monitor "
                  "standing. The next verify breaches zombie-watch: the "
                  "watch outlived its tombstone; escalate=true.",
        "delete_noop": True},
    "revoked-key": {
        "expect": "Every authenticated call answers 401. The component "
                  "classifies ProberUnreachable ('prober rejected the "
                  "key') — registration refuses (an obligation to watch "
                  "must not begin life unwatchable); verify on an "
                  "existing state dir degrades to prober-dark coverage. "
                  "The key appears in no output.",
        "reject_auth": True},
}


class _Account:
    """One scenario's prober account + target faces."""

    def __init__(self, name: str, spec: dict):
        self.name = name
        self.spec = spec
        self.lock = threading.Lock()
        self.monitors: dict[str, dict] = {}
        self.seq = 0
        self.reads = 0
        if spec.get("foreign"):
            self.seq += 1
            self.monitors[str(self.seq)] = {
                "id": self.seq, "friendlyName": "legacy-billing-page",
                "url": "https://billing.example.net/health",
                "keywordValue": "billing-ok", "interval": 300,
                "paused": False, "created": time.time(),
                "last_check": time.time(), "incidents": []}

    # -- the outside face's verdict per scenario -------------------------
    def _state_for(self, mon: dict) -> tuple[str, list]:
        n = self.name
        if mon["friendlyName"] == "legacy-billing-page":
            return "UP", []
        if n in ("corpse-proxy",):
            return "DOWN", [{"id": 9001, "cause": "keyword missing",
                             "started_at": _iso(mon["created"])}]
        if n == "unreachable":
            return "DOWN", [{"id": 9002,
                             "cause": "connection timeout from public "
                                      "vantage (sentinel unreachable)",
                             "started_at": _iso(mon["created"])}]
        return "UP", []

    def v3_rows(self) -> list[dict]:
        with self.lock:
            self.reads += 1
            pa = self.spec.get("pause_after_reads")
            da = self.spec.get("drift_after_reads")
            for mon in self.monitors.values():
                if mon["friendlyName"] == "legacy-billing-page":
                    continue
                if pa is not None and self.reads > pa and not mon["paused"]:
                    mon["paused"] = True
                    mon["pause_frozen_at"] = mon["last_check"]
                if da is not None and self.reads > da \
                        and not mon.get("drifted"):
                    mon["drifted"] = True
                    mon["url"] = mon["url"] + "?moved=1"
                    mon["interval"] = int(mon["interval"]) * 4
                if not self.spec.get("freeze") and not mon["paused"]:
                    mon["last_check"] = time.time()
            rows = []
            for mon in self.monitors.values():
                state, incidents = self._state_for(mon)
                rows.append({
                    "id": mon["id"],
                    "friendlyName": mon["friendlyName"],
                    "url": mon["url"],
                    "keywordValue": mon["keywordValue"],
                    "interval": mon["interval"],
                    "status": "PAUSED" if mon["paused"] else state,
                    # live-faithful: null on every surface (cst-2din);
                    # the deafness byte rides v2
                    "lastCheckedAt": None,
                    "incidents": incidents})
            return rows

    def v2_rows(self, start: int, end: int) -> list[dict]:
        with self.lock:
            rows = []
            for mon in self.monitors.values():
                lc = int(mon.get("pause_frozen_at") or mon["last_check"])
                rts = [{"datetime": lc}] if start <= lc <= end else []
                rows.append({"id": mon["id"], "response_times": rts})
            return rows

    def create(self, body: dict) -> dict:
        with self.lock:
            self.seq += 1
            # a frozen scenario's evidence froze two hours ago — well
            # past any sane horizon, and outside the v2 ask window
            created = time.time() - (7200 if self.spec.get("freeze")
                                     else 0)
            self.monitors[str(self.seq)] = {
                "id": self.seq, "friendlyName": body.get("friendlyName"),
                "url": body.get("url"),
                "keywordValue": body.get("keywordValue"),
                "interval": int(body.get("interval") or 300),
                "paused": False, "created": created,
                "last_check": created, "incidents": []}
            return {"id": self.seq}

    def patch(self, mid: str, body: dict) -> bool:
        with self.lock:
            mon = self.monitors.get(mid)
            if mon is None:
                return False
            if body.get("status") == "paused":
                mon["paused"] = True
                mon["pause_frozen_at"] = mon["last_check"]
                return True
            for src, dst in (("url", "url"), ("keywordValue",
                                              "keywordValue")):
                if src in body:
                    mon[dst] = body[src]
            if "interval" in body:
                mon["interval"] = int(body["interval"])
            return True

    def delete(self, mid: str) -> bool:
        with self.lock:
            if mid not in self.monitors:
                return False
            if not self.spec.get("delete_noop"):
                del self.monitors[mid]
            return True

    # -- the inside face: the watched service itself ----------------------
    def target_page(self, tid: str) -> tuple[int, dict, str]:
        n = self.name
        sentinel = f"sn-{n}-{tid}-live-01"
        if n in ("corpse-proxy",):
            return 200, {}, ("<html>This domain is parked. Buy it today!"
                             "</html>")
        if n == "internal-down":
            return 503, {}, "upstream connect error"
        if n == "stale-serial":
            return 200, {"X-Beacon-Serial": str(int(time.time()) - 3600)}, \
                f"ok {sentinel}"
        return 200, {"X-Beacon-Serial": str(int(time.time()))}, \
            f"ok {sentinel}"


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(epoch))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8445)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    accounts = {n: _Account(n, s) for n, s in SCENARIOS.items()}

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code: int, obj) -> None:
            data = json.dumps(obj, indent=2).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _page(self, code: int, headers: dict, body: str) -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw.decode() or "{}")
            except ValueError:
                return {"_raw": raw.decode(errors="replace")}

        def _authed(self, acct: _Account) -> bool:
            if acct.spec.get("reject_auth"):
                return False
            auth = self.headers.get("Authorization") or ""
            return auth.startswith("Bearer ") and len(auth) > 7

        def _serve(self, method: str) -> None:
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            if not parts:
                self._json(200, {
                    "service": "beacon_test_prober",
                    "note": "one state dir per scenario; any non-empty "
                            "bearer key; ground truth at /<scn>/account",
                    "scenarios": {n: s["expect"]
                                  for n, s in SCENARIOS.items()}})
                return
            acct = accounts.get(parts[0])
            if acct is None:
                self._json(404, {"message": "no such scenario"})
                return
            rest = parts[1:]

            # the watched service's own face (the LOCAL rail hits this)
            if rest[:1] == ["target"] and len(rest) == 2 \
                    and method == "GET":
                code, headers, body = acct.target_page(rest[1])
                self._page(code, headers, body)
                return
            # unauthenticated ground-truth console
            if rest == ["account"] and method == "GET":
                with acct.lock:
                    self._json(200, {"scenario": acct.name,
                                     "expect": acct.spec["expect"],
                                     "monitors": list(
                                         acct.monitors.values())})
                return
            # v2: form POST, api_key, response_times window
            if rest == ["v2", "getMonitors"] and method == "POST":
                n = int(self.headers.get("Content-Length") or 0)
                form = parse_qs(self.rfile.read(n).decode())
                if acct.spec.get("reject_auth") \
                        or not form.get("api_key", [""])[0]:
                    self._json(401, {"stat": "fail",
                                     "error": {"type": "invalid_parameter",
                                               "message": "api_key is "
                                                          "invalid."}})
                    return
                start = int(form.get("response_times_start_date",
                                     ["0"])[0])
                end = int(form.get("response_times_end_date",
                                   [str(int(time.time()))])[0])
                self._json(200, {"stat": "ok",
                                 "monitors": acct.v2_rows(start, end)})
                return
            # v3: bearer REST
            if rest[:1] == ["v3"]:
                if not self._authed(acct):
                    # byte-faithful to the live anon fingerprint
                    self._json(401, {"message": "Invalid token."})
                    return
                if rest == ["v3", "monitors"] and method == "GET":
                    self._json(200, {"monitors": acct.v3_rows()})
                    return
                if rest == ["v3", "monitors"] and method == "POST":
                    self._json(200, acct.create(self._body()))
                    return
                if len(rest) == 3 and rest[:2] == ["v3", "monitors"]:
                    if method == "PATCH":
                        ok = acct.patch(rest[2], self._body())
                        self._json(200 if ok else 404,
                                   {"id": rest[2]} if ok else
                                   {"message": "monitor not found"})
                        return
                    if method == "DELETE":
                        ok = acct.delete(rest[2])
                        self._json(200 if ok else 404,
                                   {"deleted": ok} if ok else
                                   {"message": "monitor not found"})
                        return
            self._json(404, {"message": "no such route"})

        def do_GET(self):  # noqa: N802
            self._serve("GET")

        def do_POST(self):  # noqa: N802
            self._serve("POST")

        def do_PATCH(self):  # noqa: N802
            self._serve("PATCH")

        def do_DELETE(self):  # noqa: N802
            self._serve("DELETE")

        def log_message(self, fmt, *a):
            pass

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"beacon test prober on http://{args.host}:{args.port}/ — "
          f"{len(SCENARIOS)} scenarios; GET / for the menu")
    srv.serve_forever()


if __name__ == "__main__":
    main()
