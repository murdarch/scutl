"""Acceptance tests for the Archive cold target (recipe #9 rev 2,
cst-px98.2). The twin owns the archive's dishonesty surface: it can
hold bytes it will not hand over (RestorePending), stall a restore
forever, lose the mirror, and corrupt cold bytes — and the two-phase
rehearsal must come out NEITHER green-washing nor crying wolf."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scutl_silo import approvals
from scutl_silo.approvals import ApprovalRequired
from scutl_silo.core import Manager
from scutl_silo.state import StateDir
from scutl_silo.store import MissingObject, RestorePending, StoreUnreachable

from test_silo import T0, TwinStore, make_file, put_one


class TwinColdStore(TwinStore):
    """The archive bucket: everything TwinStore can do, plus the
    async-restore contract."""

    def __init__(self):
        super().__init__()
        self.archived: set[str] = set()      # keys that pend on get
        self.restore_requests: list[tuple[str, int]] = []

    def get(self, key):
        self._check()
        if key in self.archived and key in self.objects:
            raise RestorePending(key)
        return super().get(key)

    def cold_status(self, key):
        base = self.head(key)
        base["storage_class"] = ("VULTR_ARCHIVE" if key in self.archived
                                 else "STANDARD")
        base["restore"] = ""
        return base

    def request_restore(self, key, days):
        self._check()
        if key not in self.objects:
            raise MissingObject(key)
        self.restore_requests.append((key, days))
        return {"http": 202, "note": "twin: accepted"}

    def thaw(self, key):
        self.archived.discard(key)


@pytest.fixture()
def cold_rig(tmp_path):
    state = StateDir(tmp_path / "state")
    twin = TwinStore()
    cold_twin = TwinColdStore()
    clock = {"now": T0}
    mgr = Manager(state=state, store=twin, cold_store=cold_twin,
                  now_fn=lambda: clock["now"])
    approvals.grant(state, "configure")
    mgr.configure(20, 10, 7, 2, 256)
    creds = tmp_path / "cold.creds"
    creds.write_text("COLD_ACCESS\nCOLD_SECRET\n")
    approvals.grant(state, "configure-cold")
    mgr.configure_cold("cold.example.invalid", "silo-cold",
                       restore_timeout_hours=12, restore_days=2,
                       creds_path=str(creds))
    return state, mgr, twin, cold_twin, clock


# -- ratification --------------------------------------------------------

def test_configure_cold_is_approval_gated(tmp_path):
    state = StateDir(tmp_path / "state")
    mgr = Manager(state=state, store=TwinStore())
    approvals.grant(state, "configure")
    mgr.configure(20, 10, 7, 2, 256)
    creds = tmp_path / "c"
    creds.write_text("A\nB\n")
    with pytest.raises(ApprovalRequired):
        mgr.configure_cold("e", "b", 12, 2, str(creds))


# -- mirror on put (one manifest, two homes) ------------------------------

def test_put_mirrors_to_cold_with_readback_rehash(cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    out = put_one(tmp_path, mgr)
    assert out["cold"] == {"mirrored": True, "chunks": 1}
    assert out["stored"] in cold_twin.objects
    assert cold_twin.objects[out["stored"]] == twin.objects[out["stored"]]
    events = [e["event"] for e in state.read_manifest()]
    assert events == ["put", "cold-put"]


def test_cold_failure_never_unwinds_the_primary_put(cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    cold_twin.dead = True
    out = put_one(tmp_path, mgr)
    assert out["cold"]["mirrored"] is False
    assert out["stored"] in twin.objects          # primary put stands
    events = [e["event"] for e in state.read_manifest()]
    assert events == ["put", "cold-put-failed"]


def test_cold_readback_mismatch_is_a_failed_mirror(cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    data = b'{"income": 1}\n'
    # corrupt whatever lands under this name in the cold home only
    class Corrupting(TwinColdStore):
        def get(self, key):
            return b"not what was sent"
    corrupting = Corrupting()
    mgr._injected_cold_store = corrupting
    out = put_one(tmp_path, mgr, data=data)
    assert out["cold"]["mirrored"] is False
    assert "mismatch" in out["cold"]["why"]


# -- the two-phase rehearsal ----------------------------------------------

def test_archived_object_pends_and_requests_restore(cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    out = put_one(tmp_path, mgr)
    cold_twin.archived.add(out["stored"])         # nightly transition
    rec = mgr.rehearse_cold()
    assert rec["outcome"] == "pending"            # NEITHER green nor red
    assert rec["pending"][0]["key"] == out["stored"]
    assert cold_twin.restore_requests == [(out["stored"], 2)]


def test_second_invocation_resolves_pending_to_green(cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    out = put_one(tmp_path, mgr)
    cold_twin.archived.add(out["stored"])
    mgr.rehearse_cold()                            # phase A
    cold_twin.thaw(out["stored"])                  # restore delivered
    clock["now"] = T0 + timedelta(hours=3)
    rec = mgr.rehearse_cold()                      # phase B
    assert rec["outcome"] == "green"
    assert rec["resolved"] == [out["stored"]]
    assert rec["pending"] == []


def test_stuck_restore_keeps_its_first_since_and_goes_overdue(
        cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    out = put_one(tmp_path, mgr)
    cold_twin.archived.add(out["stored"])
    mgr.rehearse_cold()
    clock["now"] = T0 + timedelta(hours=8)
    rec2 = mgr.rehearse_cold()                     # still pending
    # the clock did NOT reset: since is the FIRST request time
    assert rec2["pending"][0]["since"] == T0.isoformat()
    # only one restore request went to the wire
    assert len(cold_twin.restore_requests) == 1
    # 8h < 12h wall: pending, not yet a breach
    rep = mgr.report()
    assert not any("restore-overdue" in b for b in rep["breaches"])
    assert rep["cold"]["pending"][0]["age_hours"] == 8.0
    clock["now"] = T0 + timedelta(hours=13)
    rep = mgr.report()                             # 13h > 12h wall
    assert any("restore-overdue" in b for b in rep["breaches"])
    assert rep["escalate"] is True


def test_cold_corruption_is_red_not_pending(cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    out = put_one(tmp_path, mgr)
    cold_twin.corrupt[out["stored"]] = b"rot"
    rec = mgr.rehearse_cold()
    assert rec["outcome"] == "red"
    assert rec["mismatches"][0]["problem"] == "digest"


def test_never_mirrored_is_red_drift_not_absence(cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    cold_twin.dead = True
    out = put_one(tmp_path, mgr)                   # mirror fails
    cold_twin.dead = False
    rec = mgr.rehearse_cold()
    assert rec["outcome"] == "red"
    assert rec["never_mirrored"] == [out["stored"]]


def test_unreachable_cold_endpoint_is_its_own_honest_outcome(
        cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    put_one(tmp_path, mgr)
    cold_twin.dead = True
    rec = mgr.rehearse_cold()
    assert rec["outcome"] == "unreachable"


# -- report doctrine -------------------------------------------------------

def test_no_cold_rehearsal_ever_is_a_named_breach(cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    put_one(tmp_path, mgr)
    rep = mgr.report()
    assert any("no cold rehearsal has EVER" in b for b in rep["breaches"])


def test_green_cold_rehearsal_clears_the_board(cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    put_one(tmp_path, mgr)
    mgr.rehearse()
    rec = mgr.rehearse_cold()
    assert rec["outcome"] == "green"
    rep = mgr.report()
    assert rep["breaches"] == []
    assert rep["cold"]["last"]["outcome"] == "green"


def test_hot_rehearsal_untouched_by_cold_records(cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    out = put_one(tmp_path, mgr)
    cold_twin.archived.add(out["stored"])
    mgr.rehearse_cold()                            # a cold pending...
    rec = mgr.rehearse()                           # ...is invisible hot
    assert rec["outcome"] == "green"


# -- consented delete covers both homes -------------------------------------

def test_delete_removes_cold_copy_under_same_consent(cold_rig, tmp_path):
    state, mgr, twin, cold_twin, clock = cold_rig
    out = put_one(tmp_path, mgr)
    approvals.grant(state, "delete")
    res = mgr.delete(out["stored"])
    assert res["cold_deleted"] is True
    assert out["stored"] not in cold_twin.objects
