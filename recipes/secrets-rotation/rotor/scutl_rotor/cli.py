"""rotor / rotor-approve — the console surface recipe.yaml promises.

Exit-code taxonomy (mirrors the bench's tool-error contract and the
manifest's execute.refusal block):

  0  success
  1  invalid usage / interrupted
  2  not-ready (tombstone or panic — the marker is the report)
  4  approval-required (the owner's token is missing)
  5  in-flight (one ceremony at a time; recover it)
  6  integrity (bytes or journal state failed verification)

Every result is one JSON object on stdout — digests only, never a
secret byte; refusals carry {"error", "exit", "detail"} so an agent
can quote them verbatim.
"""

from __future__ import annotations

import argparse
import json
import sys

from .core import (ApprovalRequired, IntegrityError, Panicked,
                   RotationInFlight, Rotor, Tombstoned)

_HORIZON_FLAG = dict(
    type=int, default=180, metavar="DAYS",
    help="age at which status reports the kek OVERDUE (report and "
         "escalation, never authority; default 180)")


def _emit(obj: dict, code: int = 0) -> int:
    json.dump(obj, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return code


def _refuse(error: str, code: int, detail: str) -> int:
    return _emit({"error": error, "exit": code, "detail": detail}, code)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="rotor",
        description="KEK rotation as a journaled two-phase ceremony")
    p.add_argument("--state", metavar="DIR", default=None,
                   help="wallet state dir (default: $SCUTL_ROTOR_STATE, "
                        "$SCUTL_STATE, then ~/.scutl/wallet)")
    p.add_argument("--horizon-days", **_HORIZON_FLAG)
    p.add_argument("op", choices=["status", "begin", "commit",
                                  "recover", "abort"])
    p.add_argument("reason", nargs="?", default="operator abort",
                   help="abort only: the journaled reason")
    try:
        a = p.parse_args(argv)
    except SystemExit:
        return 1
    rotor = Rotor(state_root=a.state,
                  rotation_horizon_days=a.horizon_days)
    try:
        if a.op == "status":
            return _emit(rotor.status())
        if a.op == "begin":
            return _emit(rotor.begin())
        if a.op == "commit":
            return _emit(rotor.commit())
        if a.op == "recover":
            return _emit(rotor.recover())
        return _emit(rotor.abort(a.reason))
    except ApprovalRequired as e:
        return _refuse("approval-required", 4, str(e))
    except (Tombstoned, Panicked) as e:
        return _refuse("not-ready", 2,
                       f"{type(e).__name__.lower()}: {e}")
    except RotationInFlight as e:
        return _refuse("in-flight", 5, str(e))
    except IntegrityError as e:
        return _refuse("integrity", 6, str(e))
    except (FileNotFoundError, ValueError) as e:
        return _refuse("invalid", 1, str(e))


def approve_main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="rotor-approve",
        description="Grant the owner's consumable token, out of band. "
                    "Run by the HUMAN, never by the agent: one token, "
                    "one ceremony, spent whether it commits or aborts.")
    p.add_argument("--state", metavar="DIR", default=None)
    p.add_argument("op", choices=["rotate"])
    try:
        a = p.parse_args(argv)
    except SystemExit:
        return 1
    rotor = Rotor(state_root=a.state)
    path = rotor.grant(a.op)
    return _emit({"granted": a.op, "token": str(path),
                  "note": "consumed by the next 'rotor begin' — one "
                          "token, one ceremony"})


if __name__ == "__main__":
    raise SystemExit(main())
