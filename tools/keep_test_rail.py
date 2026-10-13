"""A SERVABLE managed-database rail for keep fresh-agent acceptance.

Why this exists (bead cst-mdiz.18, pattern of cst-mdiz.9/.10/.11/.13):
the managed-database recipe is bench-green (ladder/keep/), but its
bench twin injects in-process Rail/Database/DumpStore mocks. That
proves the Manager; a fresh agent integrating the recipe drives the
REAL `keep` CLI, which speaks the Vultr /v2/databases wire to its rail
(`SCUTL_KEEP_RAIL_API` test seam) and REAL psql/pg_dump/pg_restore to
the cluster. This module serves the rail wire in front of a LOCAL
PostgreSQL: a "cluster" is a registration whose coordinates point at
127.0.0.1:5432, and the rail's db/user ops perform real DDL there — so
migrations, dumps, and scratch restores exercise the genuine Postgres
wire while the subscription lifecycle stays scripted and free.

The dump store is the real silo component (keep's SiloSeam), which the
acceptance backs with silo's own servable store (tools/
silo_test_store.py) — the composition is the point, and silo's corrupt
knob is how the dump-digest-mismatch cell arrives over the wire.

Prereqs: a local PostgreSQL with ssl=on (Debian default) and a
bootstrap superuser role this server uses for DDL. Create it once:

    sudo -u postgres psql -c \
      "CREATE ROLE keeprail LOGIN PASSWORD 'keeprail-test-pw' SUPERUSER"

(or run this script with --bootstrap, which does exactly that via
sudo). Override with KEEP_RAIL_DSN if your role differs.

Usage
-----
    python3 tools/keep_test_rail.py [--port 8514] [--host 127.0.0.1] [--bootstrap]

    export SCUTL_KEEP_RAIL_API=http://127.0.0.1:8514/clean

    GET  /                     scenario index + expected behaviors
    GET  /<scen>/facts         clusters registered, knobs armed
    POST /<scen>/arm           knobs, JSON body {knob: value}:
        {"widen_ips": true}        trusted_ips silently grows a stranger
        {"plant_orphan": true}     a second keep-labeled cluster appears
        {"still_billing": true}    DELETE acks but the cluster stays,
                                   charges keep accruing (undead)

Bearer key per scenario: railkey-<scenario> (the index names it).
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PG_HOST, PG_PORT = "127.0.0.1", 5432
BOOT_DSN = ("host=127.0.0.1 port=5432 dbname=postgres "
            "user=keeprail password=keeprail-test-pw sslmode=require")


def _psql(sql: str, dsn: str | None = None) -> str:
    r = subprocess.run(["psql", dsn or BOOT_DSN, "-Atc", sql],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"psql: {r.stderr[:300]}")
    return r.stdout.strip()


class World:
    def __init__(self, name: str, expect: str, note: str | None = None):
        self.name = name
        self.expect = expect
        self.note = note
        self.key = f"railkey-{name}"
        self.serial = 0
        self.clusters: dict[str, dict] = {}
        self.dbs: dict[str, list[str]] = {}
        self.users: dict[str, str] = {}      # cluster -> app user name
        self.still_billing = False

    def facts(self) -> dict:
        return {"scenario": self.name, "expect": self.expect,
                "note": self.note, "rail_key": self.key,
                "env": {"SCUTL_KEEP_RAIL_API":
                        f"http://HOST:PORT/{self.name}"},
                "clusters": {cid: {k: v for k, v in c.items()
                                   if k != "password"}
                             for cid, c in self.clusters.items()},
                "dbs": self.dbs, "still_billing": self.still_billing}


def _build() -> dict[str, World]:
    return {w.name: w for w in [
        World("clean",
              "the full life against a real local Postgres: provision "
              "(allowlist set before first use, creds to state, never "
              "stdout), clean migrations in order, reapply/out-of-order "
              "refused by ledger, checksum tamper hard-fails, "
              "destructive DDL approval-gated, dump through the real "
              "silo with read-back, rehearsal green in a real scratch "
              "db, dump-digest mismatch via silo's corrupt knob goes "
              "red, allowlist drift breaches, teardown takes the final "
              "dump first and verifies gone"),
        World("orphan",
              "arm plant_orphan after provisioning: a second "
              "keep-labeled cluster on the rail breaches status on "
              "sight, and a re-provision attempt refuses on "
              "max_clusters — never overwrites the ledger"),
        World("undead",
              "arm still_billing, then teardown: the DELETE is acked "
              "as still-billing — teardown must FAIL (the final dump "
              "will already have been taken), never report done",
              note="arm BEFORE the teardown"),
    ]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8514)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--bootstrap", action="store_true")
    args = ap.parse_args()
    if args.bootstrap:
        subprocess.run(["sudo", "-n", "-u", "postgres", "psql", "-c",
                        "DROP ROLE IF EXISTS keeprail; CREATE ROLE "
                        "keeprail LOGIN PASSWORD 'keeprail-test-pw' "
                        "SUPERUSER"], check=True, cwd="/tmp")
        print("bootstrap role 'keeprail' ready", flush=True)

    worlds = _build()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, doc) -> None:
            body = json.dumps(doc, indent=1).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _world(self):
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            if not parts or parts[0] not in worlds:
                return None, parts
            return worlds[parts[0]], parts[1:]

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw) if raw else {}

        def _cluster_doc(self, w: World, cid: str) -> dict:
            c = w.clusters[cid]
            return {"id": cid, "status": "Running",
                    "host": PG_HOST, "port": str(PG_PORT),
                    "user": "keeprail", "password": c["password"],
                    "trusted_ips": c["trusted_ips"],
                    "latest_backup": "2026-10-13T04:00:00+00:00",
                    "label": c["label"],
                    "pending_charges": "0.40" if (w.still_billing and
                                                  c.get("deleted"))
                    else "0.12",
                    "plan": c["plan"], "region": c["region"]}

        def do_GET(self):
            w, rest = self._world()
            if w is None:
                if not rest:
                    return self._send(200, {
                        "scenarios": {n: wd.facts()
                                      for n, wd in worlds.items()}})
                return self._send(404, {"error": "no such scenario"})
            if rest[:1] == ["facts"]:
                return self._send(200, w.facts())
            if (self.headers.get("Authorization")
                    != f"Bearer {w.key}"):
                return self._send(401, {"error": "bad rail key"})
            if rest == ["databases"]:
                return self._send(200, {"databases": [
                    self._cluster_doc(w, cid) for cid, c in
                    w.clusters.items() if not c.get("deleted")
                    or w.still_billing]})
            if len(rest) == 2 and rest[0] == "databases":
                cid = rest[1]
                c = w.clusters.get(cid)
                if c is None or (c.get("deleted") and not w.still_billing):
                    return self._send(404, {"error": "not found"})
                return self._send(200,
                                  {"database": self._cluster_doc(w, cid)})
            return self._send(404, {"error": f"GET: no route {rest}"})

        def do_POST(self):
            w, rest = self._world()
            if w is None:
                return self._send(404, {"error": "no such scenario"})
            payload = self._body()
            if rest == ["arm"]:
                applied = {}
                if payload.get("widen_ips"):
                    for c in w.clusters.values():
                        c["trusted_ips"] = (c["trusted_ips"]
                                            + ["203.0.113.66/32"])
                    applied["widen_ips"] = "203.0.113.66/32 appended"
                if payload.get("plant_orphan"):
                    w.serial += 1
                    cid = f"orphan-{w.name}-{w.serial}"
                    w.clusters[cid] = {"label": "scutl-keep",
                                       "plan": "vultr-dbaas-hobbyist",
                                       "region": "ewr",
                                       "password": "unused",
                                       "trusted_ips": []}
                    applied["plant_orphan"] = cid
                if "still_billing" in payload:
                    w.still_billing = bool(payload["still_billing"])
                    applied["still_billing"] = w.still_billing
                return self._send(200, {"armed": applied})
            if (self.headers.get("Authorization")
                    != f"Bearer {w.key}"):
                return self._send(401, {"error": "bad rail key"})
            try:
                if rest == ["databases"]:
                    w.serial += 1
                    cid = f"keepdb-{w.name}-{w.serial}"
                    w.clusters[cid] = {
                        "label": payload.get("label", ""),
                        "plan": payload.get("plan", ""),
                        "region": payload.get("region", ""),
                        "password": secrets.token_hex(12),
                        "trusted_ips": []}
                    w.dbs[cid] = []
                    return self._send(201, {
                        "database": self._cluster_doc(w, cid)})
                if (len(rest) == 3 and rest[0] == "databases"
                        and rest[2] == "dbs"):
                    # NOTE: database names are global on the one local
                    # Postgres — give each scenario its own logical db
                    # name (keep admin configure --database <unique>).
                    cid, name = rest[1], payload["name"]
                    safe = name.replace('"', "")
                    _psql(f'DROP DATABASE IF EXISTS "{safe}"')
                    _psql(f'CREATE DATABASE "{safe}"')
                    owner = w.users.get(cid)
                    if owner:
                        _psql(f'ALTER DATABASE "{safe}" OWNER TO '
                              f'"{owner}"')
                    w.dbs.setdefault(cid, []).append(name)
                    return self._send(201, {"ok": True})
                if (len(rest) == 3 and rest[0] == "databases"
                        and rest[2] == "users"):
                    cid, uname = rest[1], payload["username"]
                    pw = secrets.token_hex(12)
                    safe = uname.replace('"', "")
                    # the role name is global on the one local
                    # Postgres: re-password an existing role rather
                    # than drop it (it may own another world's dbs)
                    exists = _psql("SELECT 1 FROM pg_roles WHERE "
                                   f"rolname='{safe}'")
                    if exists:
                        _psql(f"ALTER ROLE \"{safe}\" LOGIN PASSWORD "
                              f"'{pw}'")
                    else:
                        _psql(f"CREATE ROLE \"{safe}\" LOGIN PASSWORD "
                              f"'{pw}'")
                    w.users[cid] = safe
                    for name in w.dbs.get(cid, []):
                        _psql(f'ALTER DATABASE "{name}" OWNER TO '
                              f'"{safe}"')
                    return self._send(201, {"user": {"username": uname,
                                                     "password": pw}})
            except RuntimeError as e:
                return self._send(500, {"error": str(e)})
            return self._send(404, {"error": f"POST: no route {rest}"})

        def do_PUT(self):
            w, rest = self._world()
            if w is None:
                return self._send(404, {"error": "no such scenario"})
            if (self.headers.get("Authorization")
                    != f"Bearer {w.key}"):
                return self._send(401, {"error": "bad rail key"})
            payload = self._body()
            if len(rest) == 2 and rest[0] == "databases":
                c = w.clusters.get(rest[1])
                if c is None:
                    return self._send(404, {"error": "not found"})
                if "trusted_ips" in payload:
                    c["trusted_ips"] = list(payload["trusted_ips"])
                return self._send(200, {"ok": True})
            return self._send(404, {"error": f"PUT: no route {rest}"})

        def do_DELETE(self):
            w, rest = self._world()
            if w is None:
                return self._send(404, {"error": "no such scenario"})
            if (self.headers.get("Authorization")
                    != f"Bearer {w.key}"):
                return self._send(401, {"error": "bad rail key"})
            try:
                if (len(rest) == 4 and rest[0] == "databases"
                        and rest[2] == "dbs"):
                    name = rest[3].replace('"', "")
                    _psql(f'DROP DATABASE IF EXISTS "{name}"')
                    w.dbs.get(rest[1], [])[:] = [
                        d for d in w.dbs.get(rest[1], []) if d != rest[3]]
                    return self._send(204, {})
                if len(rest) == 2 and rest[0] == "databases":
                    cid = rest[1]
                    c = w.clusters.get(cid)
                    if c is None:
                        return self._send(404, {"error": "not found"})
                    if w.still_billing:
                        c["deleted"] = True     # acked, still listed
                        return self._send(204, {})
                    for name in w.dbs.get(cid, []):
                        safe = name.replace('"', "")
                        _psql(f'DROP DATABASE IF EXISTS "{safe}"')
                    w.clusters.pop(cid)
                    w.dbs.pop(cid, None)
                    return self._send(204, {})
            except RuntimeError as e:
                return self._send(500, {"error": str(e)})
            return self._send(404, {"error": f"DELETE: no route {rest}"})

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"keep test rail on http://{args.host}:{args.port}/ — "
          f"GET / for the scenario index (local Postgres at "
          f"{PG_HOST}:{PG_PORT})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
