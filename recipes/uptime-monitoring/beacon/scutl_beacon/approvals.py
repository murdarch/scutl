"""Human approval tokens for admin ops (manifest: consented acts).

The human creates a consumable token out of band. Configure uses a
plain per-op token; deregistration pins the exact target whose watch
may be retired:

    beacon-approve configure
    beacon-approve deregister --target-id <target>

One token authorizes one matching operation. A token for another target
remains armed for that target; a stale generation token is deleted.
"""

from __future__ import annotations

import json

from .state import StateDir, UnknownTarget

PLAIN_OPS = ("configure",)
SCOPED_OPS = ("deregister",)


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
        raise ApprovalRequired(op, f"run 'beacon-approve {op}'")
    token.unlink()


def _target_scope(state: StateDir, target_id: str) -> dict:
    try:
        target = state.load_target(target_id)
    except UnknownTarget:
        raise ValueError(f"cannot approve unknown target '{target_id}'") from None
    if target.get("tombstoned"):
        raise ValueError(
            f"cannot approve tombstoned target '{target_id}' "
            f"({target['tombstoned']})"
        )
    return {
        "target_id": target_id,
        "registered": target["registered"],
        "spec_hash": target["spec_hash"],
        "generation": (
            target.get("generation")
            or f"legacy:{target['registered']}:{target['spec_hash']}"
        ),
    }


def consume_scoped(state: StateDir, op: str, target_id: str) -> None:
    """Consume a token only when it names this exact target generation."""
    token = state.approvals / f"{op}.token"
    command = f"beacon-approve {op} --target-id {target_id}"
    if not token.exists():
        raise ApprovalRequired(op, f"target '{target_id}' needs '{command}'")
    try:
        scope = json.loads(token.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        token.unlink(missing_ok=True)
        raise ApprovalRequired(
            op, f"the existing token is unscoped or unreadable; run '{command}'"
        ) from None
    if scope.get("target_id") != target_id:
        raise ApprovalRequired(
            op,
            f"the granted token names target {scope.get('target_id')!r}, not "
            f"{target_id!r}; it remains armed for its named target",
        )
    expected = _target_scope(state, target_id)
    if scope != expected:
        token.unlink()
        raise ApprovalRequired(
            op,
            f"the token for target {target_id!r} is stale for its current "
            f"registration; run '{command}'",
        )
    token.unlink()


def arm_scoped(state: StateDir, op: str, target_id: str) -> str:
    """The human approval act for one immutable target generation."""
    if op not in SCOPED_OPS:
        raise ValueError(
            f"unknown scoped op '{op}' (valid: {', '.join(SCOPED_OPS)})"
        )
    state.init()
    scope = _target_scope(state, target_id)
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
