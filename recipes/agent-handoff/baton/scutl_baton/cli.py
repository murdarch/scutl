"""baton / baton-approve — the console surface recipe.yaml promises.

Exit-code taxonomy (mirrors the manifest's execute.refusal block; the
same shape as rotor's, recipe #28):

  0  success
  1  invalid usage / nothing to act on
  2  foreign-lease (someone else holds the estate — report, never
     contest in code)
  3  secret-refused (a value matching a registered secret digest
     reached the packager)
  4  authority-required (contested estate and no takeover token, or an
     override without the owner's token)
  5  digest-mismatch (manifest or report bytes failed verification —
     the package is not what was handed over)
  6  unreachable-blocked (UNREACHABLE entries block commit absent a
     journaled per-entry override)

Every result is one JSON object on stdout — digests only, never a
secret byte; refusals carry {"error", "exit", "detail"} so an agent
can quote them verbatim.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core import (TOKEN_OVERRIDE, TOKEN_TAKEOVER, AuthorityRequired,
                   Baton, DigestMismatch, ForeignLease,
                   SecretLeakRefused, UnreachableBlocked)


def _state(arg: str | None) -> str:
    return (arg or os.environ.get("SCUTL_BATON_STATE")
            or "~/.scutl/baton")


def _emit(obj: dict, code: int = 0) -> int:
    json.dump(obj, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return code


def _refuse(error: str, code: int, detail: str) -> int:
    return _emit({"error": error, "exit": code, "detail": detail}, code)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="baton",
        description="estate handoff as a journaled two-phase takeover")
    p.add_argument("--state", metavar="DIR", default=None,
                   help="baton state dir (default: $SCUTL_BATON_STATE, "
                        "then ~/.scutl/baton)")
    p.add_argument("--actor", default=os.environ.get(
                       "SCUTL_BATON_ACTOR", "agent"),
                   help="who acts (lease holder / journal 'by'; "
                        "default: $SCUTL_BATON_ACTOR or 'agent')")
    p.add_argument("op", choices=["status", "pack", "release", "stage",
                                  "commit", "recover", "override"])
    p.add_argument("arg1", nargs="?", default=None,
                   help="stage: the out-of-band manifest sha256; "
                        "override: the entry id")
    p.add_argument("arg2", nargs="?", default=None,
                   help="override only: the journaled reason")
    try:
        a = p.parse_args(argv)
    except SystemExit:
        return 1
    baton = Baton(_state(a.state), actor=a.actor)
    try:
        if a.op == "status":
            return _emit(baton.status())
        if a.op == "pack":
            return _emit(baton.pack())
        if a.op == "release":
            return _emit(baton.release())
        if a.op == "stage":
            if not a.arg1:
                return _refuse(
                    "invalid", 1,
                    "stage requires the manifest sha256 carried "
                    "out-of-band: baton stage <sha256> — the successor "
                    "verifies bytes it did not choose")
            return _emit(baton.stage(a.arg1))
        if a.op == "commit":
            return _emit(baton.commit())
        if a.op == "recover":
            return _emit(baton.recover())
        # override
        if not a.arg1 or not a.arg2:
            return _refuse(
                "invalid", 1,
                "override requires an entry id and a reason: "
                "baton override <entry-id> <reason>")
        return _emit(baton.override(a.arg1, a.arg2))
    except AuthorityRequired as e:
        return _refuse("authority-required", 4, str(e))
    except DigestMismatch as e:
        return _refuse("digest-mismatch", 5, str(e))
    except UnreachableBlocked as e:
        return _refuse("unreachable-blocked", 6, str(e))
    except ForeignLease as e:
        return _refuse("foreign-lease", 2, str(e))
    except SecretLeakRefused as e:
        return _refuse("secret-refused", 3, str(e))
    except (FileNotFoundError, ValueError) as e:
        return _refuse("invalid", 1, str(e))


def approve_main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="baton-approve",
        description="Grant the owner's consumable token, out of band. "
                    "Run by the HUMAN, never by the agent: one token, "
                    "one act, spent when consumed.")
    p.add_argument("--state", metavar="DIR", default=None)
    p.add_argument("op", choices=["takeover", "override"])
    try:
        a = p.parse_args(argv)
    except SystemExit:
        return 1
    root = Path(_state(a.state)).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    name = TOKEN_TAKEOVER if a.op == "takeover" else TOKEN_OVERRIDE
    path = root / name
    path.write_text("")
    consumer = ("'baton commit' on a contested estate"
                if a.op == "takeover"
                else "'baton override <entry-id> <reason>'")
    return _emit({"granted": a.op, "token": str(path),
                  "note": f"consumed by the next {consumer} — one "
                          f"token, one act"})


if __name__ == "__main__":
    raise SystemExit(main())
