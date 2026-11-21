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


# -- CLI (the console surface recipe.yaml promises) -----------------------

def _cli(estate, *argv, actor="agent"):
    import io
    from contextlib import redirect_stdout
    from scutl_baton.cli import main
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["--state", str(estate["state"]),
                     "--actor", actor, *argv])
    return code, json.loads(buf.getvalue())


def test_cli_full_succession_and_exit_taxonomy(estate):
    code, out = _cli(estate, "status")
    assert code == 0 and out["lease_holder"] is None
    code, out = _cli(estate, "pack", actor="elder")
    assert code == 0 and out["packed"]
    digest = out["manifest_sha256"]
    code, out = _cli(estate, "stage")           # missing digest arg
    assert code == 1 and out["error"] == "invalid"
    code, out = _cli(estate, "stage", "0" * 64)  # wrong digest
    assert code == 5 and out["error"] == "digest-mismatch"
    code, out = _cli(estate, "commit", actor="heir")  # no release, no token
    # stage first so commit reaches the authority check
    _cli(estate, "stage", digest, actor="heir")
    code, out = _cli(estate, "commit", actor="heir")
    assert code == 4 and out["error"] == "authority-required"
    code, out = _cli(estate, "release", actor="elder")
    assert code == 0 and out["manifest_sha256"] == digest
    code, out = _cli(estate, "commit", actor="heir")
    assert code == 0 and out["committed"] and out["authority"] == "release"
    code, out = _cli(estate, "release", actor="stranger")
    assert code == 2 and out["error"] == "foreign-lease"
    code, out = _cli(estate, "recover")
    assert code == 0 and out["recovered"] is False


def test_cli_unreachable_block_and_override_token(estate):
    _cli(estate, "pack", actor="elder")
    digest = json.loads(
        (estate["state"] / "manifest.json").read_bytes()) and \
        hashlib.sha256(
            (estate["state"] / "manifest.json").read_bytes()).hexdigest()
    _cli(estate, "release", actor="elder")
    estate["svc"].unlink()                      # probe surface gone:
    estate["svc"].mkdir()                       # a dir where a file was
                                                # makes the read itself fail
    code, out = _cli(estate, "stage", digest, actor="heir")
    assert code == 0 and out["unreachable"] == ["income-service"]
    code, out = _cli(estate, "commit", actor="heir")
    assert code == 6 and out["error"] == "unreachable-blocked"
    code, out = _cli(estate, "override", "income-service", "host rebuilt",
                     actor="heir")
    assert code == 4                            # no owner token yet
    from scutl_baton.cli import approve_main
    import io
    from contextlib import redirect_stdout
    with redirect_stdout(io.StringIO()):
        approve_main(["--state", str(estate["state"]), "override"])
    code, out = _cli(estate, "override", "income-service", "host rebuilt",
                     actor="heir")
    assert code == 0
    code, out = _cli(estate, "commit", actor="heir")
    assert code == 0 and out["committed"]


def test_cli_secret_refusal_exit_3(estate):
    blob = json.loads((estate["state"] / ESTATE_NAME).read_text())
    blob["entries"][0]["invariant"] = "hunter2-kek-passphrase"
    (estate["state"] / ESTATE_NAME).write_text(json.dumps(blob))
    code, out = _cli(estate, "pack", actor="elder")
    assert code == 3 and out["error"] == "secret-refused"
    assert "hunter2-kek-passphrase" not in out["detail"]


# -- status: source-bound facts, never a succession action ----------------

def test_status_preserves_tokens_and_state_until_real_commit(estate):
    pred = Baton(estate["state"], actor="elder")
    digest = pred.pack()["manifest_sha256"]
    heir = Baton(estate["state"], actor="heir")
    heir.stage(digest)
    before_token = heir.status()["handoff"]
    assert before_token["journal"]["latest_released"] is None
    assert before_token["authority"]["missing_release_and_token"] is True
    takeover = estate["state"] / TOKEN_TAKEOVER
    takeover.write_text("owner approval\n")
    override = estate["state"] / TOKEN_OVERRIDE
    override.write_text("separate approval\n")
    before = {p.name: p.read_bytes() for p in estate["state"].iterdir()}
    first = heir.status()
    assert heir.status() == first
    assert {p.name: p.read_bytes() for p in estate["state"].iterdir()} == before
    facts = first["handoff"]
    assert facts["authority"]["takeover_token_present"] is True
    assert facts["authority"]["missing_release_and_token"] is False
    assert facts["journal"]["succession_events"] == []
    assert facts["lease"] is None
    assert heir.commit()["authority"] == "owner-token"
    assert not takeover.exists()
    assert override.read_text() == "separate approval\n"
    assert heir.status()["handoff"]["lease"] == json.loads(
        heir.lease_path.read_bytes())


def test_status_distinguishes_inherited_report_from_current_actor(estate):
    _, heir, digest = _handoff_to_stage(estate)
    staged = heir.stage(digest)
    estate["svc"].write_text("changed after the recorded probe\n")
    status = Baton(estate["state"], actor="next-heir").status()
    facts = status["handoff"]
    assert facts["actor"] == "next-heir"
    assert facts["report"]["path"] == str(heir.report_path)
    assert facts["report"]["file_sha256"] == staged["report_sha256"]
    assert facts["report"]["document"] == json.loads(
        heir.report_path.read_bytes())
    assert facts["report"]["document"]["entries"][0]["verdict"] == "MATCH"
    assert facts["bindings"]["report_actor_matches_current"] is False
    assert facts["bindings"]["report_matches_latest_stage"] is True
    assert facts["bindings"]["report_matches_manifest"] is True
    assert facts["bindings"]["latest_release_matches_report_manifest"] is True
    assert status["last_report"]["verdicts"] == {"MATCH": 3}


