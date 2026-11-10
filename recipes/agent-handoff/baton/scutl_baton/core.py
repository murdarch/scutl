"""Baton: every handoff tool (bt_status / bt_pack / bt_release /
bt_stage / bt_commit / bt_recover) maps to one method here.

Design record (recipe baton rev 1, cst-fu8y): succession is a CEREMONY,
not a note —

  pack     walks the REGISTERED estate (entries are declared, never
           discovered), runs every probe to record the as-packaged
           baseline, writes the content-addressed manifest, and
           journals 'packed' with its sha256. Refuses in code to
           serialize any value matching a registered secret digest.
  release  the predecessor's last act: journals 'released' binding the
           manifest digest handed over, and drops the lease.
  stage    the successor's first act: verifies the manifest bytes
           against the digest carried OUT-OF-BAND, checks freshness,
           runs EVERY probe, and files the verification report —
           MATCH / DRIFT / UNREACHABLE per entry, each verdict built
           from the probe's actual output digest, never from the
           manifest's testimony. Journals 'staged' with the report's
           sha256. Read-only against the estate.
  commit   takes the estate: against a released manifest with a
           complete stage report — or a contested one plus the owner's
           consumable takeover token. Refuses while any entry reads
           UNREACHABLE absent a journaled per-entry override. Journals
           commit-started, writes the lease, journals committed.
  recover  reads the journal and finishes what a crash left: an
           unfinished stage simply re-stages (probes are read-only and
           idempotent); 'commit-started' without a committed entry
           completes or unwinds the lease write by comparing digests.

The journal (takeover.log, append-only JSONL) is the single arbiter of
who holds the estate. Entries carry digests only — safe to read,
quote, and back up.

Honest-scope note, carried from every scutl approvals module: the
token gate is an out-of-band consent channel, not a cryptographic
barrier. What IS enforced in code with no consent channel involved:
secrets never ride the package, the report is built from probe
outputs, commit never binds to bytes the journal did not record, and
the lease is never left in a state the journal cannot explain.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ESTATE_NAME = "estate.json"
MANIFEST_NAME = "manifest.json"
REPORT_NAME = "report.json"
JOURNAL_NAME = "takeover.log"
LEASE_NAME = "lease.json"
INBOX_NAME = "baton-inbox.json"
TOKEN_TAKEOVER = "takeover.token"
TOKEN_OVERRIDE = "override.token"

CLASSES = ("service", "money", "obligation")
VERDICTS = ("MATCH", "DRIFT", "UNREACHABLE")


class AuthorityRequired(Exception):
    """exit 4 — commit lacked authority (no release and no token)."""

    def __init__(self, detail: str):
        super().__init__(
            f"takeover requires authority: {detail} — create the token "
            f"with 'baton-approve takeover' (out of band), then retry")


class DigestMismatch(Exception):
    """exit 5 — manifest or report bytes failed their digest check."""


class UnreachableBlocked(Exception):
    """exit 6 — UNREACHABLE entries block commit absent an override."""

    def __init__(self, entry_ids: list[str]):
        super().__init__(
            "UNREACHABLE entries block commit (override per entry, out "
            "of band, or repair the probe surface): "
            + ", ".join(sorted(entry_ids)))
        self.entry_ids = entry_ids


class ForeignLease(Exception):
    """Someone else holds the estate; contesting in code is forbidden."""


class SecretLeakRefused(Exception):
    """A value matching a registered secret digest reached the packager."""


class SimulatedCrash(Exception):
    """Test hook: the process 'dies' at a named crash point."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(p: Path) -> str:
    return _sha256_bytes(p.read_bytes())


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)


