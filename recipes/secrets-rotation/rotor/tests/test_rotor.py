"""scutl_rotor component tests: the ceremony's walls, every crash
point recoverable, no secret byte in any return value. Fast KDF params
throughout — the cipher is the library's job; the ceremony is ours."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from eth_account import Account

from scutl_rotor.core import (ApprovalRequired, IntegrityError, Panicked,
                              Rotor, RotationInFlight, SimulatedCrash,
                              Tombstoned, _sha256_file)

KEY = "0x" + "11" * 32
KEK = "aa" * 32
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def make_identity(root: Path, key: str = KEY, kek: str = KEK) -> str:
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    (root / "approvals").mkdir(exist_ok=True)
    doc = Account.encrypt(key, kek, kdf="pbkdf2", iterations=100)
    (root / "keystore.json").write_text(json.dumps(doc))
    (root / "kek").write_text(kek)
    return Account.from_key(key).address


def make_rotor(root: Path, **kw) -> Rotor:
    kw.setdefault("clock", lambda: NOW)
    kw.setdefault("kdf", "pbkdf2")
    kw.setdefault("kdf_iterations", 100)
    return Rotor(state_root=root, **kw)


@pytest.fixture
def rig(tmp_path):
    root = tmp_path / "state"
    addr = make_identity(root)
    return root, addr


def crash_at(point):
    def hook(p):
        if p == point:
            raise SimulatedCrash(point)
    return hook


# -- the happy ceremony ----------------------------------------------------

def test_full_rotation_swaps_kek_and_keeps_address(rig):
    root, addr = rig
    r = make_rotor(root)
    r.grant("rotate")
    old_kek_digest = _sha256_file(root / "kek")
    out = r.begin()
    assert out["phase"] == "verified"
    assert out["owner_address"].lower() == addr.lower()
    landed = r.commit()
    assert landed["committed"] and landed["old_kek_destroyed"]
    new_kek = (root / "kek").read_text().strip()
    assert new_kek != KEK
    assert _sha256_file(root / "kek") != old_kek_digest
    restored = Account.from_key(Account.decrypt(
        json.loads((root / "keystore.json").read_text()), new_kek))
    assert restored.address.lower() == addr.lower()
    events = [e["event"] for e in r._journal()]
    assert events == ["begin", "staged", "verified", "commit-started",
                      "committed"]
    assert not r.staging.exists()


def test_no_secret_bytes_in_any_return_value(rig):
    root, _ = rig
    r = make_rotor(root)
    r.grant("rotate")
    blob = repr(r.status()) + repr(r.begin())
    blob += repr(r.commit()) + repr(r.status()) + repr(r.recover())
    new_kek = (root / "kek").read_text().strip()
    assert KEK not in blob
    assert new_kek not in blob
    assert KEY[2:] not in blob
    # ... and the journal itself is digests-only
    assert KEK not in (root / "rotation.log").read_text()
    assert new_kek not in (root / "rotation.log").read_text()


# -- gates -------------------------------------------------------------

def test_unapproved_rotation_hard_fails(rig):
    root, _ = rig
    r = make_rotor(root)
    with pytest.raises(ApprovalRequired):
        r.begin()
    assert not r._journal() and not r.staging.exists()


def test_one_token_one_rotation(rig):
    root, _ = rig
    r = make_rotor(root)
    r.grant("rotate")
    r.begin()
    r.commit()
    with pytest.raises(ApprovalRequired):
        r.begin()


def test_tombstone_and_panic_refuse(rig):
    root, _ = rig
    r = make_rotor(root)
    r.grant("rotate")
    (root / "tombstone.json").write_text(json.dumps({"address": "0x0"}))
    with pytest.raises(Tombstoned):
        r.begin()
    (root / "tombstone.json").unlink()
    (root / "panic.json").write_text(json.dumps(
        {"panicked_at": NOW.isoformat(), "reason": "drill"}))
    with pytest.raises(Panicked):
        r.begin()


def test_second_begin_refuses_while_in_flight(rig):
    root, _ = rig
    r = make_rotor(root)
    r.grant("rotate")
    r.begin()
    r.grant("rotate")
    with pytest.raises(RotationInFlight):
        r.begin()


def test_wrong_kek_refuses_and_leaves_no_staging(rig):
    root, _ = rig
    (root / "kek").write_text("ff" * 32)
    r = make_rotor(root)
    r.grant("rotate")
    with pytest.raises(IntegrityError):
        r.begin()
    assert not r.staging.exists()


# -- commit binds to bytes ---------------------------------------------

def test_commit_refuses_tampered_staging(rig):
    root, _ = rig
    r = make_rotor(root)
    r.grant("rotate")
    r.begin()
    staged = r.staging / "keystore.json"
    staged.write_text(staged.read_text() + " ")
    with pytest.raises(IntegrityError, match="journal recorded"):
        r.commit()
    # live pair untouched
    assert (root / "kek").read_text().strip() == KEK


def test_commit_refuses_live_drift(rig):
    root, addr = rig
    r = make_rotor(root)
    r.grant("rotate")
    r.begin()
    # the live keystore changes under the ceremony (same key, new salt)
    doc = Account.encrypt(KEY, KEK, kdf="pbkdf2", iterations=100)
    (root / "keystore.json").write_text(json.dumps(doc))
    with pytest.raises(IntegrityError, match="drift"):
        r.commit()
    assert (root / "kek").read_text().strip() == KEK


# -- every crash point recovers -----------------------------------------

@pytest.mark.parametrize("point", ["after-journal-begin",
                                   "after-stage-keystore",
                                   "after-stage-kek",
                                   "after-journal-staged"])
def test_crash_before_verified_rolls_back(rig, point):
    root, addr = rig
    r = make_rotor(root, fault_hook=crash_at(point))
    r.grant("rotate")
    with pytest.raises(SimulatedCrash):
        r.begin()
    r2 = make_rotor(root)       # fresh process, no hook
    out = r2.recover()
    assert out["recovered"] and out["action"] == "rolled-back"
    assert (root / "kek").read_text().strip() == KEK
    restored = Account.from_key(Account.decrypt(
        json.loads((root / "keystore.json").read_text()), KEK))
    assert restored.address.lower() == addr.lower()
    assert r2._journal()[-1]["event"] == "aborted"
    assert not r2.staging.exists()


def test_crash_after_verified_rolls_forward(rig):
    root, addr = rig
    r = make_rotor(root, fault_hook=crash_at("after-journal-verified"))
    r.grant("rotate")
    with pytest.raises(SimulatedCrash):
        r.begin()
    r2 = make_rotor(root)
    out = r2.recover()
    assert out["action"] == "rolled-forward" and out["committed"]
    new_kek = (root / "kek").read_text().strip()
    assert new_kek != KEK
    restored = Account.from_key(Account.decrypt(
        json.loads((root / "keystore.json").read_text()), new_kek))
    assert restored.address.lower() == addr.lower()


@pytest.mark.parametrize("point", ["after-journal-commit-started",
                                   "mid-commit"])
def test_crash_during_commit_completes_swap(rig, point):
    root, addr = rig
    r = make_rotor(root, fault_hook=crash_at(point))
    r.grant("rotate")
    r.begin()
    with pytest.raises(SimulatedCrash):
        r.commit()
    r2 = make_rotor(root)
    out = r2.recover()
    assert out["action"] == "completed-swap" and out["committed"]
    new_kek = (root / "kek").read_text().strip()
    assert new_kek != KEK
    restored = Account.from_key(Account.decrypt(
        json.loads((root / "keystore.json").read_text()), new_kek))
    assert restored.address.lower() == addr.lower()
    assert r2._journal()[-1]["event"] == "committed"
    assert not r2.staging.exists()


def test_recover_idle_is_a_noop(rig):
    root, _ = rig
    r = make_rotor(root)
    assert r.recover() == {"recovered": False, "state": "idle",
                           "note": "journal shows no rotation in flight"}


def test_abort_refuses_past_commit_started(rig):
    root, _ = rig
    r = make_rotor(root, fault_hook=crash_at("mid-commit"))
    r.grant("rotate")
    r.begin()
    with pytest.raises(SimulatedCrash):
        r.commit()
    r2 = make_rotor(root)
    with pytest.raises(IntegrityError, match="recover"):
        r2.abort()


def test_abort_before_commit_leaves_live_untouched(rig):
    root, _ = rig
    r = make_rotor(root)
    r.grant("rotate")
    r.begin()
    out = r.abort("changed my mind")
    assert "aborted" in out
    assert (root / "kek").read_text().strip() == KEK
    assert not r.staging.exists()


# -- status ------------------------------------------------------------

def test_status_reports_overdue_and_inflight(rig):
    root, _ = rig
    old = NOW - timedelta(days=400)
    os.utime(root / "kek", (old.timestamp(), old.timestamp()))
    r = make_rotor(root)
    st = r.status()
    assert st["overdue"] and st["kek_age_days"] >= 399
    assert st["in_flight"] is None
    r.grant("rotate")
    r.begin()
    st2 = r.status()
    assert st2["in_flight"]["phase"] == "verified"
    r.commit()
    st3 = r.status()
    assert st3["rotations_committed"] == 1
    assert not st3["overdue"]       # age now counts from the commit
