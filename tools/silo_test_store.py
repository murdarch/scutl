"""A SERVABLE object store + subscription rail for silo fresh-agent
acceptance.

Why this exists (bead cst-mdiz.19, pattern of cst-mdiz.9/.10/.11/.13):
the durable-object-storage recipe is bench-green (ladder/silo/), but
its bench twin injects in-process mocks across the ObjectStore/Rail
seams. That proves the Manager; a fresh agent integrating the recipe
drives the REAL `silo` CLI, which speaks path-style S3 (SigV4) to its
endpoint and the Vultr wire to its rail. This module serves both — the
store with the bench's hazard knobs (phantom writes, same-length
corruption with lying metadata, truncation, outage) and the rail with
the undead-subscription case — so the adversarial storage weather the
recipe must catch arrives over real HTTP.

No real storage anywhere: objects are dicts, subscriptions are dicts,
nothing bills.

Usage
-----
    python3 tools/silo_test_store.py [--port 8504] [--host 127.0.0.1]

(stdlib only — any python3 runs it). Each scenario is a base path
holding an INDEPENDENT world (rail + store):

    export SCUTL_SILO_RAIL_API=http://127.0.0.1:8504/clean/rail
    # the rail's create answers with this world's store endpoint
    # (scheme included — the component's endpoint seam uses it as-is)

    GET  /                      scenario index + expected behaviors
    GET  /<scen>/facts          world state (objects, subscriptions)
    POST /<scen>/arm            hazard knobs, JSON body {knob: value}:
        {"phantom": true}            the NEXT object put is acked, then lost
        {"corrupt": "<key>"}         flip bytes SAME-LENGTH; HEAD keeps
                                     claiming the original etag/size
        {"truncate": "<key>"}        shorten the stored bytes
        {"plant": "<key>"}           a foreign object appears in the bucket
        {"drop": "<key>"}            the object vanishes from the bucket
        {"down": true|false}         the store endpoint 503s / recovers
"""

