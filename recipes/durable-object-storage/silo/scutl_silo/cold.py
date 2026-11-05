"""Cold target: the Archive-tier second home (cst-px98.2, rev 2).

The contract change Archive forces, absorbed without lying in either
direction (design ratified in the 2026-09-16 draft on the bead):

  - Standard fetches synchronously, so rev-1's rehearsal vocabulary is
    binary: green or red. Archive restores are explicit + ASYNC, so a
    rehearsal that starts and cannot yet fetch is NEITHER. Calling it
    green is green-washing; calling it red trains alarm-deafness. It
    is a THIRD state: PENDING, with its age quoted everywhere.
  - Phase A (this invocation): try the fetch; where the archive says
    'still retrieving', issue/refresh the restore request and append a
    pending line — per-key, with the FIRST requested-at preserved so a
    stuck restore ages honestly across invocations.
  - Phase B (a later invocation of the same op): fetch, re-hash
    against the SAME manifest (one manifest, two homes), convert each
    pending to green/red.
  - A pending older than the ratified restore_timeout_hours is a
    breach in its own name — restore-overdue — distinct from red
    (bytes wrong) and from rehearsal-overdue (never started). That
    wall lives in config['cold'] and is owner-set (configure-cold).

The cold copy is a SECOND target with its own inventory (cold-put
manifest events), written under the same append-only/digest walls:
mirrored bytes are re-hashed on read-back while still hot, and nothing
here trusts a storage-class header over a digest.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from .store import MissingObject, RestorePending, StoreUnreachable

COLD_WALLS = ("restore_timeout_hours", "restore_days")


def cold_config(config: dict) -> dict | None:
    """The ratified cold block, or None when rev-2 is not enabled."""
    cold = config.get("cold")
    if not cold:
        return None
    missing = [w for w in COLD_WALLS if w not in cold]
    if missing:
        raise ValueError(
            f"cold target unratified: missing {', '.join(missing)} — "
            f"run 'silo admin configure-cold' (owner act)")
    return cold


def store_chunks(entry: dict) -> list[dict]:
    return entry.get("chunks") or [
        {"key": entry["key"], "sha256": entry["sha256"],
         "size": entry["size"]}]


def cold_inventory(manifest: list[dict]) -> dict[str, dict]:
    """Store-keys the manifest says reached the cold target."""
    out: dict[str, dict] = {}
    for e in manifest:
        if e.get("event") == "cold-put":
            for c in e.get("chunks_cold", ()):
                out[c["key"]] = c
        elif e.get("event") == "delete":
            # a consented delete removes the logical key everywhere
            for c in store_chunks(e) if "sha256" in e else ():
                out.pop(c["key"], None)
    return out


def pending_since(cold_records: list[dict]) -> dict[str, str]:
    """key -> ISO ts of the FIRST unresolved pending. Later records
    resolve a key (fetched green or red) or re-pend it; re-pending
    never resets the clock — a stuck restore must age, not reset."""
    since: dict[str, str] = {}
    for rec in cold_records:
        for key in rec.get("resolved", ()):
            since.pop(key, None)
        for p in rec.get("pending", ()):
            since.setdefault(p["key"], p.get("since", rec["ts"]))
    return since


def rehearse_cold(store, manifest: list[dict], inventory: dict[str, dict],
                  cold_records: list[dict], cold: dict,
                  now: datetime) -> dict:
    """One invocation of the two-phase spine. Pure over its inputs
    except the store calls; the caller appends the returned record."""
    prior_pending = pending_since(cold_records)
    mirrored = cold_inventory(manifest)
    mismatches: list[dict] = []
    pending: list[dict] = []
    resolved: list[str] = []
    never_mirrored: list[str] = []
    fetched = 0

    try:
        for lkey, entry in sorted(inventory.items()):
            for c in store_chunks(entry):
                key = c["key"]
                if key not in mirrored:
                    # absence is drift, not a fetch problem: the put
                    # never reached the cold home (or its event did
                    # not survive) — named separately from red
                    never_mirrored.append(key)
                    continue
                try:
                    part = store.get(key)
                except RestorePending:
                    first = prior_pending.get(key)
                    if first is None:
                        req = store.request_restore(
                            key, int(cold["restore_days"]))
                        pending.append({"key": key,
                                        "since": now.isoformat(),
                                        "request_http": req.get("http")})
                    else:
                        pending.append({"key": key, "since": first,
                                        "request_http": None})
                    continue
                except MissingObject:
                    mismatches.append({"key": key, "problem": "missing"})
                    if key in prior_pending:
                        resolved.append(key)
                    continue
                fetched += 1
                got = hashlib.sha256(part).hexdigest()
                if got != c["sha256"]:
                    mismatches.append({
                        "key": key, "problem": "digest",
                        "expected": c["sha256"], "actual": got})
                if key in prior_pending:
                    resolved.append(key)
    except StoreUnreachable as e:
        return {"ts": now.isoformat(), "target": "cold",
                "outcome": "unreachable", "why": str(e)[:200],
                "objects": len(inventory)}

    outcome = ("red" if mismatches or never_mirrored
               else "pending" if pending else "green")
    record = {"ts": now.isoformat(), "target": "cold",
              "outcome": outcome, "objects": len(inventory),
              "fetched": fetched, "mismatches": mismatches,
              "pending": pending, "resolved": resolved,
              "never_mirrored": never_mirrored}
    return record


def pending_breaches(cold_records: list[dict], cold: dict,
                     now: datetime) -> list[str]:
    """restore-overdue, by name: pendings older than the ratified
    restore_timeout_hours. Quoted in hours so a stuck restore
    surfaces in days, not at the next disaster."""
    out: list[str] = []
    timeout_h = float(cold["restore_timeout_hours"])
    for key, since in sorted(pending_since(cold_records).items()):
        age_h = (now - datetime.fromisoformat(since)
                 ).total_seconds() / 3600
        if age_h > timeout_h:
            out.append(
                f"restore-overdue: '{key}' has been pending "
                f"{age_h:.1f}h, restore_timeout_hours={timeout_h:g} — "
                f"the archive accepted a restore it has not delivered; "
                f"this is a provider fault to escalate, not a red "
                f"(bytes unproven, not disproven) and not a wait")
    return out
