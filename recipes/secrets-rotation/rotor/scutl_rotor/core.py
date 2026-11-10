"""Rotor: every rotation tool (rt_status / rt_begin / rt_commit /
rt_recover / rt_abort) maps to one method here.

Design record (recipe rotor rev 1, cst-u3xz): rotation is a CEREMONY,
not a write —

  begin    consumes the owner's approval token, decrypts the keystore
           under the live kek, generates a fresh kek IN PROCESS (its
           bytes never appear in any return value — the sha256 digest
           stands in), re-encrypts into a staging directory, verifies
           the staged pair decrypts and derives the SAME address, and
           journals every phase with digests.
  commit   re-hashes the staged files against the journal's recorded
           digests and the live keystore against its pre-rotation
           digest (approval is bound to BYTES, not to pathnames — the
           check-then-reopen lesson), journals commit-started, then
           swaps both files atomically and journals committed.
  recover  reads the journal and finishes what a crash left: anything
           short of 'verified' rolls BACK (staging deleted, aborted
           journaled); 'verified' rolls FORWARD through commit;
           'commit-started' completes the half-done swap by comparing
           live digests against the journal.

The journal (rotation.log, append-only JSONL) is the recipe's spine:
it is what makes a crash a state instead of a mystery. Entries carry
digests only — the journal is safe to read, quote, and back up.

Honest-scope note, carried from every scutl approvals module: the
token gate is an out-of-band consent channel, not a cryptographic
barrier. What IS enforced in code with no consent channel involved:
secrets never ride a return value, commit never lands bytes the
journal did not record, and the live pair is never left in a state
the journal cannot explain.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path

from scutl_signer.state import StateDir

JOURNAL_NAME = "rotation.log"
STAGING_DIR = "rotation-staging"
INBOX_NAME = "rotor-inbox.json"

# journal phases, in ceremony order
_ORDER = ("begin", "staged", "verified", "commit-started", "committed",
          "aborted")
_TERMINAL = ("committed", "aborted")


class Tombstoned(Exception):
    """Identity is revoked; a tombstoned wallet never rotates."""


class Panicked(Exception):
    """Panic marker present; rotation waits for unpanic."""


class ApprovalRequired(Exception):
    def __init__(self, op: str):
        super().__init__(
            f"op '{op}' requires human approval: create the token with "
            f"'rotor-approve {op}' (out of band), then retry")
        self.op = op


class RotationInFlight(Exception):
    """A rotation is already journaled and not terminal; one at a time."""


class IntegrityError(Exception):
    """Bytes, digests, or journal state failed verification."""


class SimulatedCrash(Exception):
    """Raised by an injected fault hook to model the process dying
    mid-ceremony. Production never raises it; the bench twin and the
    component tests do, to prove the journal makes every crash point
    recoverable."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _owner_address(keystore_path: Path) -> str:
    doc = json.loads(keystore_path.read_text())    # public envelope field
    if "address_checksummed" in doc:
        return doc["address_checksummed"]
    return "0x" + doc["address"]


