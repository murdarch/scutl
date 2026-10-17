"""Human approval tokens for admin ops (manifest: sender admission).

The human creates a consumable token out of band. Configure uses a
plain per-op token; sender admission pins the exact sender allowed
through the new attack surface:

    wing-approve configure
    wing-approve sender-add --sender-id <sender> --descriptor <json-file>

One token authorizes one matching operation. A token for another sender
remains armed for that sender; a same-sender descriptor mismatch is
stale and deleted.
"""

from __future__ import annotations

import hashlib
import json

from .state import StateDir

PLAIN_OPS = ("configure",)
SCOPED_OPS = ("sender-add",)


class ApprovalRequired(Exception):
    def __init__(self, op: str, detail: str = ""):
        msg = (
            f"admin op '{op}' requires human approval: create the token "
            f"out of band, then retry"
        )
        if detail:
            msg += f" — {detail}"
        super().__init__(msg)
        self.op = op


def consume(state: StateDir, op: str) -> None:
    """Consume a plain per-op token."""
    token = state.approvals / f"{op}.token"
    if not token.exists():
        raise ApprovalRequired(op, f"run 'wing-approve {op}'")
    token.unlink()


def sender_scope(sender_id: str, descriptor: dict) -> dict:
    sender_id = sender_id.strip().lower()
    canonical = json.dumps(
        descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return {
        "sender_id": sender_id,
        "descriptor_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def consume_scoped(
    state: StateDir, op: str, sender_id: str, descriptor: dict
) -> None:
    """Consume a token only for this sender and descriptor."""
    token = state.approvals / f"{op}.token"
    command = (
        f"wing-approve {op} --sender-id {sender_id} "
        f"--descriptor <same-json-file>"
    )
    if not token.exists():
        raise ApprovalRequired(op, f"sender '{sender_id}' needs '{command}'")
    try:
        scope = json.loads(token.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        token.unlink(missing_ok=True)
        raise ApprovalRequired(
            op, f"the existing token is unscoped or unreadable; run '{command}'"
        ) from None
    if scope.get("sender_id") != sender_id:
        raise ApprovalRequired(
            op,
            f"the granted token names sender {scope.get('sender_id')!r}, not "
            f"{sender_id!r}; it remains armed for its named sender",
        )
    expected = sender_scope(sender_id, descriptor)
    if scope != expected:
        token.unlink()
        raise ApprovalRequired(
            op,
            f"the token for sender {sender_id!r} carries a stale descriptor; "
            f"run '{command}'",
        )
    token.unlink()


def arm_scoped(
    state: StateDir, op: str, sender_id: str, descriptor: dict
) -> str:
    """The human approval act for one sender and descriptor."""
    if op not in SCOPED_OPS:
        raise ValueError(
            f"unknown scoped op '{op}' (valid: {', '.join(SCOPED_OPS)})"
        )
    sender_id = sender_id.strip().lower()
    if not sender_id:
        raise ValueError("sender_id must not be empty")
    state.init()
    scope = sender_scope(sender_id, descriptor)
    token = state.approvals / f"{op}.token"
    token.write_text(json.dumps(scope), encoding="utf-8")
    token.chmod(0o600)
    return str(token)


def grant(state: StateDir, op: str) -> str:
    """Human/CLI helper for plain per-op tokens."""
    if op not in PLAIN_OPS:
        raise ValueError(
            f"unknown plain op '{op}' (valid: {', '.join(PLAIN_OPS)})"
        )
    state.init()
    token = state.approvals / f"{op}.token"
    token.touch(mode=0o600)
    return str(token)
