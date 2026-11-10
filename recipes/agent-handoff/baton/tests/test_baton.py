"""Baton acceptance: the recipe's verify block, as tests.

Every crash test recovers from a FRESH Baton instance — no in-memory
state survives, exactly as on the live wire.
"""

import hashlib
import json
from pathlib import Path

import pytest

from scutl_baton import (AuthorityRequired, Baton, DigestMismatch,
                         ForeignLease, SecretLeakRefused, SimulatedCrash,
                         UnreachableBlocked)
from scutl_baton.core import (ESTATE_NAME, JOURNAL_NAME, TOKEN_OVERRIDE,
                              TOKEN_TAKEOVER)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture
def estate(tmp_path):
    """A three-class registered estate backed by real files."""
    svc = tmp_path / "service.state"
    svc.write_text("running\n")
    bal = tmp_path / "balance.state"
    bal.write_text("12.50 USDC cap 1.00/day\n")
    due = tmp_path / "obligation.state"
    due.write_text("backup rehearsal due 2026-11-20\n")
    state = tmp_path / "baton-state"
    state.mkdir()
    (state / ESTATE_NAME).write_text(json.dumps({
        "freshness_hours": 72,
        "secret_digests": [_sha("hunter2-kek-passphrase")],
        "entries": [
            {"id": "income-service", "class": "service",
             "probe": {"kind": "file", "path": str(svc)},
             "invariant": "unit state file matches baseline"},
            {"id": "wallet", "class": "money",
             "probe": {"kind": "file", "path": str(bal)},
             "invariant": "balance surface matches baseline; secrets "
                          "by digest, rail: idbr restore ceremony"},
            {"id": "backup-rehearsal", "class": "obligation",
             "probe": {"kind": "file", "path": str(due)},
             "due": "2026-11-20",
             "invariant": "rehearsal not lapsed"},
        ]}))
    return {"state": state, "svc": svc, "bal": bal, "due": due,
            "tmp": tmp_path}


def _handoff_to_stage(estate, successor="heir"):
    pred = Baton(estate["state"], actor="elder")
    digest = pred.pack()["manifest_sha256"]
    pred.release()
    heir = Baton(estate["state"], actor=successor)
    return pred, heir, digest


# -- pack ----------------------------------------------------------------

def test_pack_records_baselines_and_journals_digest(estate):
    b = Baton(estate["state"], actor="elder")
    out = b.pack()
    assert out["entries"] == 3
    manifest = json.loads((estate["state"] / "manifest.json").read_text())
    for rec in manifest["entries"]:
        assert rec["baseline"]["observed_sha256"]
        assert rec["baseline"]["reachable"] is True
    tail = b.status()["journal_tail"]
    assert tail[-1]["event"] == "packed"
    assert tail[-1]["manifest_sha256"] == out["manifest_sha256"]


def test_pack_refuses_registered_secret_value(estate):
    blob = json.loads((estate["state"] / ESTATE_NAME).read_text())
    blob["entries"][1]["invariant"] = "hunter2-kek-passphrase"
    (estate["state"] / ESTATE_NAME).write_text(json.dumps(blob))
    with pytest.raises(SecretLeakRefused):
        Baton(estate["state"], actor="elder").pack()
    assert not (estate["state"] / "manifest.json").exists()


def test_pack_refuses_empty_estate(tmp_path):
    with pytest.raises(ValueError):
        Baton(tmp_path / "s", actor="elder").pack()


# -- release + stage -----------------------------------------------------

def test_full_succession_green_path(estate):
    pred, heir, digest = _handoff_to_stage(estate)
    staged = heir.stage(digest)
    assert staged["verdicts"] == {"MATCH": 3}
    out = heir.commit()
    assert out["committed"] and out["authority"] == "release"
    assert out["holder"] == "heir"
    events = [e["event"] for e in heir._entries()]
    assert events == ["packed", "released", "staged",
                      "commit-started", "committed"]


def test_stage_refuses_wrong_out_of_band_digest(estate):
    _, heir, digest = _handoff_to_stage(estate)
    with pytest.raises(DigestMismatch):
        heir.stage("0" * 64)


