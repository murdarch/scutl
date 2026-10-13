"""Merchant client — the ONLY module that talks to the merchant.

Core never builds a URL and never parses raw HTTP; everything crosses
this boundary as plain dicts. This is also the trust boundary the recipe
is about: the merchant is ADVERSARIAL, so nothing it returns is believed
on its own — core re-checks every number against the target and the caps
and the quote it accepted. The client's job is only to carry the
merchant's claims across faithfully, cheating and all.

Rev 1 is BENCH-FIRST: no live merchant is blessed (x402-merchant is a
future bindings rev; card-network and closed-retail checkout are benched
but not live-receiptable). This class is the shape a blessed merchant
must fit; the mock in scutbench/pwatch implements the same surface. The
manifest's contracts block is the source of truth for ops and failure
modes:

  quote(item)      -> {quote_id, sticker_usdc, quoted_total_usdc, expires,
                       presentation}      # presentation is the merchant's
                                          # free text — anchors, scarcity,
                                          # "price for you" — pure DATA
  checkout(quote)  -> {total_usdc, fees:[{label, usdc}]}   # the real total
  settle(quote,pid)-> {txid} | transient/permanent error
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request


class TransientError(Exception):
    """Timeouts, 5xx, 429 — safe to retry with the same payment id."""


class PermanentError(Exception):
    """4xx (except 429) — retrying the same request cannot succeed."""


class MerchantClient:
    """Merchant surface over HTTP. No blessed live default in rev 1:
    constructing one without an explicit binding refuses, so a bench rung
    must inject its mock and SCUTL_PWATCH_MERCHANT binds a servable
    endpoint (the catalog's test merchant today; a blessed live rail is a
    future bindings rev) deliberately."""

    def __init__(self, base: str | None = None, timeout: int = 30):
        import os
        self.base = base or os.environ.get("SCUTL_PWATCH_MERCHANT")
        self.timeout = timeout
        if not self.base:
            raise PermanentError(
                "no merchant binding in rev 1: inject a mock (bench) or set "
                "SCUTL_PWATCH_MERCHANT to a merchant endpoint")

    # -- transport -----------------------------------------------------
    def _request(self, method: str, path: str,
                 body: dict | None = None) -> dict:
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code == 429 or e.code >= 500:
                raise TransientError(f"merchant {e.code}: {detail}") from None
            raise PermanentError(f"merchant {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.HTTPException) as e:
            # The connection dying mid-exchange is the settle-ack-lost
            # case: the charge may have landed, so this must surface as
            # transient and the retry must reuse the SAME payment id.
            raise TransientError(
                f"merchant unreachable: "
                f"{e.reason if hasattr(e, 'reason') else e}") from None

    # -- surface (contracts.merchant) ------------------------------------
    def quote(self, item: str) -> dict:
        return self._request(
            "GET", f"/quote?item={urllib.parse.quote(item)}")

    def checkout(self, quote_id: str) -> dict:
        return self._request(
            "GET", f"/checkout?quote_id={urllib.parse.quote(quote_id)}")

    def settle(self, quote_id: str, payment_id: str) -> dict:
        return self._request("POST", "/settle",
                             {"quote_id": quote_id,
                              "payment_id": payment_id})
