"""Claims + settlement clients — the ONLY modules that talk to the outside.

Core never builds a URL and never parses raw HTTP; everything crosses
these boundaries as plain dicts. The claimant is ADVERSARIAL: every field
of a claim — the tx it names, the amount, the payer it asserts, the story
around them — is carried across faithfully, cheating and all, and core
believes NONE of it: the earnings ledger decides what happened, policy
decides what auto-pays, and the payout address comes from the settle
record, never from here.

Rev 1 is BENCH-FIRST: no live claim source or payout rail is blessed
(the support inbox composes inbox-triage in a future bindings rev; a
live payout is a wallet-recipe send). These classes are the shapes
blessed rails must fit; the mocks in scutbench/refund implement the same
surfaces. The manifest's contracts block is the source of truth:

  claimant.next()    -> {claim_id, text, claimed_tx, claimed_amount,
                         claimed_payer} | None      # all of it assertion
  settlement.payout(refund_id, address, amount)
                     -> {txid} | transient/permanent error
"""

from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request


class TransientError(Exception):
    """Timeouts, 5xx, 429 — safe to retry with the same refund id."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


def _request(base: str, timeout: int, method: str, path: str,
             body: dict | None = None, who: str = "rail") -> dict | None:
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        if e.code == 429 or e.code >= 500:
            raise TransientError(f"{who} {e.code}: {detail}") from None
        raise PermanentError(f"{who} {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError,
            http.client.HTTPException) as e:
        # A connection dying mid-exchange may have landed the request
        # (the payout-ack-lost case): surface as transient so the retry
        # reuses the SAME refund id.
        raise TransientError(
            f"{who} unreachable: "
            f"{e.reason if hasattr(e, 'reason') else e}") from None


class ClaimsClient:
    """Claim-queue surface over HTTP. No blessed live default in rev 1:
    constructing one without an explicit binding refuses, so a bench rung
    must inject its mock and SCUTL_REFUND_CLAIMS binds a servable endpoint
    (the catalog's test desk today; a blessed live source is a future
    bindings rev) deliberately."""

    def __init__(self, base: str | None = None, timeout: int = 30):
        self.base = base or os.environ.get("SCUTL_REFUND_CLAIMS")
        self.timeout = timeout
        if not self.base:
            raise PermanentError(
                "no claims binding in rev 1: inject a mock (bench) or set "
                "SCUTL_REFUND_CLAIMS to a claims endpoint")

    def next(self) -> dict | None:
        out = _request(self.base, self.timeout, "GET", "/next",
                       who="claims")
        return out or None     # an empty body / null means queue drained


class SettlementClient:
    """Payout surface over HTTP — same rev-1 binding rule
    (SCUTL_REFUND_SETTLEMENT). A live payout is a wallet send to the
    recorded payer; the clerk itself never holds a key."""

    def __init__(self, base: str | None = None, timeout: int = 30):
        self.base = base or os.environ.get("SCUTL_REFUND_SETTLEMENT")
        self.timeout = timeout
        if not self.base:
            raise PermanentError(
                "no settlement binding in rev 1: inject a mock (bench) or "
                "set SCUTL_REFUND_SETTLEMENT to a settlement endpoint")

    def payout(self, refund_id: str, address: str, amount: str) -> dict:
        return _request(self.base, self.timeout, "POST", "/payout",
                        {"refund_id": refund_id, "address": address,
                         "amount": amount}, who="settlement")
