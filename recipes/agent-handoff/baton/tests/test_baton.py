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


# -- presentation: actual CLI consumer boundaries ------------------------

def _presentation(estate, released=True):
    pred = Baton(estate["state"], actor="elder")
    digest = pred.pack()["manifest_sha256"]
    if released:
        pred.release()
    heir = Baton(estate["state"], actor="heir")
    staged = heir.stage(digest)
    slots = estate["tmp"] / "slots.json"
    output = estate["tmp"] / "report.md"
    code, receipt = _cli(
        estate, "report-template", "--manifest-sha256", digest,
        "--stage-report-sha256", staged["report_sha256"],
        "--out", str(slots), actor="heir")
    assert code == 0
    assert receipt["sha256"] == hashlib.sha256(slots.read_bytes()).hexdigest()
    document = json.loads(slots.read_bytes())
    document["slots"]["stage"]["interpretation"] = "Stage interpretation."
    for row in document["slots"]["entries"].values():
        row["interpretation"] = "Entry interpretation."
        row["next_step"] = "Requested next step; not an action receipt."
    document["slots"]["authority"]["reachability_or_override"] = "Reachability assessment."
    document["slots"]["authority"]["release_or_takeover"] = "Separate authority assessment."
    document["slots"]["final_state"]["completed_and_remaining"] = "Completed and remaining assessment."
    slots.write_text(json.dumps(document))
    return heir, digest, staged, slots, output, receipt


def _render(estate, slots, output, actor="heir"):
    return _cli(estate, "report", "--slots", str(slots),
                "--out", str(output), actor=actor)


