"""scutl_baton — estate handoff as a journaled two-phase takeover.

The predecessor packs a content-addressed manifest of everything it
was responsible for; the successor verifies the bytes it was handed
out-of-band, probes every entry before acting, and takes the
single-driver lease through an append-only journal. Secrets never
ride the package — digests and rail locations are the whole of it.
"""

from .core import (AuthorityRequired, Baton, DigestMismatch,
                   ForeignLease, SecretLeakRefused, SimulatedCrash,
                   UnreachableBlocked)

__all__ = ["Baton", "AuthorityRequired", "DigestMismatch",
           "ForeignLease", "SecretLeakRefused", "SimulatedCrash",
           "UnreachableBlocked"]