class Baton:
    """One estate, one state dir, one journal."""

    def __init__(self, state_dir: str | Path, actor: str = "agent",
                 crash_point: str | None = None):
        self.dir = Path(state_dir).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.actor = actor
        self._crash_point = crash_point
        self.journal_path = self.dir / JOURNAL_NAME
        self.manifest_path = self.dir / MANIFEST_NAME
        self.report_path = self.dir / REPORT_NAME
        self.lease_path = self.dir / LEASE_NAME

    # -- plumbing ------------------------------------------------------
    def _maybe_crash(self, point: str):
        if self._crash_point == point:
            raise SimulatedCrash(point)

    def _journal(self, event: str, **fields):
        entry = {"at": _now(), "event": event, **fields}
        with open(self.journal_path, "a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")

    def _entries(self) -> list[dict]:
        if not self.journal_path.exists():
            return []
        return [json.loads(line) for line
                in self.journal_path.read_text().splitlines() if line]

    def _estate(self) -> dict:
        p = self.dir / ESTATE_NAME
        if not p.exists():
            return {"entries": [], "secret_digests": []}
        return json.loads(p.read_text())

    def _consume_token(self, name: str) -> bool:
        p = self.dir / name
        if not p.exists():
            return False
        p.unlink()
        return True

    # -- probes ----------------------------------------------------------
    # A probe runs against the estate's own read-only truth surface and
    # returns (observed, reachable). The VERDICT is made elsewhere, by
    # comparing the observed-output digest against the packed baseline:
    # the report is built from what the probe SAW, never from testimony.
    def _probe(self, spec: dict) -> tuple[str | None, bool]:
        kind = spec.get("kind")
        try:
            if kind == "file":
                p = Path(spec["path"]).expanduser()
                if not p.exists():
                    return ("absent", True)
                return ("sha256:" + _sha256_file(p), True)
            if kind == "command":
                out = subprocess.run(
                    spec["argv"], capture_output=True, timeout=30)
                return (f"exit:{out.returncode} "
                        f"stdout_sha256:{_sha256_bytes(out.stdout)}", True)
            return (None, False)       # unknown probe kind: unreachable
        except (OSError, subprocess.SubprocessError, PermissionError):
            return (None, False)

    def _baseline_entry(self, entry: dict) -> dict:
        observed, reachable = self._probe(entry["probe"])
        rec = {k: entry[k] for k in
               ("id", "class", "probe", "invariant") if k in entry}
        if entry.get("deliberate"):
            rec["deliberate"] = entry["deliberate"]
        if entry.get("due"):
            rec["due"] = entry["due"]
        rec["baseline"] = {
            "reachable": reachable,
            "observed_sha256": (_sha256_bytes(observed.encode())
                                if observed is not None else None),
        }
        return rec

    # -- bt_status -------------------------------------------------------
    def status(self) -> dict:
        lease = (json.loads(self.lease_path.read_text())
                 if self.lease_path.exists() else None)
        manifest = (json.loads(self.manifest_path.read_text())
                    if self.manifest_path.exists() else None)
        report = (json.loads(self.report_path.read_text())
                  if self.report_path.exists() else None)
        inbox_p = self.dir / INBOX_NAME
        inbox = json.loads(inbox_p.read_text()) if inbox_p.exists() else []
        counts: dict[str, int] = {}
        for e in self._estate()["entries"]:
            counts[e["class"]] = counts.get(e["class"], 0) + 1
        out = {
            "lease_holder": lease["holder"] if lease else None,
            "entry_counts": counts,
            "journal_tail": self._entries()[-5:],
            "inbox": inbox,
        }
        if manifest:
            age_h = (time.time() - manifest["packed_epoch"]) / 3600
            out["manifest_sha256"] = _sha256_file(self.manifest_path)
            out["manifest_age_hours"] = round(age_h, 1)
        if report:
            verdicts: dict[str, int] = {}
            for r in report["entries"]:
                verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
            out["last_report"] = {
                "manifest_sha256": report["manifest_sha256"],
                "verdicts": verdicts}
        return out

    # -- bt_pack -----------------------------------------------------------
    def pack(self) -> dict:
        estate = self._estate()
        if not estate["entries"]:
            raise ValueError("no registered estate: declare entries "
                             "before packing — an inventory nobody "
                             "signed is a claim nobody owns")
        records = [self._baseline_entry(e) for e in estate["entries"]]
        manifest = {
            "packed_at": _now(),
            "packed_epoch": time.time(),
            "packed_by": self.actor,
            "freshness_hours": estate.get("freshness_hours", 72),
            "entries": records,
        }
        # the guard is the code: no manifest string may hash to a
        # registered secret digest — digests and rail locations travel,
        # values never do.
        secret_digests = set(estate.get("secret_digests", []))
        for s in _walk_strings(manifest):
            if _sha256_bytes(s.encode()) in secret_digests:
                raise SecretLeakRefused(
                    "a value matching a registered secret digest "
                    "reached the packager; secrets move on their own "
                    "rails, never in the baton")
        blob = _canon(manifest)
        self._maybe_crash("before-manifest-write")
        self.manifest_path.write_bytes(blob)
        digest = _sha256_bytes(blob)
        self._journal("packed", manifest_sha256=digest, by=self.actor)
        return {"packed": True, "manifest_sha256": digest,
                "entries": len(records)}

    # -- bt_release ----------------------------------------------------------
    def release(self) -> dict:
        if not self.manifest_path.exists():
            raise ValueError("nothing packed; release binds a manifest "
                             "digest and there is none")
        lease = (json.loads(self.lease_path.read_text())
                 if self.lease_path.exists() else None)
        if lease and lease["holder"] != self.actor:
            raise ForeignLease(
                f"lease held by '{lease['holder']}'; only the holder "
                f"releases — report, never contest in code")
        digest = _sha256_file(self.manifest_path)
        self._journal("released", manifest_sha256=digest, by=self.actor)
        self._maybe_crash("after-journal-released")
        if self.lease_path.exists():
            self.lease_path.unlink()
        return {"released": True, "manifest_sha256": digest,
                "note": "carry this digest out-of-band; the successor "
                        "verifies bytes it did not choose"}

    # -- bt_stage -------------------------------------------------------------
    def stage(self, expected_manifest_sha256: str) -> dict:
        if not self.manifest_path.exists():
            raise DigestMismatch("no manifest present to stage against")
        blob = self.manifest_path.read_bytes()
        actual = _sha256_bytes(blob)
        if actual != expected_manifest_sha256:
            raise DigestMismatch(
                f"manifest bytes hash to {actual}, not the out-of-band "
                f"digest {expected_manifest_sha256} — the package is "
                f"not what was handed over")
        manifest = json.loads(blob)
        age_h = (time.time() - manifest["packed_epoch"]) / 3600
        stale = age_h > manifest["freshness_hours"]
        results = []
        for rec in manifest["entries"]:
            observed, reachable = self._probe(rec["probe"])
            if not reachable:
                verdict = "UNREACHABLE"
                obs_digest = None
            else:
                obs_digest = _sha256_bytes(observed.encode())
                verdict = ("MATCH" if obs_digest ==
                           rec["baseline"]["observed_sha256"] else "DRIFT")
            row = {"id": rec["id"], "class": rec["class"],
                   "verdict": verdict, "observed_sha256": obs_digest,
                   "baseline_sha256": rec["baseline"]["observed_sha256"]}
            if rec.get("deliberate"):
                # a deliberate state is load-bearing: matching its
                # packed (possibly 'down') baseline IS the match, and
                # the report says so out loud.
                row["deliberate"] = rec["deliberate"]
            results.append(row)
        self._maybe_crash("mid-stage")
        report = {
            "staged_at": _now(),
            "staged_by": self.actor,
            "manifest_sha256": actual,
            "manifest_stale": stale,
            "manifest_age_hours": round(age_h, 1),
            "entries": results,
        }
        rblob = _canon(report)
        self.report_path.write_bytes(rblob)
        rdigest = _sha256_bytes(rblob)
        self._journal("staged", manifest_sha256=actual,
                      report_sha256=rdigest, by=self.actor)
        verdicts: dict[str, int] = {}
        for r in results:
            verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        return {"staged": True, "manifest_sha256": actual,
                "report_sha256": rdigest, "manifest_stale": stale,
                "verdicts": verdicts,
                "drift": [r["id"] for r in results
                          if r["verdict"] == "DRIFT"],
                "unreachable": [r["id"] for r in results
                                if r["verdict"] == "UNREACHABLE"]}

    # -- overrides (out-of-band, journaled) ---------------------------------
    def override(self, entry_id: str, reason: str) -> dict:
        """Per-entry UNREACHABLE override. Consumes the owner's
        override token — one token, one entry — and journals it."""
        if not self._consume_token(TOKEN_OVERRIDE):
            raise AuthorityRequired(
                f"override of '{entry_id}' needs the owner's token")
        self._journal("override", entry=entry_id, reason=reason,
                      by=self.actor)
        return {"overridden": entry_id, "reason": reason}

    # -- bt_commit --------------------------------------------------------------
    def commit(self) -> dict:
        if not self.report_path.exists():
            raise DigestMismatch("no stage report; probe before you act")
        rblob = self.report_path.read_bytes()
        rdigest = _sha256_bytes(rblob)
        entries = self._entries()
        staged = [e for e in entries if e["event"] == "staged"]
        if not staged or staged[-1]["report_sha256"] != rdigest:
            raise DigestMismatch(
                "stage report does not hash to what the journal "
                "recorded at staging time — tampered after stage")
        report = json.loads(rblob)
        mdigest = report["manifest_sha256"]
        if (not self.manifest_path.exists()
                or _sha256_file(self.manifest_path) != mdigest):
            raise DigestMismatch(
                "manifest changed since stage — re-stage against the "
                "out-of-band digest")
        lease = (json.loads(self.lease_path.read_text())
                 if self.lease_path.exists() else None)
        if lease and lease["holder"] != self.actor:
            raise ForeignLease(
                f"lease held by '{lease['holder']}'; a live foreign "
                f"lease is a report, never a contest")
        # UNREACHABLE blocks, minus journaled per-entry overrides
        overridden = {e["entry"] for e in entries
                      if e["event"] == "override"}
        blocked = [r["id"] for r in report["entries"]
                   if r["verdict"] == "UNREACHABLE"
                   and r["id"] not in overridden]
        if blocked:
            raise UnreachableBlocked(blocked)
        # authority: a release binding these bytes, or the owner's token
        released = [e for e in entries if e["event"] == "released"]
        clean = bool(released and
                     released[-1]["manifest_sha256"] == mdigest)
        authority = "release"
        if not clean:
            if not self._consume_token(TOKEN_TAKEOVER):
                raise AuthorityRequired(
                    "estate is CONTESTED (no release entry binds this "
                    "manifest digest) and no takeover token is present; "
                    "escalate with the stage report — urgency is never "
                    "authority")
            authority = "owner-token"
        self._journal("commit-started", manifest_sha256=mdigest,
                      report_sha256=rdigest, authority=authority,
                      by=self.actor)
        self._maybe_crash("mid-commit")
        self.lease_path.write_text(json.dumps(
            {"holder": self.actor, "since": _now(),
             "manifest_sha256": mdigest}, sort_keys=True))
        self._journal("committed", manifest_sha256=mdigest,
                      holder=self.actor, authority=authority)
        return {"committed": True, "holder": self.actor,
                "manifest_sha256": mdigest, "authority": authority}

    # -- bt_recover ---------------------------------------------------------------
    def recover(self) -> dict:
        """Finish what a crash left, from the journal alone. An
        unfinished stage re-stages (probes are read-only and
        idempotent). 'commit-started' with no 'committed' completes
        the lease write when the filed report still hashes to what the
        journal recorded — or unwinds it when it does not. Ungated:
        recovery completes or unwinds an already-authorized act, it
        authorizes nothing new."""
        entries = self._entries()
        if not entries:
            return {"recovered": False, "state": "idle",
                    "note": "journal empty; nothing in flight"}
        last = entries[-1]
        if last["event"] in ("packed", "released", "staged",
                             "committed", "override", "unwound"):
            return {"recovered": False, "state": last["event"],
                    "note": "journal ends on a terminal entry; "
                            "nothing in flight"}
        if last["event"] == "commit-started":
            ok = (self.report_path.exists()
                  and _sha256_file(self.report_path)
                  == last["report_sha256"]
                  and self.manifest_path.exists()
                  and _sha256_file(self.manifest_path)
                  == last["manifest_sha256"])
            if ok:
                self.lease_path.write_text(json.dumps(
                    {"holder": last["by"], "since": _now(),
                     "manifest_sha256": last["manifest_sha256"]},
                    sort_keys=True))
                self._journal("committed",
                              manifest_sha256=last["manifest_sha256"],
                              holder=last["by"],
                              authority=last["authority"],
                              recovered=True)
                return {"recovered": True, "action": "completed",
                        "holder": last["by"],
                        "note": "lease write finished from the "
                                "journal's digests"}
            if self.lease_path.exists():
                self.lease_path.unlink()
            self._journal("unwound",
                          manifest_sha256=last["manifest_sha256"],
                          reason="bytes no longer match the journaled "
                                 "digests; the half-done takeover is "
                                 "unwound, re-stage from the "
                                 "out-of-band digest")
            return {"recovered": True, "action": "unwound",
                    "note": "report or manifest drifted during the "
                            "crash window; takeover unwound, nothing "
                            "committed"}
        return {"recovered": False, "state": last["event"],
                "note": f"journal ends on '{last['event']}' which "
                        f"names no in-flight work; if the estate "
                        f"surface disagrees, that is an escalation, "
                        f"never a guess"}
