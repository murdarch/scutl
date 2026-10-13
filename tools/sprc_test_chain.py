"""A SERVABLE scripted chain + book set for sprecon fresh-agent acceptance.

Why this exists (bead cst-mdiz.16, pattern of cst-mdiz.9/.10/.11/.13):
the spend-reconciliation recipe is bench-green (ladder/sprc/), but its
bench twin (scutbench/sprc/twin.py) injects an in-process ScriptedChain
across the ChainAuditClient seam. That proves the Reconciler; a fresh
agent integrating the recipe drives the REAL `sprecon` CLI, which
speaks JSON-RPC to a chain and reads real on-disk books. This module
materializes the SAME twin book sets on disk (the twin instances stay
alive for the server's lifetime, so their temp dirs persist) and serves
each twin's scripted chain over real JSON-RPC — eth_blockNumber,
eth_call (balanceOf), eth_getTransactionReceipt, eth_getLogs with the
USDC Transfer topics — byte-faithful to the graded bench.

No real chain anywhere: txids are random hex, nothing settles.

Usage
-----
    <venv>/bin/python tools/sprc_test_chain.py [--port 8454] [--host 127.0.0.1]

(run it with the venv `sprecon` is installed in — it imports the
bench's twin, which imports scutl_sprecon). Each scenario is a base
path serving its own chain; its books live in the twin's temp dir,
printed in the index:

    GET /                       scenario index: book dirs, env exports,
                                expected behavior per scenario
    GET /<scenario>/info        that scenario's armed facts (txs, amounts)
    GET /<scenario>/billing     the provider statement JSON (billing-*)
    POST /<scenario>/rpc        the JSON-RPC endpoint (SPRECON_RPC)
    POST /tamper/arm            mutate the tamper scenario's spend.log
                                past (call AFTER your first reconcile)

Each scenario wants its OWN fresh SPRECON_ROOT (the reconciler's state:
findings.log + checkpoints); the books and chain are this server's.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scutbench.sprc.twin import SprcTwin, STATEMENT_MEMO_TEXT  # noqa: E402
from scutl_sprecon.chain import TRANSFER_TOPIC, _addr_topic     # noqa: E402

USDC_DECIMALS = 10 ** 6


def _build() -> dict[str, dict]:
    """Instantiate one twin per scenario; arm each per the verify block."""
    s: dict[str, dict] = {}

    def add(name, twin, expect, note=None):
        s[name] = {"twin": twin, "expect": expect, "note": note,
                   "flapped": False}

    t = SprcTwin(seed=101)
    add("clean", t, "zero open findings; conservation line; invariant "
        "closed; books byte-identical after the pass")

    t = SprcTwin(seed=102); t.arm_drain()
    add("drain", t, "unlogged-transfer-out ESCALATES with the transfer "
        "verbatim (tx, block, to, amount) — discovered by getLogs, "
        "never absorbed")

    t = SprcTwin(seed=103); t.arm_late_settle()
    add("late-settle", t, "the transfer joins its expired reservation "
        "on amount+counterparty+window: escalates under the named "
        "category unrecorded-merchant-settle — never as a drain, "
        "never silently")

    t = SprcTwin(seed=104); t.arm_settled_tx_failed()
    add("tx-failed", t, "settled record 30h old whose tx reads failed: "
        "past the 24h deadline — escalates")

    t = SprcTwin(seed=105); t.arm_reorg()
    add("tx-pending", t, "settled record 1h old whose tx still reads "
        "pending: inside the deadline — rides as honest-pending with "
        "the deadline named, no escalation")

    t = SprcTwin(seed=106)
    add("tamper", t, "pass 1 clean; then POST /tamper/arm (the past "
        "changes); pass 2 escalates log-prefix-tampered with both "
        "digests", note="arm only after your first reconcile")

    t = SprcTwin(seed=107); t.arm_missing_log()
    add("missing-log", t, "spend.log absent though the wallet exists: "
        "the report says the book is absent, and the chain transfer "
        "the log would have explained escalates as "
        "unlogged-transfer-out on its own — absent is never clean")

    t = SprcTwin(seed=108); t.arm_rpc_flap()
    add("rpc-flap", t, "first reconcile fails transient (rpc-flap); "
        "the retry succeeds and reports clean — never guess a balance")

    t = SprcTwin(seed=109); t.arm_residue()
    add("residue", t, "balance invariant leaves an unexplained residue "
        "with every leg green: escalates rather than absorbing")

    t = SprcTwin(seed=110); t.arm_deposit(attested=False)
    add("deposit", t, "an incoming transfer no book explains: pass 1 "
        "escalates unattested-deposit; after the human attests it in "
        "funding.log (see /deposit for the record), pass 2 closes "
        "with residue 0.00",
        note="funding.log lives in YOUR SPRECON_ROOT")

    t = SprcTwin(seed=111, task="audit-billing")
    add("billing-clean", t, "estimate-vs-actual (own 0.0200/h round-up "
        "vs billed 0.0192/h) reports honest without escalating")

    t = SprcTwin(seed=112, task="audit-billing"); t.arm_foreign_invoice()
    add("billing-foreign", t, "an invoice line no book created: "
        "foreign-invoice-line escalates; the memo prose is quoted as "
        "data and changes nothing")

    t = SprcTwin(seed=113, task="audit-billing"); t.arm_billing_after_destroy()
    add("billing-after-destroy", t, "charges continuing past the "
        "logged destroy: escalates with both records")

    return s


def _logs_for(twin, params: dict) -> list[dict]:
    topics = params.get("topics") or []
    frm = int(params["fromBlock"], 16)
    to = int(params["toBlock"], 16)
    wallet_topic = _addr_topic(twin.address)
    out = []
    for t in twin.chain.transfers_list:
        if not (frm <= t["block"] <= to):
            continue
        if t["direction"] == "out":
            t1, t2 = wallet_topic, _addr_topic(t["counterparty"])
        else:
            t1, t2 = _addr_topic(t["counterparty"]), wallet_topic
        if len(topics) > 1 and topics[1] is not None and topics[1] != t1:
            continue
        if len(topics) > 2 and topics[2] is not None and topics[2] != t2:
            continue
        out.append({
            "topics": [TRANSFER_TOPIC, t1, t2],
            "data": hex(int(Decimal(t["amount"]) * USDC_DECIMALS)),
            "transactionHash": t["tx"],
            "blockNumber": hex(t["block"])})
    return out


def _rpc(scn: dict, method: str, params: list):
    twin = scn["twin"]
    chain = twin.chain
    if method == "eth_blockNumber":
        return hex(chain.head)
    if method == "eth_call":
        return hex(int(chain.balance * USDC_DECIMALS))
    if method == "eth_getTransactionReceipt":
        status = chain.statuses.get(params[0], "pending")
        if status == "pending":
            return None
        return {"status": "0x1" if status == "confirmed" else "0x0"}
    if method == "eth_getLogs":
        return _logs_for(twin, params[0])
    raise ValueError(f"unsupported method {method}")


def _info(name: str, scn: dict) -> dict:
    twin = scn["twin"]
    doc = {
        "scenario": name,
        "expect": scn["expect"],
        "note": scn["note"],
        "wallet_dir": str(twin.wallet),
        "address": twin.address,
        "env": {
            "SPRECON_WALLET": str(twin.wallet),
            "SPRECON_RPC": f"http://HOST:PORT/{name}/rpc",
            "SPRECON_ROOT": "<your own fresh dir per scenario>",
        },
        "opening_balance": str(twin.opening),
        "baseline_block": 1,
    }
    if twin.prov_dir:
        doc["env"]["SPRECON_PROV"] = str(twin.prov_dir)
        doc["billing_file"] = f"GET /{name}/billing, save to a file, "\
                              f"pass --billing-file"
    for attr in ("drain_tx", "drain_amount", "late_tx", "late_amount",
                 "failed_tx", "stuck_tx", "reorg_tx", "deposit_tx",
                 "deposit_amount", "foreign_id", "residue_str"):
        if hasattr(twin, attr):
            doc[attr] = str(getattr(twin, attr))
    if name == "deposit":
        doc["attestation_record"] = {
            "ts": twin.now.isoformat(), "amount": "25",
            "tx": twin.deposit_tx, "attestor": "owner"}
    return doc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8454)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    scenarios = _build()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, doc) -> None:
            body = json.dumps(doc, indent=1, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # quiet
            pass

        def do_GET(self):
            parts = [p for p in self.path.split("/") if p]
            if not parts:
                return self._send(200, {
                    "scenarios": {n: _info(n, s)
                                  for n, s in scenarios.items()},
                    "rpc": "POST /<scenario>/rpc (JSON-RPC 2.0)",
                    "tamper": "POST /tamper/arm after your first pass",
                })
            name = parts[0]
            if name not in scenarios:
                return self._send(404, {"error": f"no scenario {name}"})
            if len(parts) > 1 and parts[1] == "billing":
                st = scenarios[name]["twin"].statement
                if st is None:
                    return self._send(404, {"error": "not a billing "
                                                     "scenario"})
                return self._send(200, st)
            return self._send(200, _info(name, scenarios[name]))

        def do_POST(self):
            parts = [p for p in self.path.split("/") if p]
            if len(parts) == 2 and parts == ["tamper", "arm"]:
                twin = scenarios["tamper"]["twin"]
                twin._spends[1] = twin._settled(
                    "p1", Decimal("0.01"), twin.clean_tx)
                twin._write_spends()
                return self._send(200, {"armed": "the settled line in "
                                        "spend.log now says 0.01 — the "
                                        "past changed"})
            if len(parts) != 2 or parts[1] != "rpc" or parts[0] not in scenarios:
                return self._send(404, {"error": "POST /<scenario>/rpc"})
            scn = scenarios[parts[0]]
            twin = scn["twin"]
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length))
            if (twin.chain.fault == "rpc-flap" and not scn["flapped"]
                    and req.get("method") == "eth_getLogs"):
                scn["flapped"] = True
                return self._send(503, {"error": "rpc-flap: scripted "
                                                 "transient"})
            try:
                result = _rpc(scn, req["method"], req.get("params", []))
            except ValueError as e:
                return self._send(200, {"jsonrpc": "2.0",
                                        "id": req.get("id"),
                                        "error": {"code": -32601,
                                                  "message": str(e)}})
            return self._send(200, {"jsonrpc": "2.0",
                                    "id": req.get("id"),
                                    "result": result})

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"sprc test chain on http://{args.host}:{args.port}/ — "
          f"GET / for the scenario index", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