def test_status_exposes_tampered_report_and_actual_file_hash(estate):
    _, heir, digest = _handoff_to_stage(estate)
    staged = heir.stage(digest)
    report = json.loads(heir.report_path.read_bytes())
    report["entries"][0]["verdict"] = "DRIFT"
    heir.report_path.write_text(json.dumps(report))
    facts = heir.status()["handoff"]
    assert facts["report"]["document"] == report
    assert facts["report"]["file_sha256"] == hashlib.sha256(
        heir.report_path.read_bytes()).hexdigest()
    assert facts["bindings"]["journal_report_sha256"] == staged["report_sha256"]
    assert facts["report"]["file_sha256"] != staged["report_sha256"]
    assert facts["bindings"]["report_matches_latest_stage"] is False
    assert facts["bindings"]["report_matches_manifest"] is True
    with pytest.raises(DigestMismatch):
        heir.commit()


def test_status_distinguishes_changed_manifest_from_intact_stage(estate):
    _, heir, digest = _handoff_to_stage(estate)
    heir.stage(digest)
    manifest = json.loads(heir.manifest_path.read_bytes())
    manifest["entries"][0]["invariant"] = "different manifest bytes"
    heir.manifest_path.write_text(json.dumps(manifest))
    status = heir.status()
    facts = status["handoff"]
    assert facts["manifest_sha256"] == hashlib.sha256(
        heir.manifest_path.read_bytes()).hexdigest()
    assert status["manifest_sha256"] == facts["manifest_sha256"]
    assert facts["manifest_sha256"] != digest
    assert facts["bindings"]["report_matches_latest_stage"] is True
    assert facts["bindings"]["report_matches_manifest"] is False
    assert facts["bindings"]["latest_release_matches_report_manifest"] is True
    with pytest.raises(DigestMismatch):
        heir.commit()


def test_status_keeps_old_succession_events_raw_after_restage(estate):
    _, heir, digest = _handoff_to_stage(estate)
    first = heir.stage(digest)
    heir.commit()
    history = [e for e in heir._entries()
               if e["event"] in ("commit-started", "committed", "unwound")]
    estate["svc"].write_text("new drift for the new report\n")
    second = heir.stage(digest)
    assert second["report_sha256"] != first["report_sha256"]
    facts = heir.status()["handoff"]
    assert facts["journal"]["succession_events"] == history
    assert history[0]["report_sha256"] == first["report_sha256"]
    assert "report_sha256" not in history[1]
    assert facts["journal"]["latest_staged"]["report_sha256"] == second["report_sha256"]
    assert facts["bindings"]["report_matches_latest_stage"] is True
    assert facts["lease"]["holder"] == "heir"


def test_status_finds_release_outside_five_entry_tail(estate):
    _, heir, digest = _handoff_to_stage(estate)
    for _ in range(6):
        heir.stage(digest)
    status = heir.status()
    assert all(e["event"] == "staged" for e in status["journal_tail"])
    facts = status["handoff"]
    assert facts["journal"]["latest_released"] == heir._entries()[1]
    assert facts["bindings"]["latest_release_matches_report_manifest"] is True
    assert facts["authority"]["missing_release_and_token"] is False


def test_status_lists_unreachable_minus_historical_overrides(estate):
    registered = estate["state"] / ESTATE_NAME
    declaration = json.loads(registered.read_text())
    probes = []
    for entry in declaration["entries"][:2]:
        probe = estate["tmp"] / (entry["id"] + ".probe")
        probe.write_text("#!/bin/sh\nprintf 'baseline\\n'\n")
        probe.chmod(0o755)
        entry["probe"] = {"kind": "command", "argv": [str(probe)]}
        probes.append(probe)
    registered.write_text(json.dumps(declaration))
    _, heir, digest = _handoff_to_stage(estate)
    for probe in probes:
        probe.unlink()
    heir.stage(digest)
    facts = heir.status()["handoff"]
    assert facts["authority"]["unreachable_without_override"] == [
        "income-service", "wallet"]
    (estate["state"] / TOKEN_OVERRIDE).touch()
    heir.override("income-service", "owner accepts unavailable service")
    # Restaging does not expire a historical override in the component.
    heir.stage(digest)
    facts = heir.status()["handoff"]
    assert facts["journal"]["overrides"] == [
        e for e in heir._entries() if e["event"] == "override"]
    assert facts["journal"]["overrides"][0]["reason"] == (
        "owner accepts unavailable service")
    assert facts["authority"]["unreachable_without_override"] == ["wallet"]
    with pytest.raises(UnreachableBlocked) as exc:
        heir.commit()
    assert exc.value.entry_ids == ["wallet"]


def test_status_missing_sources_do_not_invent_stage_or_release(estate):
    heir = Baton(estate["state"], actor="heir")
    facts = heir.status()["handoff"]
    assert facts["report"] is None
    assert facts["manifest_sha256"] is None
    assert facts["lease"] is None
    assert facts["journal"]["latest_staged"] is None
    assert facts["journal"]["latest_released"] is None
    assert facts["bindings"]["journal_report_sha256"] is None
    assert facts["bindings"]["report_matches_latest_stage"] is False
    assert facts["bindings"]["report_matches_manifest"] is False
    assert facts["bindings"]["latest_release_matches_report_manifest"] is False
    assert facts["bindings"]["report_actor_matches_current"] is False
    assert facts["authority"]["unreachable_without_override"] == []
