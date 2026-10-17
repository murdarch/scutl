"""Human approval tokens for admin ops (manifest: consented acts).

The human creates a consumable token out of band. Configure uses a
plain per-op token; deregistration pins the exact job whose obligation
may be removed:

    bell-approve configure
    bell-approve deregister --job-id <job>

One token authorizes one matching operation. A token for another job
remains armed for that job; a stale generation token is deleted.
"""

from __future__ import annotations

import json

from .state import StateDir, UnknownJob

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
        raise ApprovalRequired(op, f"run 'bell-approve {op}'")
    token.unlink()


def _job_scope(state: StateDir, job_id: str) -> dict:
    try:
        job = state.load_job(job_id)
    except UnknownJob:
        raise ValueError(f"cannot approve unknown job '{job_id}'") from None
    if job.get("tombstoned"):
        raise ValueError(
            f"cannot approve tombstoned job '{job_id}' ({job['tombstoned']})"
        )
    return {
        "job_id": job_id,
        "registered": job["registered"],
        "spec_hash": job["spec_hash"],
        "generation": (
            job.get("generation")
            or f"legacy:{job['registered']}:{job['spec_hash']}"
        ),
    }


def consume_scoped(state: StateDir, op: str, job_id: str) -> None:
    """Consume a token only when it names this exact job generation."""
    token = state.approvals / f"{op}.token"
    command = f"bell-approve {op} --job-id {job_id}"
    if not token.exists():
        raise ApprovalRequired(op, f"job '{job_id}' needs '{command}'")
    try:
        scope = json.loads(token.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        token.unlink(missing_ok=True)
        raise ApprovalRequired(
            op, f"the existing token is unscoped or unreadable; run '{command}'"
        ) from None
    if scope.get("job_id") != job_id:
        raise ApprovalRequired(
            op,
            f"the granted token names job {scope.get('job_id')!r}, not "
            f"{job_id!r}; it remains armed for its named job",
        )
    expected = _job_scope(state, job_id)
    if scope != expected:
        token.unlink()
        raise ApprovalRequired(
            op,
            f"the token for job {job_id!r} is stale for its current "
            f"registration; run '{command}'",
        )
    token.unlink()


def arm_scoped(state: StateDir, op: str, job_id: str) -> str:
    """The human approval act for one immutable job generation."""
    if op not in SCOPED_OPS:
        raise ValueError(
            f"unknown scoped op '{op}' (valid: {', '.join(SCOPED_OPS)})"
        )
    state.init()
    scope = _job_scope(state, job_id)
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