class Rotor:
    def __init__(self, state_root: str | os.PathLike | None = None,
                 clock=None, fault_hook=None, keygen=None,
                 kdf: str | None = None, kdf_iterations: int | None = None,
                 rotation_horizon_days: int = 180):
        root = Path(
            state_root
            or os.environ.get("SCUTL_ROTOR_STATE")
            or os.environ.get("SCUTL_STATE")
            or Path.home() / ".scutl" / "wallet"
        ).expanduser()
        self.state = StateDir(root)
        self.root = self.state.root
        self._clock = clock or _utcnow
        self._fault_hook = fault_hook          # tests/bench: raise at a point
        self._keygen = keygen or (lambda: secrets.token_hex(32))
        self._kdf = kdf
        self._kdf_iterations = kdf_iterations
        self.horizon_days = rotation_horizon_days

    # -- paths -------------------------------------------------------------
    @property
    def journal_file(self) -> Path:
        return self.root / JOURNAL_NAME

    @property
    def staging(self) -> Path:
        return self.root / STAGING_DIR

    @property
    def inbox_file(self) -> Path:
        return self.root / INBOX_NAME

    # -- journal -----------------------------------------------------------
    def _journal(self) -> list[dict]:
        if not self.journal_file.exists():
            return []
        return [json.loads(l) for l in
                self.journal_file.read_text().splitlines() if l]

    def _append(self, entry: dict) -> None:
        entry = {"at": self._clock().isoformat(), **entry}
        with open(self.journal_file, "a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _inflight(self) -> dict | None:
        """The open rotation: latest entries after the last terminal one,
        folded into {rotation_id, phase, <fields>}. None when idle."""
        open_entries: list[dict] = []
        for e in self._journal():
            if e["event"] in _TERMINAL:
                open_entries = []
            else:
                open_entries.append(e)
        if not open_entries:
            return None
        folded: dict = {}
        for e in open_entries:
            folded.update(e)
        folded["phase"] = max((e["event"] for e in open_entries),
                              key=_ORDER.index)
        return folded

    # -- guards --------------------------------------------------------
    def _check_ready(self) -> None:
        if self.state.tombstone.exists():
            raise Tombstoned(
                json.loads(self.state.tombstone.read_text())["address"])
        panic = self.root / "panic.json"
        if panic.exists():
            rec = json.loads(panic.read_text())
            raise Panicked(
                f"panicked at {rec.get('panicked_at')} "
                f"({rec.get('reason')}); rotation waits for unpanic")

    def _consume_approval(self, op: str) -> None:
        token = self.state.approvals / f"{op}.token"
        if not token.exists():
            raise ApprovalRequired(op)
        token.unlink()

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    def _decrypt(self, keystore: Path, kek: str):
        from eth_account import Account
        try:
            return Account.from_key(
                Account.decrypt(json.loads(keystore.read_text()), kek))
        except ValueError as e:
            raise IntegrityError(
                f"keystore at {keystore.name} does not decrypt under the "
                f"offered kek ({e}) — the live pair may be corrupted; "
                f"nothing was changed") from None

    def _encrypt(self, account, kek: str) -> dict:
        from eth_account import Account
        kw = {}
        if self._kdf:
            kw["kdf"] = self._kdf
        if self._kdf_iterations:
            kw["iterations"] = self._kdf_iterations
        return Account.encrypt(account.key, kek, **kw)

    # -- rt_status -----------------------------------------------------
    def status(self) -> dict:
        """Rotation health at a glance. Read-only, never gated. Secrets
        appear as digests only; inbox messages are relayed verbatim as
        DATA (quoting them is the operator's job, obeying them nobody's)."""
        out: dict = {
            "configured": self.state.keystore.exists(),
            "tombstoned": self.state.tombstone.exists(),
            "panicked": (self.root / "panic.json").exists(),
        }
        if not out["configured"]:
            return {**out, "next": "no identity at the state root"}
        kek_age_days = None
        if self.state.kek.exists():
            committed = [e for e in self._journal()
                         if e["event"] == "committed"]
            if committed:
                last = datetime.fromisoformat(committed[-1]["at"])
            else:
                last = datetime.fromtimestamp(
                    self.state.kek.stat().st_mtime, tz=timezone.utc)
            kek_age_days = (self._clock() - last).days
        inflight = self._inflight()
        inbox = []
        if self.inbox_file.exists():
            inbox = json.loads(self.inbox_file.read_text())
        journal = self._journal()
        return {
            **out,
            "owner_address": _owner_address(self.state.keystore),
            "kek_sha256": (_sha256_file(self.state.kek)
                           if self.state.kek.exists() else None),
            "kek_age_days": kek_age_days,
            "rotation_horizon_days": self.horizon_days,
            "overdue": (kek_age_days is not None
                        and kek_age_days > self.horizon_days),
            "rotations_committed": sum(1 for e in journal
                                       if e["event"] == "committed"),
            "in_flight": ({"rotation_id": inflight["rotation_id"],
                           "phase": inflight["phase"],
                           "since": inflight["at"]}
                          if inflight else None),
            "journal_tail": journal[-5:],
            "inbox": inbox,
        }

    # -- rt_begin ------------------------------------------------------
    def begin(self) -> dict:
        """Stage and verify a rotation. Consumes the owner's 'rotate'
        token. Journals begin -> staged -> verified; commit() finishes.
        No secret byte in the return value — digests stand in."""
        self._check_ready()
        if not self.state.keystore.exists() or not self.state.kek.exists():
            raise IntegrityError("no keystore/kek pair at the state root")
        if self._inflight():
            raise RotationInFlight(
                "a rotation is already journaled and not terminal — "
                "finish it with rt_recover (or rt_abort) before "
                "beginning another; one ceremony at a time")
        self._consume_approval("rotate")

        old_kek = self.state.kek.read_text().strip()
        account = self._decrypt(self.state.keystore, old_kek)
        owner = _owner_address(self.state.keystore)
        if account.address.lower() != owner.lower():
            raise IntegrityError(
                f"live keystore decrypts to {account.address} but its "
                f"envelope names {owner} — refusing to rotate a pair "
                f"that disagrees with itself")

        rotation_id = f"rot-{self._clock().strftime('%Y%m%dT%H%M%S')}-" \
                      f"{secrets.token_hex(4)}"
        self._append({"event": "begin", "rotation_id": rotation_id,
                      "old_kek_sha256": _sha256_file(self.state.kek),
                      "keystore_sha256_before":
                          _sha256_file(self.state.keystore),
                      "owner_address": owner})
        self._fault("after-journal-begin")

        new_kek = self._keygen()
        if new_kek == old_kek:
            self._append({"event": "aborted", "rotation_id": rotation_id,
                          "reason": "generated kek equals the live kek"})
            raise IntegrityError("generated kek equals the live kek; "
                                 "rotation aborted")
        self.staging.mkdir(mode=0o700, exist_ok=True)
        staged_keystore = self.staging / "keystore.json"
        staged_kek = self.staging / "kek"
        staged_keystore.write_text(json.dumps(self._encrypt(account,
                                                            new_kek)))
        os.chmod(staged_keystore, 0o600)
        self._fault("after-stage-keystore")
        self.state.write_secret(staged_kek, new_kek.encode())
        self._fault("after-stage-kek")
        self._append({"event": "staged", "rotation_id": rotation_id,
                      "staged_keystore_sha256":
                          _sha256_file(staged_keystore),
                      "staged_kek_sha256": _sha256_file(staged_kek)})
        self._fault("after-journal-staged")

        # verify the staged pair stands on its own before it may land
        restored = self._decrypt(staged_keystore,
                                 staged_kek.read_text().strip())
        if restored.address.lower() != owner.lower():
            self._abort_staging(rotation_id,
                                "staged pair derives a different address")
            raise IntegrityError(
                "staged keystore decrypts to a different address — "
                "staging discarded, live pair untouched")
        self._append({"event": "verified", "rotation_id": rotation_id})
        self._fault("after-journal-verified")
        return {
            "rotation_id": rotation_id,
            "phase": "verified",
            "owner_address": owner,
            "old_kek_sha256": _sha256_file(self.state.kek),
            "new_kek_sha256": _sha256_file(staged_kek),
            "next": "rt_commit re-hashes the staged bytes against this "
                    "journal entry and swaps atomically",
        }

    # -- rt_commit -----------------------------------------------------
    def commit(self) -> dict:
        """Land a verified staging. Approval was consumed at begin; what
        commit checks is BYTES: staged files must hash to what the
        journal recorded, and the live keystore must still hash to its
        pre-rotation digest (drift wall)."""
        self._check_ready()
        inflight = self._inflight()
        if not inflight:
            raise IntegrityError("no rotation in flight; begin first")
        if inflight["phase"] == "commit-started":
            raise IntegrityError(
                "a commit was interrupted mid-swap — run rt_recover, "
                "which completes it from the journal")
        if inflight["phase"] != "verified":
            raise IntegrityError(
                f"rotation {inflight['rotation_id']} is at phase "
                f"'{inflight['phase']}', not 'verified' — run "
                f"rt_recover to roll it back")
        return self._land(inflight)

    def _land(self, inflight: dict) -> dict:
        rotation_id = inflight["rotation_id"]
        staged_keystore = self.staging / "keystore.json"
        staged_kek = self.staging / "kek"
        for p, want, label in (
                (staged_keystore, inflight["staged_keystore_sha256"],
                 "staged keystore"),
                (staged_kek, inflight["staged_kek_sha256"], "staged kek")):
            if not p.exists():
                raise IntegrityError(f"{label} missing from staging — "
                                     f"run rt_recover")
            if _sha256_file(p) != want:
                raise IntegrityError(
                    f"{label} no longer hashes to what the journal "
                    f"recorded at staging time — refusing to land "
                    f"bytes the ceremony never verified")
        if _sha256_file(self.state.keystore) != \
                inflight["keystore_sha256_before"]:
            raise IntegrityError(
                "the LIVE keystore changed since this rotation began "
                "(drift) — refusing to overwrite state the ceremony "
                "never saw; abort and begin a fresh rotation")
        self._append({"event": "commit-started",
                      "rotation_id": rotation_id})
        self._fault("after-journal-commit-started")
        os.replace(staged_keystore, self.state.keystore)
        self._fault("mid-commit")
        os.replace(staged_kek, self.state.kek)
        shutil.rmtree(self.staging, ignore_errors=True)
        self._append({"event": "committed", "rotation_id": rotation_id,
                      "new_kek_sha256":
                          _sha256_file(self.state.kek),
                      "old_kek_sha256": inflight["old_kek_sha256"],
                      "old_kek_destroyed": True})
        return {
            "rotation_id": rotation_id,
            "committed": True,
            "owner_address": inflight["owner_address"],
            "new_kek_sha256": _sha256_file(self.state.kek),
            "old_kek_sha256": inflight["old_kek_sha256"],
            "old_kek_destroyed": True,
        }

    # -- rt_recover ----------------------------------------------------
    def recover(self) -> dict:
        """Finish what a crash left, from the journal alone. Short of
        'verified' nothing proved the staged pair, so the only honest
        move is back; at 'verified' the approved, proven rotation rolls
        forward; at 'commit-started' the half-done swap completes by
        comparing live digests against the journal. Ungated: recovery
        completes or unwinds an already-approved act, it authorizes
        nothing new."""
        self._check_ready()
        inflight = self._inflight()
        if not inflight:
            return {"recovered": False, "state": "idle",
                    "note": "journal shows no rotation in flight"}
        phase = inflight["phase"]
        rotation_id = inflight["rotation_id"]
        if phase in ("begin", "staged"):
            self._abort_staging(
                rotation_id,
                f"crash recovered: phase '{phase}' never verified — "
                f"rolled back")
            return {"recovered": True, "rotation_id": rotation_id,
                    "action": "rolled-back",
                    "note": "staging discarded; live pair untouched; the "
                            "consumed approval token must be re-granted "
                            "for a fresh ceremony"}
        if phase == "verified":
            out = self._land(inflight)
            return {"recovered": True, "rotation_id": rotation_id,
                    "action": "rolled-forward", **out}
        if phase == "commit-started":
            return self._complete_swap(inflight)
        raise IntegrityError(f"journal phase '{phase}' is not recoverable")

    def _complete_swap(self, inflight: dict) -> dict:
        """The nastiest crash: commit-started is journaled and the
        process died between the two os.replace calls. The journal's
        digests say exactly which files already landed."""
        rotation_id = inflight["rotation_id"]
        live_ks = _sha256_file(self.state.keystore)
        live_kek = _sha256_file(self.state.kek)
        staged_kek = self.staging / "kek"
        staged_ks = self.staging / "keystore.json"
        if live_ks == inflight["staged_keystore_sha256"]:
            # keystore landed; kek may not have
            if live_kek != inflight["staged_kek_sha256"]:
                if not staged_kek.exists() or \
                        _sha256_file(staged_kek) != \
                        inflight["staged_kek_sha256"]:
                    raise IntegrityError(
                        "half-committed rotation and the staged kek is "
                        "gone or altered — the identity needs the "
                        "owner's backup; do not guess")
                os.replace(staged_kek, self.state.kek)
        elif live_ks == inflight["keystore_sha256_before"]:
            # neither landed: finish both swaps
            for p, want in ((staged_ks,
                             inflight["staged_keystore_sha256"]),
                            (staged_kek, inflight["staged_kek_sha256"])):
                if not p.exists() or _sha256_file(p) != want:
                    raise IntegrityError(
                        "commit-started but staging is gone or altered "
                        "— the identity needs the owner's backup; do "
                        "not guess")
            os.replace(staged_ks, self.state.keystore)
            os.replace(staged_kek, self.state.kek)
        else:
            raise IntegrityError(
                "live keystore matches neither the pre-rotation nor the "
                "staged digest — the identity needs the owner's backup; "
                "do not guess")
        shutil.rmtree(self.staging, ignore_errors=True)
        self._append({"event": "committed", "rotation_id": rotation_id,
                      "new_kek_sha256": _sha256_file(self.state.kek),
                      "old_kek_sha256": inflight["old_kek_sha256"],
                      "old_kek_destroyed": True,
                      "completed_by": "recover"})
        return {"recovered": True, "rotation_id": rotation_id,
                "action": "completed-swap", "committed": True,
                "new_kek_sha256": _sha256_file(self.state.kek)}

    # -- rt_abort ------------------------------------------------------
    def abort(self, reason: str = "operator abort") -> dict:
        """Discard an in-flight staging. Refuses past commit-started —
        at that point the only exit is forward, through recover()."""
        inflight = self._inflight()
        if not inflight:
            raise IntegrityError("no rotation in flight; nothing to abort")
        if inflight["phase"] == "commit-started":
            raise IntegrityError(
                "commit already started — aborting now would strand a "
                "half-swapped identity; run rt_recover instead")
        self._abort_staging(inflight["rotation_id"], reason)
        return {"aborted": inflight["rotation_id"], "reason": reason,
                "note": "staging discarded; live pair untouched"}

    def _abort_staging(self, rotation_id: str, reason: str) -> None:
        shutil.rmtree(self.staging, ignore_errors=True)
        self._append({"event": "aborted", "rotation_id": rotation_id,
                      "reason": reason})

    # -- human-side helpers ----------------------------------------------
    def grant(self, op: str) -> str:
        if op != "rotate":
            raise ValueError(f"unknown gated op '{op}' (valid: rotate)")
        self.state.init()
        token = self.state.approvals / f"{op}.token"
        token.touch(mode=0o600)
        return str(token)