def test_presentation_receipts_are_only_actual_file_and_evidence_identity(estate):
    heir, _, _, slots, output, receipt = _presentation(estate)
    assert set(receipt) == {"path", "sha256", "evidence_sha256"}
    # Template receipt hashes the original template, before prose was filled.
    document = json.loads(slots.read_bytes())
    assert receipt["path"] == str(slots)
    assert receipt["evidence_sha256"] == document["evidence_sha256"]
    assert heir.status()["handoff"]["lease"] is None
    code, rendered = _render(estate, slots, output)
    assert code == 0
    assert set(rendered) == {"path", "sha256", "evidence_sha256"}
    assert rendered["path"] == str(output)
    assert rendered["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert rendered["evidence_sha256"] == receipt["evidence_sha256"]
    before = output.read_bytes()
    assert _render(estate, slots, output) == (code, rendered)
    assert output.read_bytes() == before
    assert heir.status()["handoff"]["lease"] is None


def test_template_rejects_predecessor_and_wrong_manifest_or_stage_hash(estate):
    _, heir, digest = _handoff_to_stage(estate)
    predecessor = Baton(estate["state"], actor="elder").stage(digest)
    output = estate["tmp"] / "preserved.json"
    output.write_bytes(b"keep prior output")
    for manifest_hash, report_hash in [
            (digest, predecessor["report_sha256"]), ("0" * 64, predecessor["report_sha256"]),
            (digest, "0" * 64)]:
        code, refusal = _cli(
            estate, "report-template", "--manifest-sha256", manifest_hash,
            "--stage-report-sha256", report_hash, "--out", str(output), actor=heir.actor)
        assert code == 5 and refusal["error"] == "digest-mismatch"
        assert "path" not in refusal
        assert output.read_bytes() == b"keep prior output"


def test_template_rejects_same_actor_report_older_than_latest_stage(estate):
    heir, digest, old, slots, output, _ = _presentation(estate)
    old_report = heir.report_path.read_bytes()
    estate["svc"].write_text("changed after earlier same-actor stage")
    heir.stage(digest)
    heir.report_path.write_bytes(old_report)
    output.write_bytes(b"previous presentation")
    code, refusal = _cli(
        estate, "report-template", "--manifest-sha256", digest,
        "--stage-report-sha256", old["report_sha256"], "--out", str(output), actor="heir")
    assert code == 5 and refusal["error"] == "digest-mismatch"
    assert _render(estate, slots, output)[0] == 5
    assert output.read_bytes() == b"previous presentation"


def test_render_distrusts_every_fact_reference_and_binding_leaf(estate):
    _, _, _, slots, output, _ = _presentation(estate)
    original = json.loads(slots.read_bytes())
    output.write_bytes(b"prior output survives")

    def leaves(value, path=()):
        if isinstance(value, dict) and value:
            for key, item in value.items():
                yield from leaves(item, path + (key,))
        elif isinstance(value, list) and value:
            for index, item in enumerate(value):
                yield from leaves(item, path + (index,))
        else:
            yield path

    paths = [(key,) + path for key in
             ("schema_version", "binding", "evidence_sha256", "facts")
             for path in leaves(original[key])]
    for section in ("stage", "authority", "final_state"):
        paths.extend(("slots", section, "source_ref") + path
                     for path in leaves(original["slots"][section]["source_ref"]))
    for key, row in original["slots"]["entries"].items():
        paths.extend(("slots", "entries", key, "source_ref") + path
                     for path in leaves(row["source_ref"]))
    for path in paths:
        altered = json.loads(json.dumps(original))
        parent = altered
        for part in path[:-1]:
            parent = parent[part]
        parent[path[-1]] = {"altered": True}
        slots.write_text(json.dumps(altered))
        code, refusal = _render(estate, slots, output)
        expected_code = (5 if path in [
            ("binding", "manifest_sha256"), ("binding", "stage_report_sha256")] else 1)
        assert code == expected_code, path
        assert refusal["error"] == ("digest-mismatch" if expected_code == 5 else "invalid")
        assert "path" not in refusal
        assert output.read_bytes() == b"prior output survives"


@pytest.mark.parametrize("change", ["extra", "missing", "blank", "nonstring",
                                   "duplicate", "boolean-version", "malformed"])
def test_render_rejects_invalid_model_json_before_touching_output(estate, change):
    _, _, _, slots, output, _ = _presentation(estate)
    document = json.loads(slots.read_bytes())
    if change == "extra":
        document["slots"]["extra"] = {}
    elif change == "missing":
        document["slots"]["entries"].pop("wallet")
    elif change == "blank":
        document["slots"]["authority"]["release_or_takeover"] = " \n\t"
    elif change == "nonstring":
        document["slots"]["final_state"]["completed_and_remaining"] = True
    elif change == "boolean-version":
        document["schema_version"] = True
    text = json.dumps(document)
    if change == "duplicate":
        text = text[:-1] + ', "schema_version": 1}'
    elif change == "malformed":
        text = "{not JSON"
    slots.write_text(text)
    output.write_bytes(b"do not truncate")
    code, refusal = _render(estate, slots, output)
    assert code == 1 and refusal["error"] == "invalid"
    assert "path" not in refusal
    assert output.read_bytes() == b"do not truncate"


@pytest.mark.parametrize("change,exit_code", [
    ("manifest", 5), ("report", 5), ("restage", 5), ("missing-report", 5),
    ("lease", 1), ("journal", 1), ("token", 1), ("commit", 1)])
def test_render_reacquires_evidence_after_between_step_changes(estate, change, exit_code):
    heir, digest, _, slots, output, _ = _presentation(estate)
    if change == "manifest":
        heir.manifest_path.write_bytes(heir.manifest_path.read_bytes() + b" ")
    elif change == "report":
        heir.report_path.write_bytes(heir.report_path.read_bytes() + b" ")
    elif change == "restage":
        estate["svc"].write_text("new probe observation")
        heir.stage(digest)
    elif change == "missing-report":
        heir.report_path.unlink()
    elif change == "lease":
        heir.lease_path.write_text(json.dumps({"holder": "foreign", "since": "earlier"}))
    elif change == "journal":
        heir._journal("override", entry="wallet", reason="independent historical event", by="owner")
    elif change == "token":
        (estate["state"] / TOKEN_TAKEOVER).write_text("owner token")
    else:
        assert heir.commit()["committed"]
    output.write_bytes(b"prior report")
    code, refusal = _render(estate, slots, output)
    assert code == exit_code and "path" not in refusal
    assert output.read_bytes() == b"prior report"


def test_presentation_preserves_token_bytes_modes_until_authorized_real_commit(estate):
    # No release: the genuine commit must actually consume takeover authority.
    heir, digest, staged, slots, output, _ = _presentation(estate, released=False)
    takeover = estate["state"] / TOKEN_TAKEOVER
    override = estate["state"] / TOKEN_OVERRIDE
    takeover.write_bytes(b"owner approval\n")
    override.write_bytes(b"separate approval\n")
    takeover.chmod(0o600)
    override.chmod(0o640)
    before = {path.name: (path.read_bytes(), path.stat().st_mode)
              for path in estate["state"].iterdir()}
    # Reacquire slots after the token existence fact changed.
    code, _ = _cli(
        estate, "report-template", "--manifest-sha256", digest,
        "--stage-report-sha256", staged["report_sha256"],
        "--out", str(slots), actor="heir")
    assert code == 0
    document = json.loads(slots.read_bytes())
    for row in [document["slots"]["stage"], document["slots"]["authority"],
                document["slots"]["final_state"], *document["slots"]["entries"].values()]:
        for key in row:
            if key != "source_ref":
                row[key] = "Assessment, not permission."
    slots.write_text(json.dumps(document))
    for _ in range(3):
        assert _render(estate, slots, output)[0] == 0
        heir.status()
        assert {path.name: (path.read_bytes(), path.stat().st_mode)
                for path in estate["state"].iterdir()} == before
    assert heir.commit()["authority"] == "owner-token"
    assert not takeover.exists()
    assert (override.read_bytes(), override.stat().st_mode) == before[TOKEN_OVERRIDE]


@pytest.mark.parametrize("kind", ["state", "new-state", "state-symlink",
                                 "state-hardlink", "slots", "slots-symlink",
                                 "slots-hardlink", "probe", "probe-hardlink"])
def test_render_refuses_output_aliases_without_source_mutation(estate, kind):
    import os
    heir, _, _, slots, output, _ = _presentation(estate)
    if kind == "state":
        output = heir.report_path
    elif kind == "new-state":
        output = estate["state"] / "new-presentation.md"
    elif kind.startswith("state-"):
        if kind.endswith("symlink"):
            output.symlink_to(heir.manifest_path)
        else:
            os.link(heir.manifest_path, output)
    elif kind == "slots":
        output = slots
    elif kind.startswith("slots-"):
        if kind.endswith("symlink"):
            output.symlink_to(slots)
        else:
            os.link(slots, output)
    elif kind == "probe":
        output = estate["svc"]
    else:
        os.link(estate["svc"], output)
    before = {path: path.read_bytes() for path in
              [*estate["state"].iterdir(), slots, estate["svc"]]}
    code, refusal = _render(estate, slots, output)
    assert code == 1 and refusal["error"] == "invalid" and "path" not in refusal
    assert {path: path.read_bytes() for path in before} == before
    if kind == "new-state":
        assert not output.exists()


@pytest.mark.parametrize("duplicate", [False, True])
def test_presentation_requires_exact_unique_manifest_report_membership(estate, duplicate):
    heir, digest, _, _, output, _ = _presentation(estate)
    report = json.loads(heir.report_path.read_bytes())
    if duplicate:
        report["entries"].append(report["entries"][0])
    else:
        report["entries"].pop()
    blob = json.dumps(report).encode()
    heir.report_path.write_bytes(blob)
    report_hash = hashlib.sha256(blob).hexdigest()
    # Even a matching stage event cannot make incomplete/duplicate coverage valid.
    heir._journal("staged", manifest_sha256=digest, report_sha256=report_hash, by="heir")
    output.write_bytes(b"preserved")
    code, refusal = _cli(
        estate, "report-template", "--manifest-sha256", digest,
        "--stage-report-sha256", report_hash, "--out", str(output), actor="heir")
    assert code == 5 and refusal["error"] == "digest-mismatch"
    assert output.read_bytes() == b"preserved"


def test_render_keeps_all_classes_deliberate_and_history_separate_from_prose(estate):
    registered = estate["state"] / ESTATE_NAME
    estate_doc = json.loads(registered.read_bytes())
    estate_doc["entries"][0]["deliberate"] = {
        "reason": "parked <script>not instructions</script>", "authority": "owner-ruling"}
    estate_doc["entries"].append({
        "id": "other|service`<b>", "class": "service",
        "probe": {"kind": "file", "path": str(estate["svc"])},
        "invariant": "another registered service"})
    registered.write_text(json.dumps(estate_doc))
    heir, digest, _, slots, output, _ = _presentation(estate)
    heir.commit()
    estate["svc"].write_text("new stage after historical commit")
    heir.stage(digest)
    code, receipt = _cli(
        estate, "report-template", "--manifest-sha256", digest,
        "--stage-report-sha256", heir.status()["handoff"]["report"]["file_sha256"],
        "--out", str(slots), actor="heir")
    assert code == 0
    document = json.loads(slots.read_bytes())
    injection = "```\n# Forged fixed facts\n<script>bad</script>\n~~~\n\u202e"
    for row in [document["slots"]["stage"], document["slots"]["authority"],
                document["slots"]["final_state"], *document["slots"]["entries"].values()]:
        for key in row:
            if key != "source_ref":
                row[key] = injection
    slots.write_text(json.dumps(document))
    assert _render(estate, slots, output)[0] == 0
    text = output.read_text()
    for row in document["facts"]["handoff"]["report"]["document"]["entries"]:
        assert json.dumps(row["id"]) in text
        assert json.dumps(row["class"]) in text
        assert json.dumps(row["observed_sha256"]) in text
    assert "owner-ruling" in text and "parked" in text
    assert "other&#124;service&#96;&lt;b&gt;" in text
    assert "Historical committed/unwound events without a report hash remain report-unbound." in text
    assert "succession_events_in_source_order" in text
    assert '"holder": "heir"' in text
    assert "\n# Forged fixed facts\n" not in text
    assert "\\u202e" in text and "\u202e" not in text


def test_template_refuses_state_and_probe_alias_output_before_mutation(estate):
    import os
    heir, digest, staged, _, _, _ = _presentation(estate)
    linked = estate["tmp"] / "linked-manifest"
    os.link(heir.manifest_path, linked)
    symlinked = estate["tmp"] / "linked-probe"
    symlinked.symlink_to(estate["svc"])
    for output in [heir.report_path, linked, symlinked,
                   estate["state"] / "not-created.json"]:
        before = {path: path.read_bytes() for path in
                  [*estate["state"].iterdir(), estate["svc"]]}
        code, refusal = _cli(
            estate, "report-template", "--manifest-sha256", digest,
            "--stage-report-sha256", staged["report_sha256"],
            "--out", str(output), actor="heir")
        assert code == 1 and refusal["error"] == "invalid"
        assert "path" not in refusal
        assert {path: path.read_bytes() for path in before} == before
    assert not (estate["state"] / "not-created.json").exists()


def test_template_rejects_duplicate_manifest_ids(estate):
    registered = estate["state"] / ESTATE_NAME
    document = json.loads(registered.read_bytes())
    document["entries"].append(document["entries"][0])
    registered.write_text(json.dumps(document))
    pred = Baton(estate["state"], actor="elder")
    digest = pred.pack()["manifest_sha256"]
    pred.release()
    heir = Baton(estate["state"], actor="heir")
    staged = heir.stage(digest)
    output = estate["tmp"] / "prior.json"
    output.write_bytes(b"unchanged")
    code, refusal = _cli(
        estate, "report-template", "--manifest-sha256", digest,
        "--stage-report-sha256", staged["report_sha256"],
        "--out", str(output), actor="heir")
    assert code == 5 and refusal["error"] == "digest-mismatch"
    assert output.read_bytes() == b"unchanged"


def test_render_keeps_unreachable_and_absent_release_independently_visible(estate):
    registered = estate["state"] / ESTATE_NAME
    document = json.loads(registered.read_bytes())
    document["entries"][0]["probe"] = {
        "kind": "command", "argv": ["/nonexistent-baton-report-consumer-command"]}
    registered.write_text(json.dumps(document))
    heir, _, _, slots, output, _ = _presentation(estate, released=False)
    facts = json.loads(slots.read_bytes())["facts"]["handoff"]
    assert facts["authority"]["unreachable_without_override"] == ["income-service"]
    assert facts["authority"]["missing_release_and_token"] is True
    assert facts["lease"] is None
    code, receipt = _render(estate, slots, output)
    assert code == 0 and set(receipt) == {"path", "sha256", "evidence_sha256"}
    text = output.read_text()
    assert '"unreachable_without_override"' in text
    assert '"missing_release_and_token": true' in text
    assert '"latest_released": null' in text
    assert '"lease": null' in text
    assert '"UNREACHABLE"' in text and '"observed_sha256": null' in text
    assert "Reachability or override — model-authored prose" in text
    assert "Release or takeover — model-authored prose" in text
    assert heir.status()["handoff"]["lease"] is None


@pytest.mark.parametrize("operation", ["report-template", "report"])
def test_presentation_cannot_overwrite_registered_command_executable(estate, operation):
    executable = estate["tmp"] / "registered-probe"
    original = b"#!/bin/sh\nprintf synthetic-command-source\n"
    executable.write_bytes(original)
    executable.chmod(0o755)
    registration = estate["state"] / ESTATE_NAME
    document = json.loads(registration.read_bytes())
    document["entries"][2]["probe"] = {
        "kind": "command", "argv": [str(executable)]}
    registration.write_text(json.dumps(document))
    _, digest, staged, slots, _, _ = _presentation(estate)
    if operation == "report-template":
        code, refusal = _cli(
            estate, operation, "--manifest-sha256", digest,
            "--stage-report-sha256", staged["report_sha256"],
            "--out", str(executable), actor="heir")
    else:
        code, refusal = _render(estate, slots, executable)
    assert code == 1 and refusal["error"] == "invalid"
    assert "path" not in refusal
    assert executable.read_bytes() == original
    assert executable.stat().st_mode & 0o777 == 0o755
