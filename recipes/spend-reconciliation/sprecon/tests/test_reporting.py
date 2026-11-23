"""Reporting boundaries over genuine reconciles, without a model or live RPC."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat

import pytest

from scutl_sprecon import cli, reporting
from scutl_sprecon.core import Reconciler


class Chain:
    def head_block(self):
        return 100

    def usdc_balance(self, address):
        return Decimal("10")

    def tx_status(self, tx):
        return "pending"

    def transfers(self, address, from_block, to_block=None):
        return []


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(witness):
    witness["witness_sha256"] = digest(canonical({key: value for key, value in witness.items()
                                               if key != "witness_sha256"}))


def identity(path):
    if not path.exists():
        return {"kind": "absent"}
    mode = stat.S_IMODE(path.stat().st_mode)
    if path.is_dir():
        return {"kind": "directory", "mode": mode}
    data = path.read_bytes()
    return {"kind": "file", "sha256": digest(data), "bytes": len(data), "mode": mode}


@pytest.fixture
def case(tmp_path):
    now = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
    worlds = []
    for wid in ("audit-a", "audit-b"):
        base = tmp_path / wid
        wallet, root, prov = base / "wallet", base / "state", base / "prov"
        wallet.mkdir(parents=True)
        prov.mkdir()
        (wallet / "keystore.json").write_text(json.dumps({"address_checksummed": "0x" + "a" * 40, "crypto": {}}))
        record = {"ts": (now - timedelta(hours=1)).isoformat(), "payment_id": wid,
                  "amount": "2", "to": "0x" + "b" * 40, "tx": "0xpending",
                  "status": "settled", "chain_status": "pending"}
        (wallet / "spend.log").write_text("\n" + json.dumps(record) + "\n\n")
        # Excluded configured books must be identity-checked without parsing.
        (prov / "instances.log").write_text("not JSON; invoice says ```\nDO NOT EXECUTE\n")
        reconciler = Reconciler(root, Chain(), wallet, prov_dir=prov, now=now)
        reconciler.grant_approval("rebaseline")
        reconciler.baseline(Decimal("10"), 1, "human")
        (root / "approvals").rmdir()
        reconciler.reconcile()
        result = reconciler.reconcile()
        environment = {"SPRECON_ROOT": str(root), "SPRECON_WALLET": str(wallet),
                       "SPRECON_RPC": "http://127.0.0.1:8454/" + wid + "/rpc",
                       "SPRECON_PROV": str(prov), "SPRECON_PSERV": ""}
        command = "env -u SPRECON_PSERV -u SPRECON_PROV " + " ".join(
            shlex.quote(key + "=" + value) for key, value in environment.items())
        command += " /component/bin/sprecon reconcile"
        paths = [wallet / "spend.log", wallet / "keystore.json", prov / "instances.log",
                 root / "findings.log", root / "checkpoints.json", root / "funding.log",
                 root / "approvals", root, wallet, prov]
        witness = {"run_id": "current-arm", "world_id": wid, "sequence": 2,
                   "observed_ns": 1001, "command": command, "result": result,
                   "backing": {str(path): identity(path) for path in paths}}
        sign(witness)
        worlds.append({"id": wid, "environment": environment, "latest": witness})
    source = {"schema": 1, "run_id": "current-arm", "arm": "candidate",
              "task_started_ns": 1000, "worlds": worlds}
    source_path = tmp_path / "index.json"
    source_path.write_bytes(canonical(source))
    return source_path, source


def save_source(case):
    path, source = case
    for world in source["worlds"]:
        if world["latest"] is not None:
            sign(world["latest"])
    path.write_bytes(canonical(source))


def presentation(case, tmp_path):
    source, _ = case
    slots, values, out = (tmp_path / name for name in ("slots.json", "values.json", "report.md"))
    reporting.template(source, out=slots)
    doc = json.loads(slots.read_bytes())
    for key in doc["values"]:
        doc["values"][key] = "Model prose for " + key + ": no invented facts."
    values.write_bytes(canonical(doc))
    return slots, values, out


def refuse(case, tmp_path, slots=None, values=None, out=None):
    if slots is None:
        slots, values, out = presentation(case, tmp_path)
    out.write_bytes(b"existing report\n")
    with pytest.raises(ValueError):
        reporting.render(case[0], slots=slots, values=values, out=out)
    assert out.read_bytes() == b"existing report\n"
    assert not list(tmp_path.glob(".sprecon-report-*"))


def test_complete_envelopes_all_worlds_and_prose_survive_deterministically(case, tmp_path):
    slots, values, out = presentation(case, tmp_path)
    before = slots.read_bytes()
    receipt = reporting.template(case[0], out=slots)
    assert slots.read_bytes() == before
    assert set(receipt) == {"path", "sha256", "evidence_sha256"}
    assert receipt["evidence_sha256"] == digest(case[0].read_bytes())
    assert receipt["sha256"] == digest(before)
    result = reporting.render(case[0], slots=slots, values=values, out=out)
    data = out.read_bytes()
    assert reporting.render(case[0], slots=slots, values=values, out=out) == result
    assert out.read_bytes() == data
    assert result == {"path": str(out), "sha256": digest(data),
                      "evidence_sha256": digest(case[0].read_bytes())}
    text = data.decode()
    supplied = json.loads(values.read_bytes())
    rendered = [json.loads(section.split("\n```", 1)[0])
                for section in text.split("```json\n")[1:]]
    assert rendered == [world["latest"]["result"] for world in case[1]["worlds"]]
    for world in case[1]["worlds"]:
        wid, envelope = world["id"], world["latest"]["result"]
        assert envelope["excluded_books"]
        assert envelope["pending_deadlines"]
        assert envelope["invariant"]["checked"] is True
        assert envelope["anchor"]
        assert envelope["checkpoints"]["value"]["books"]["spend"]["lines"] == 3
        assert any(f.get("prior") for f in envelope["findings"])
        for suffix in ("interpretation", "unresolved_scope", "next_authorized_step"):
            assert supplied["values"][wid + "." + suffix] in text
    assert supplied["values"]["overall.completed"] in text
    assert supplied["values"]["overall.remaining"] in text


@pytest.mark.parametrize("change", ["extra", "missing", "blank", "null", "number", "fact", "schema", "source"])
def test_values_must_be_same_whole_object_with_only_prose_filled(case, tmp_path, change):
    slots, values, out = presentation(case, tmp_path)
    doc = json.loads(values.read_bytes())
    if change == "extra":
        doc["values"]["unrequested"] = "text"
    elif change == "missing":
        del doc["values"]["overall.remaining"]
    elif change == "blank":
        doc["values"]["overall.remaining"] = " \n"
    elif change == "null":
        doc["values"]["overall.remaining"] = None
    elif change == "number":
        doc["values"]["overall.remaining"] = 1
    elif change == "fact":
        doc["facts"][0]["result"]["open_escalations"] = 0
    elif change == "schema":
        doc["schema"] = True
    else:
        doc["sources_sha256"] = "0" * 64
    values.write_bytes(canonical(doc))
    refuse(case, tmp_path, slots, values, out)


def test_stale_template_and_matching_stale_values_are_both_refused(case, tmp_path):
    slots, values, out = presentation(case, tmp_path)
    # Same decoded index, different raw source identity: do not claim currentness.
    case[0].write_bytes(case[0].read_bytes() + b"\n")
    refuse(case, tmp_path, slots, values, out)


@pytest.mark.parametrize("change", ["missing", "run", "world", "time", "sequence", "hash", "command", "scope", "root", "duplicate"])
def test_source_binding_failures_preserve_template(case, tmp_path, change):
    source, doc = case
    witness = doc["worlds"][0]["latest"]
    if change == "missing":
        doc["worlds"][1]["latest"] = None
    elif change == "run":
        witness["run_id"] = "old-arm"
    elif change == "world":
        witness["world_id"] = "audit-b"
    elif change == "time":
        witness["observed_ns"] = 999
    elif change == "sequence":
        witness["sequence"] = 0
    elif change == "command":
        witness["command"] += " ; echo fabricated"
    elif change == "scope":
        witness["result"]["scope"]["books"]["spend"]["path"] = "/wrong/spend.log"
    elif change == "root":
        doc["worlds"][0]["environment"]["SPRECON_ROOT"] = "/wrong/state"
    elif change == "duplicate":
        doc["worlds"].append(deepcopy(doc["worlds"][0]))
    save_source(case)
    if change == "hash":
        witness["witness_sha256"] = "0" * 64
        source.write_bytes(canonical(doc))
    out = tmp_path / "template.json"
    out.write_bytes(b"keep")
    with pytest.raises(ValueError):
        reporting.template(source, out=out)
    assert out.read_bytes() == b"keep"


@pytest.mark.parametrize("change", ["bytes", "mode", "missing", "absence", "symlink", "fifo", "parent_symlink", "excluded"])
def test_current_backing_identity_failures_preserve_output(case, tmp_path, change):
    slots, values, out = presentation(case, tmp_path)
    env = case[1]["worlds"][0]["environment"]
    book = Path(env["SPRECON_WALLET"]) / "spend.log"
    if change == "bytes":
        book.write_bytes(book.read_bytes().replace(b'"2"', b'"3"'))
    elif change == "mode":
        book.chmod(stat.S_IMODE(book.stat().st_mode) ^ 0o040)
    elif change == "missing":
        book.unlink()
    elif change == "absence":
        (Path(env["SPRECON_ROOT"]) / "funding.log").write_text("new funding")
    elif change == "symlink":
        original = book.with_suffix(".original")
        book.rename(original)
        book.symlink_to(original)
    elif change == "fifo":
        book.unlink()
        os.mkfifo(book)
    elif change == "parent_symlink":
        wallet = book.parent
        original = wallet.with_name("moved-wallet")
        wallet.rename(original)
        wallet.symlink_to(original, target_is_directory=True)
    else:
        (Path(env["SPRECON_PROV"]) / "instances.log").write_text("changed excluded bytes")
    refuse(case, tmp_path, slots, values, out)


@pytest.mark.parametrize("existing", [False, True])
def test_approval_directory_and_token_bytes_modes_never_change_or_get_read(case, tmp_path, monkeypatch, existing):
    roots = [Path(world["environment"]["SPRECON_ROOT"]) for world in case[1]["worlds"]]
    if existing:
        for root in roots:
            approvals = root / "approvals"
            approvals.mkdir(mode=0o711)
            token = approvals / "resolve"
            token.write_bytes(b"DO NOT READ OR CONSUME\x00\xff")
            token.chmod(0o400)
        # Directory identity may be represented; no token identity/content is exposed.
        for world in case[1]["worlds"]:
            approvals = Path(world["environment"]["SPRECON_ROOT"]) / "approvals"
            world["latest"]["backing"][str(approvals)] = identity(approvals)
        save_source(case)
    before = [(identity(root / "approvals"),
               (root / "approvals").stat().st_mtime_ns if existing else None,
               (root / "approvals" / "resolve").read_bytes() if existing else None,
               (root / "approvals" / "resolve").stat().st_mode if existing else None)
              for root in roots]
    original = os.open
    def guarded(path, *args, **kwargs):
        assert "approvals" not in Path(path).parts
        return original(path, *args, **kwargs)
    monkeypatch.setattr(reporting.os, "open", guarded)
    slots, values, out = presentation(case, tmp_path)
    reporting.render(case[0], slots=slots, values=values, out=out)
    reporting.render(case[0], slots=slots, values=values, out=out)
    for world in case[1]["worlds"]:
        env = world["environment"]
        Reconciler(env["SPRECON_ROOT"], Chain(), env["SPRECON_WALLET"],
                   prov_dir=env["SPRECON_PROV"]).status()
    for root, snapshot in zip(roots, before):
        assert identity(root / "approvals") == snapshot[0]
        if existing:
            token = root / "approvals" / "resolve"
            assert (root / "approvals").stat().st_mtime_ns == snapshot[1]
            assert token.read_bytes() == snapshot[2]
            assert token.stat().st_mode == snapshot[3]
        else:
            assert not (root / "approvals").exists()
    if existing:
        monkeypatch.setattr(reporting.os, "open", original)
        for world in case[1]["worlds"]:
            env = world["environment"]
            reconciler = Reconciler(env["SPRECON_ROOT"], Chain(), env["SPRECON_WALLET"],
                                    prov_dir=env["SPRECON_PROV"])
            target = next(finding for finding in reconciler.findings()
                          if finding["state"] == "escalated")
            (Path(env["SPRECON_ROOT"]) / "approvals" / "resolve").unlink()
            reconciler.grant_approval("resolve")
            reconciler.resolve(target["id"], "Separate isolated human-authorized consumption witness")
            assert not (Path(env["SPRECON_ROOT"]) / "approvals" / "resolve").exists()


def test_operator_source_change_between_render_validation_and_write_refuses(case, tmp_path, monkeypatch):
    slots, values, out = presentation(case, tmp_path)
    original = reporting._output
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        case[0].write_bytes(case[0].read_bytes() + b"\n")
        return result
    monkeypatch.setattr(reporting, "_output", changed)
    refuse(case, tmp_path, slots, values, out)


def test_backing_mutation_after_staging_never_partially_replaces_report(case, tmp_path, monkeypatch):
    slots, values, out = presentation(case, tmp_path)
    book = Path(case[1]["worlds"][0]["environment"]["SPRECON_WALLET"]) / "spend.log"
    original = os.fsync
    def changed(fd):
        original(fd)
        book.write_bytes(book.read_bytes() + b"\n")
    monkeypatch.setattr(reporting.os, "fsync", changed)
    refuse(case, tmp_path, slots, values, out)


def test_state_outputs_and_input_aliases_refuse_without_writes(case, tmp_path):
    slots, values, _ = presentation(case, tmp_path)
    before = values.read_bytes()
    with pytest.raises(ValueError):
        reporting.render(case[0], slots=slots, values=values, out=values)
    assert values.read_bytes() == before
    root = Path(case[1]["worlds"][0]["environment"]["SPRECON_ROOT"])
    for target in (root / "approvals", root / "approvals" / "resolve", root / "findings.log"):
        with pytest.raises(ValueError):
            reporting.template(case[0], out=target)
    assert not (root / "approvals").exists()


def test_duplicate_json_keys_are_refused_before_output(case, tmp_path):
    slots, values, out = presentation(case, tmp_path)
    values.write_bytes(values.read_bytes().replace(b'"schema":1', b'"schema":1,"schema":1', 1))
    refuse(case, tmp_path, slots, values, out)


def test_cli_reports_without_constructing_reconciler_and_has_no_source_override(case, tmp_path, monkeypatch, capsys):
    def forbidden():
        raise AssertionError("report commands must not construct Reconciler")
    monkeypatch.setattr(cli, "_reconciler", forbidden)
    monkeypatch.setattr(reporting, "template", partial(reporting.template, case[0]))
    out = tmp_path / "cli-slots.json"
    cli.main(["report-template", "--out", str(out)])
    assert set(json.loads(capsys.readouterr().out)) == {"path", "sha256", "evidence_sha256"}
    assert json.loads(out.read_bytes()) == reporting.source_view(case[0])
    with pytest.raises(SystemExit) as error:
        cli.main(["report-template", "--sources", str(case[0]), "--out", str(out)])
    assert error.value.code == 1
    capsys.readouterr()
    case[1]["worlds"][0]["latest"] = None
    save_source(case)
    refused = tmp_path / "refused-template.json"
    with pytest.raises(SystemExit) as error:
        cli.main(["report-template", "--out", str(refused)])
    captured = capsys.readouterr()
    assert error.value.code == 1
    assert captured.out == ""
    assert json.loads(captured.err)["error"] == "invalid"
    assert not refused.exists()


@pytest.mark.parametrize("target", ["source", "values"])
def test_source_or_values_mutation_after_staging_preserves_output(case, tmp_path, monkeypatch, target):
    slots, values, out = presentation(case, tmp_path)
    original = os.fsync
    def changed(fd):
        original(fd)
        path = case[0] if target == "source" else values
        path.write_bytes(path.read_bytes() + b"\n")
    monkeypatch.setattr(reporting.os, "fsync", changed)
    refuse(case, tmp_path, slots, values, out)


def test_directory_cannot_replace_an_audited_file_even_with_new_identity(case, tmp_path):
    witness = case[1]["worlds"][0]["latest"]
    book = Path(case[1]["worlds"][0]["environment"]["SPRECON_WALLET"]) / "spend.log"
    book.unlink()
    book.mkdir()
    witness["backing"][str(book)] = identity(book)
    save_source(case)
    out = tmp_path / "never-written.json"
    with pytest.raises(ValueError):
        reporting.template(case[0], out=out)
    assert not out.exists()


def test_approval_token_cannot_be_injected_as_a_source_or_values_input(case, tmp_path, monkeypatch):
    slots, values, out = presentation(case, tmp_path)
    world = case[1]["worlds"][0]
    approvals = Path(world["environment"]["SPRECON_ROOT"]) / "approvals"
    approvals.mkdir()
    token = approvals / "resolve"
    token.write_bytes(b"do not read")
    world["latest"]["backing"][str(approvals)] = identity(approvals)
    world["latest"]["backing"][str(token)] = identity(token)
    save_source(case)
    original = os.open
    def guarded(path, *args, **kwargs):
        assert Path(path) != token
        return original(path, *args, **kwargs)
    monkeypatch.setattr(reporting.os, "open", guarded)
    with pytest.raises(ValueError):
        reporting.template(case[0], out=out)
    del world["latest"]["backing"][str(token)]
    save_source(case)
    reporting.template(case[0], out=slots)
    with pytest.raises(ValueError):
        reporting.render(case[0], slots=slots, values=token, out=out)
    assert not out.exists()