def test_stage_refuses_manifest_swapped_after_release(estate):
    _, heir, digest = _handoff_to_stage(estate)
    mp = estate["state"] / "manifest.json"
    m = json.loads(mp.read_text())
    m["entries"][0]["invariant"] = "trust me, it is fine"
    mp.write_text(json.dumps(m, sort_keys=True, separators=(",", ":")))
    with pytest.raises(DigestMismatch):
        heir.stage(digest)


def test_testimony_contradicted_lands_as_drift(estate):
    _, heir, digest = _handoff_to_stage(estate)
    estate["svc"].write_text("dead\n")      # world moved; manifest says up
    staged = heir.stage(digest)
    assert staged["drift"] == ["income-service"]
    report = json.loads((estate["state"] / "report.json").read_text())
    row = [r for r in report["entries"] if r["id"] == "income-service"][0]
    assert row["verdict"] == "DRIFT"
    assert row["observed_sha256"] != row["baseline_sha256"]


def test_deliberate_down_state_is_a_match(estate):
    # park the service BEFORE pack, marked deliberate: its packed
    # baseline is 'down', and a probe seeing the same down is MATCH.
    estate["svc"].write_text("paused by cst-xyz ruling\n")
    blob = json.loads((estate["state"] / ESTATE_NAME).read_text())
    blob["entries"][0]["deliberate"] = {
        "reason": "announce parked pending ruling", "authority": "cst-xyz"}
    (estate["state"] / ESTATE_NAME).write_text(json.dumps(blob))
    _, heir, digest = _handoff_to_stage(estate)
    staged = heir.stage(digest)
    assert staged["verdicts"] == {"MATCH": 3}
    report = json.loads((estate["state"] / "report.json").read_text())
    row = [r for r in report["entries"] if r["id"] == "income-service"][0]
    assert row["deliberate"]["authority"] == "cst-xyz"


def test_stale_manifest_is_reported_not_skipped(estate):
    _, heir, digest = _handoff_to_stage(estate)
    mp = estate["state"] / "manifest.json"
    m = json.loads(mp.read_text())
    m["packed_epoch"] -= 100 * 3600
    mp.write_text(json.dumps(m, sort_keys=True, separators=(",", ":")))
    new_digest = hashlib.sha256(mp.read_bytes()).hexdigest()
    staged = heir.stage(new_digest)     # digest still carried out-of-band
    assert staged["manifest_stale"] is True


# -- commit: report binding, unreachable, authority ----------------------

def test_commit_refuses_report_tampered_after_stage(estate):
    _, heir, digest = _handoff_to_stage(estate)
    estate["svc"].write_text("dead\n")      # a real DRIFT to falsify
    heir.stage(digest)
    rp = estate["state"] / "report.json"
    r = json.loads(rp.read_text())
    for row in r["entries"]:
        row["verdict"] = "MATCH"        # transcribe testimony
    rp.write_text(json.dumps(r, sort_keys=True, separators=(",", ":")))
    with pytest.raises(DigestMismatch):
        heir.commit()


def test_commit_refuses_without_stage_report(estate):
    _, heir, digest = _handoff_to_stage(estate)
    with pytest.raises(DigestMismatch):
        heir.commit()


def test_unreachable_blocks_commit_until_override(estate):
    blob = json.loads((estate["state"] / ESTATE_NAME).read_text())
    blob["entries"][0]["probe"] = {"kind": "command",
                                   "argv": ["/no/such/binary"]}
    (estate["state"] / ESTATE_NAME).write_text(json.dumps(blob))
    _, heir, digest = _handoff_to_stage(estate)
    staged = heir.stage(digest)
    assert staged["unreachable"] == ["income-service"]
    with pytest.raises(UnreachableBlocked):
        heir.commit()
    # override without the owner's token refuses; with it, commits
    with pytest.raises(AuthorityRequired):
        heir.override("income-service", "probe host rebuilt")
    (estate["state"] / TOKEN_OVERRIDE).touch()
    heir.override("income-service", "probe host rebuilt")
    out = heir.commit()
    assert out["committed"]
    events = [e for e in heir._entries() if e["event"] == "override"]
    assert events[0]["entry"] == "income-service"


def test_contested_estate_needs_owner_token(estate):
    pred = Baton(estate["state"], actor="elder")
    digest = pred.pack()["manifest_sha256"]
    # predecessor vanished: no release entry
    heir = Baton(estate["state"], actor="heir")
    heir.stage(digest)
    with pytest.raises(AuthorityRequired):
        heir.commit()
    (estate["state"] / TOKEN_TAKEOVER).touch()
    out = heir.commit()
    assert out["authority"] == "owner-token"
    # one token, one takeover: the token is gone
    assert not (estate["state"] / TOKEN_TAKEOVER).exists()