from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _etag(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class World:
    def __init__(self, name: str, expect: str, undead: bool = False,
                 note: str | None = None):
        self.name = name
        self.expect = expect
        self.note = note
        self.undead = undead          # destroy acked, sub stays, store up
        self.subs: list[dict] = []
        self.serial = 0
        self.buckets: dict[str, dict[str, dict]] = {}   # bucket -> key -> obj
        self.phantom_next = False
        self.down = False
        self.access = f"AK-{name}"
        self.secret = f"SK-{name}"
        self.rail_key = f"railkey-{name}"

    def endpoint(self, host: str, port: int) -> str:
        return f"http://{host}:{port}/{self.name}/s3"

    def facts(self) -> dict:
        return {"scenario": self.name, "expect": self.expect,
                "note": self.note,
                "rail_key": self.rail_key,
                "env": {"SCUTL_SILO_RAIL_API":
                        f"http://HOST:PORT/{self.name}/rail"},
                "subscriptions": self.subs,
                "objects": {b: {k: {"size": len(o["body"]),
                                    "claimed_etag": o["etag"]}
                                for k, o in objs.items()}
                            for b, objs in self.buckets.items()},
                "down": self.down, "undead": self.undead}


def _build() -> dict[str, World]:
    return {w.name: w for w in [
        World("clean",
              "the full life: provision over the rail (keys delivered, "
              "never printed), clean puts (hash, upload, read back, "
              "re-hash), overwrite refused, deny-list refused, get, "
              "rehearsal green, inventory clean, and a teardown that is "
              "done only when the rail says gone AND the endpoint probe "
              "fails (this world's store goes dark after destroy)"),
        World("hazards",
              "storage weather, armed one knob at a time: a PHANTOM put "
              "(acked, lost) fails read-back and never enters the "
              "manifest; same-length CORRUPTION is caught by re-hash at "
              "get/rehearse while HEAD keeps claiming the original "
              "etag/size (log the metadata as the liar it was); "
              "TRUNCATION fails size before digest; PLANT/DROP make "
              "inventory breach in both directions with the diff quoted"),
        World("down",
              "an outage mid-life: after a clean put, arm {\"down\": "
              "true} — status and rehearsal breach honestly with the "
              "probe evidence, no tool reports healthy from memory; "
              "disarm and the next rehearsal is green again"),
        World("undead",
              "teardown's hard case: the rail ACKS the destroy but the "
              "subscription stays listed and the endpoint keeps "
              "answering — si_teardown must FAIL this, not pass it",
              undead=True),
    ]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8504)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    worlds = _build()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes | dict | list,
                  ctype: str = "application/json",
                  headers: dict | None = None) -> None:
            if isinstance(body, (dict, list)):
                body = json.dumps(body, indent=1).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _world(self):
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            if not parts or parts[0] not in worlds:
                return None, parts
            return worlds[parts[0]], parts[1:]

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        # ---- rail (Vultr object-storage wire) --------------------------
        def _rail(self, w: World, method: str, path: list[str],
                  payload: dict):
            auth = self.headers.get("Authorization") or ""
            if auth != f"Bearer {w.rail_key}":
                return self._send(401, {"error": "bad rail key"})
            if method == "POST" and path == ["object-storage"]:
                w.serial += 1
                sub = {"id": f"sub-{w.name}-{w.serial}",
                       "label": payload.get("label", ""),
                       "status": "active",
                       "s3_hostname": w.endpoint(args.host, args.port),
                       "s3_access_key": w.access,
                       "s3_secret_key": w.secret}
                w.subs.append({k: v for k, v in sub.items()})
                return self._send(201, {"object_storage": sub})
            if (method == "GET" and len(path) == 4
                    and path[1] == "clusters" and path[3] == "tiers"):
                return self._send(200, {"tiers": [
                    {"id": 1, "price": 5.00, "disk_gb_price": 0.018,
                     "bw_gb_price": 0.01}]})
            if method == "GET" and len(path) == 2:
                sub = next((s for s in w.subs if s["id"] == path[1]), None)
                if sub is None:
                    return self._send(404, {"error": "not found"})
                return self._send(200, {"object_storage": sub})
            if method == "DELETE" and len(path) == 2:
                if w.undead:
                    return self._send(204, b"")   # acked; nothing removed
                w.subs = [s for s in w.subs if s["id"] != path[1]]
                w.down = True                     # endpoint goes dark too
                return self._send(204, b"")
            return self._send(404, {"error": f"rail: no route {path}"})

        # ---- store (path-style S3) --------------------------------------
        def _store(self, w: World, method: str, path: list[str]):
            if w.down:
                return self._send(503, {"error": "store endpoint down"})
            auth = self.headers.get("Authorization") or ""
            anonymous = "Credential=" not in auth
            if not anonymous and f"Credential={w.access}/" not in auth:
                return self._send(403, {"error":
                                        "SignatureDoesNotMatch"})
            bucket = path[0] if path else ""
            key = "/".join(path[1:])
            objs = w.buckets.setdefault(bucket, {})
            if method == "PUT" and not key:
                return self._send(200, b"")           # ensure bucket
            if method == "PUT":
                body = self._body()
                if w.phantom_next:
                    w.phantom_next = False
                    return self._send(200, b"")       # acked, lost
                objs[key] = {"body": body, "etag": _etag(body)}
                return self._send(200, b"")
            if method == "DELETE":
                objs.pop(key, None)
                return self._send(204, b"")
            if method in ("GET", "HEAD") and key:
                obj = objs.get(key)
                if obj is None:
                    return self._send(404, b"<Error><Code>NoSuchKey"
                                           b"</Code></Error>",
                                      "application/xml")
                if method == "HEAD":
                    # the metadata keeps claiming the ORIGINAL etag (the
                    # lying-metadata knob corrupts the body only); HEAD's
                    # Content-Length is the stored size
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "application/octet-stream")
                    self.send_header("Content-Length",
                                     str(len(obj["body"])))
                    self.send_header("ETag", f'"{obj["etag"]}"')
                    self.end_headers()
                    return None
                return self._send(200, obj["body"],
                                  "application/octet-stream")
            if method == "GET":                       # list-type=2
                contents = "".join(
                    f"<Contents><Key>{k}</Key>"
                    f"<Size>{len(o['body'])}</Size></Contents>"
                    for k, o in sorted(objs.items()))
                xml = (b'<?xml version="1.0"?><ListBucketResult '
                       b'xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                       + contents.encode() + b"</ListBucketResult>")
                return self._send(200, xml, "application/xml")
            return self._send(404, {"error": "store: no route"})

        def _route(self, method: str):
            w, rest = self._world()
            if w is None:
                if not rest and method == "GET":
                    return self._send(200, {
                        "scenarios": {n: wd.facts()
                                      for n, wd in worlds.items()}})
                return self._send(404, {"error": "no such scenario"})
            if rest[:1] == ["facts"] and method == "GET":
                return self._send(200, w.facts())
            if rest[:1] == ["arm"] and method == "POST":
                p = json.loads(self._body() or b"{}")
                applied = {}
                objs = max(w.buckets.values(), key=len, default={})
                if p.get("phantom"):
                    w.phantom_next = True
                    applied["phantom"] = "next put acked then lost"
                if "corrupt" in p and p["corrupt"] in objs:
                    o = objs[p["corrupt"]]
                    body = bytearray(o["body"])
                    if body:
                        body[len(body) // 2] ^= 0xFF
                    o["body"] = bytes(body)           # etag claim kept
                    applied["corrupt"] = p["corrupt"]
                if "truncate" in p and p["truncate"] in objs:
                    o = objs[p["truncate"]]
                    o["body"] = o["body"][: max(0, len(o["body"]) - 7)]
                    applied["truncate"] = p["truncate"]
                if "plant" in p:
                    objs[p["plant"]] = {"body": b"foreign bytes",
                                        "etag": _etag(b"foreign bytes")}
                    applied["plant"] = p["plant"]
                if "drop" in p and p["drop"] in objs:
                    objs.pop(p["drop"])
                    applied["drop"] = p["drop"]
                if "down" in p:
                    w.down = bool(p["down"])
                    applied["down"] = w.down
                return self._send(200, {"armed": applied})
            if rest[:1] == ["rail"]:
                payload = {}
                if method == "POST":
                    raw = self._body()
                    payload = json.loads(raw) if raw else {}
                return self._rail(w, method, rest[1:], payload)
            if rest[:1] == ["s3"]:
                return self._store(w, method, rest[1:])
            return self._send(404, {"error": "rail|s3|facts|arm"})

        def do_GET(self):
            self._route("GET")

        def do_HEAD(self):
            self._route("HEAD")

        def do_PUT(self):
            self._route("PUT")

        def do_POST(self):
            self._route("POST")

        def do_DELETE(self):
            self._route("DELETE")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"silo test store on http://{args.host}:{args.port}/ — "
          f"GET / for the scenario index", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
