"""scutl_rotor — KEK rotation as a journaled two-phase ceremony.

The agent rotates the key-encryption passphrase that seals the wallet
keystore, under an out-of-band human approval, without a single secret
byte ever passing through a tool result. Begin stages and verifies;
commit re-hashes the staged bytes against the journal before the atomic
swap; a crash at any point leaves a journal that recover() can finish
or unwind deterministically.
"""

from .core import (ApprovalRequired, IntegrityError, Panicked,
                   RotationInFlight, Rotor, SimulatedCrash, Tombstoned)

__all__ = ["Rotor", "ApprovalRequired", "IntegrityError", "Panicked",
           "RotationInFlight", "SimulatedCrash", "Tombstoned"]