def test_foreign_live_lease_refuses_commit_and_release(estate):
    pred, heir, digest = _handoff_to_stage(estate)
    heir.stage(digest)
    (estate["state"] / "lease.json").write_text(json.dumps(
        {"holder": "usurper", "since": "now", "manifest_sha256": digest}))
    with pytest.raises(ForeignLease):
        heir.commit()
    with pytest.raises(ForeignLease):
        pred.release()


# -- crash recovery, always from a fresh process -------------------------

def test_crash_mid_stage_restages_clean(estate):
    _, heir, digest = _handoff_to_stage(estate)
    crasher = Baton(estate["state"], actor="heir", crash_point="mid-stage")
    with pytest.raises(SimulatedCrash):
        crasher.stage(digest)
    fresh = Baton(estate["state"], actor="heir")
    rec = fresh.recover()
    assert rec["recovered"] is False        # nothing journaled mid-stage;
    assert rec["state"] == "released"       # probes are idempotent —
    fresh.stage(digest)                     # re-stage and carry on
    assert fresh.commit()["committed"]


def test_crash_mid_commit_completes_from_journal(estate):
    _, heir, digest = _handoff_to_stage(estate)
    heir.stage(digest)
    crasher = Baton(estate["state"], actor="heir",
                    crash_point="mid-commit")
    with pytest.raises(SimulatedCrash):
        crasher.commit()
    assert not (estate["state"] / "lease.json").exists()
    fresh = Baton(estate["state"], actor="heir")
    rec = fresh.recover()
    assert rec["action"] == "completed"
    lease = json.loads((estate["state"] / "lease.json").read_text())
    assert lease["holder"] == "heir"
    assert [e["event"] for e in fresh._entries()][-2:] == \
        ["commit-started", "committed"]


def test_crash_mid_commit_with_drifted_bytes_unwinds(estate):
    _, heir, digest = _handoff_to_stage(estate)
    heir.stage(digest)
    crasher = Baton(estate["state"], actor="heir",
                    crash_point="mid-commit")
    with pytest.raises(SimulatedCrash):
        crasher.commit()
    rp = estate["state"] / "report.json"
    rp.write_text(rp.read_text() + " ")     # drift inside the crash window
    fresh = Baton(estate["state"], actor="heir")
    rec = fresh.recover()
    assert rec["action"] == "unwound"
    assert not (estate["state"] / "lease.json").exists()
    assert fresh._entries()[-1]["event"] == "unwound"


def test_recover_idle_estate_reports_idle(estate):
    b = Baton(estate["state"], actor="heir")
    assert b.recover()["state"] == "idle"


# -- journal + report honesty -------------------------------------------

def test_journal_is_append_only_jsonl_digests_only(estate):
    _, heir, digest = _handoff_to_stage(estate)
    heir.stage(digest)
    heir.commit()
    lines = (estate["state"] / JOURNAL_NAME).read_text().splitlines()
    assert len(lines) == 5
    for line in lines:
        e = json.loads(line)
        assert "hunter2" not in line
        assert e["event"] in ("packed", "released", "staged",
                              "commit-started", "committed")


def test_no_secret_byte_in_any_artifact_or_return(estate):
    secret = "hunter2-kek-passphrase"
    pred, heir, digest = _handoff_to_stage(estate)
    returns = [json.dumps(heir.stage(digest)),
               json.dumps(heir.commit()),
               json.dumps(heir.status(), default=str)]
    artifacts = [p.read_text() for p in
                 (estate["state"] / n for n in
                  ("manifest.json", "report.json", JOURNAL_NAME))]
    for text in returns + artifacts:
        assert secret not in text


def test_cross_model_succession_from_digest_alone(estate):
    """stage+commit by an actor that did not produce the package,
    knowing only the state dir and the out-of-band digest."""
    pred = Baton(estate["state"], actor="elder-fable")
    digest = pred.pack()["manifest_sha256"]
    pred.release()
    heir = Baton(estate["state"], actor="qwen-successor")
    assert heir.stage(digest)["verdicts"] == {"MATCH": 3}
    assert heir.commit()["holder"] == "qwen-successor"
