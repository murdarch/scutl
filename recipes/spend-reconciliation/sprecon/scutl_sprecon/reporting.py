"""Deterministic presentation of an operator-owned completed-pass source view.

Witness hashes detect changes; authority is the read-only operator channel, not
those self-hashes. This module neither runs an audit nor interprets its numbers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile

DEFAULT_SOURCE = "/run/report-sources/index.json"
_ENV = {"SPRECON_ROOT", "SPRECON_WALLET", "SPRECON_RPC",
        "SPRECON_PROV", "SPRECON_PSERV"}
_ENVELOPE = {"records_in", "records_categorized", "conservation_ok", "legs",
             "invariant", "findings", "open_escalations", "as_of", "block_window",
             "anchor", "scope", "counted_records", "excluded_books", "checkpoints",
             "latest_findings", "pending_deadlines"}
_PROSE = ("interpretation", "unresolved_scope", "next_authorized_step")


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _pairs(items):
    doc = {}
    for key, value in items:
        if key in doc:
            raise ValueError("duplicate JSON key: " + key)
        doc[key] = value
    return doc


def _json(data):
    def invalid(value):
        raise ValueError("nonfinite JSON value: " + value)
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid UTF-8 JSON") from exc


def _keys(doc, keys, label):
    if type(doc) is not dict or set(doc) != set(keys):
        raise ValueError(label + " has incorrect keys")


def _path(value):
    if (type(value) is not str or not value.startswith("/")
            or str(Path(value)) != value or ".." in Path(value).parts):
        raise ValueError("source paths must be canonical absolute paths")
    return Path(value)


def _no_symlinks(path):
    for part in reversed((path, *path.parents)):
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError("symlink refused: " + str(part))


def _identity(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size,
            st.st_mtime_ns, st.st_ctime_ns)


def _read(path):
    path = Path(path).absolute()
    _no_symlinks(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("regular file required: " + str(path))
            data = stream.read()
            after = os.fstat(stream.fileno())
        _no_symlinks(path)
        if (_identity(before) != _identity(after)
                or _identity(after) != _identity(path.lstat())):
            raise ValueError("file changed while reading: " + str(path))
        return data, after
    except OSError as exc:
        raise ValueError("cannot read source file: " + str(path)) from exc


def _check_backing(path, expected):
    path = _path(path)
    _no_symlinks(path)
    if type(expected) is not dict:
        raise ValueError("invalid backing identity")
    kind = expected.get("kind")
    if kind == "absent":
        _keys(expected, {"kind"}, "absent backing")
        if os.path.lexists(path):
            raise ValueError("backing is no longer absent: " + str(path))
        return
    if kind == "directory":
        _keys(expected, {"kind", "mode"}, "directory backing")
        try:
            current = path.lstat()
        except OSError as exc:
            raise ValueError("missing backing directory") from exc
        if not stat.S_ISDIR(current.st_mode):
            raise ValueError("backing directory changed type")
    elif kind == "file":
        _keys(expected, {"kind", "sha256", "bytes", "mode"}, "file backing")
        if (type(expected["bytes"]) is not int or expected["bytes"] < 0
                or type(expected["sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", expected["sha256"])):
            raise ValueError("invalid backing file digest or size")
        data, current = _read(path)
        if len(data) != expected["bytes"] or _hash(data) != expected["sha256"]:
            raise ValueError("backing bytes changed: " + str(path))
    else:
        raise ValueError("unsupported backing kind")
    if (type(expected["mode"]) is not int or not 0 <= expected["mode"] <= 0o7777
            or stat.S_IMODE(current.st_mode) != expected["mode"]):
        raise ValueError("backing mode changed: " + str(path))


def _command(command, environment):
    if type(command) is not str or not command.strip():
        raise ValueError("missing actual reconcile command")
    try:
        words = shlex.split(command)
    except ValueError as exc:
        raise ValueError("invalid reconcile command") from exc
    if words[:1] == ["env"]:
        words = words[1:]
        unset = set()
        while words[:1] == ["-u"]:
            if len(words) < 2 or words[1] not in {"SPRECON_PSERV", "SPRECON_PROV"}:
                raise ValueError("unsupported environment removal")
            if words[1] in unset:
                raise ValueError("duplicate environment removal")
            unset.add(words[1])
            words = words[2:]
        assigned = {}
        while words and "=" in words[0]:
            key, value = words.pop(0).split("=", 1)
            if key not in _ENV or key in assigned:
                raise ValueError("ambiguous environment assignment")
            assigned[key] = value
        effective = {key: "" for key in unset}
        effective.update(assigned)
        if effective != environment:
            raise ValueError("command environment differs from declared world")
    else:
        raise ValueError("source command requires an explicit declared environment")
    if words[:2] != ["/component/bin/sprecon", "reconcile"]:
        raise ValueError("source command is not the sealed reconcile")
    if len(words) == 2:
        return None
    if len(words) == 4 and words[2] == "--billing-file":
        return str(_path(words[3]))
    raise ValueError("source command is compound or ambiguous")


def _envelope(result):
    _keys(result, _ENVELOPE, "completed reconcile envelope")
    for name in ("records_in", "records_categorized", "open_escalations"):
        if type(result[name]) is not int or result[name] < 0:
            raise ValueError("invalid envelope count")
    if type(result["conservation_ok"]) is not bool:
        raise ValueError("invalid conservation indicator")
    _keys(result["legs"], {"books", "chain", "billing"}, "legs")
    if any(type(value) is not str for value in result["legs"].values()):
        raise ValueError("invalid leg value")
    if type(result["as_of"]) is not str or not result["as_of"]:
        raise ValueError("missing pass decision time")
    _keys(result["block_window"], {"from_block", "through_block"}, "block window")
    if any(type(value) is not int or value < 0 for value in result["block_window"].values()):
        raise ValueError("invalid block window")
    if result["anchor"] is not None and type(result["anchor"]) is not dict:
        raise ValueError("invalid anchor envelope")
    invariant = result["invariant"]
    if type(invariant) is not dict or type(invariant.get("checked")) is not bool:
        raise ValueError("invalid invariant envelope")
    _keys(invariant, {"checked", "expected", "actual", "residue", "explained_by_findings"}
          if invariant["checked"] else {"checked"}, "invariant")
    if any(type(value) is not str for key, value in invariant.items() if key != "checked"):
        raise ValueError("invalid invariant fact")
    for name in ("findings", "latest_findings", "excluded_books", "pending_deadlines"):
        if type(result[name]) is not list or any(type(value) is not dict for value in result[name]):
            raise ValueError("invalid " + name)
    counts = result["counted_records"]
    if type(counts) is not dict or any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError("invalid decoded counts")
    _keys(result["checkpoints"], {"line_unit", "value"}, "checkpoints")
    if (result["checkpoints"]["line_unit"] != "physical_lines"
            or type(result["checkpoints"]["value"]) is not dict):
        raise ValueError("invalid physical checkpoint envelope")


def _view(source_path):
    raw, _ = _read(source_path)
    source = _json(raw)
    _keys(source, {"schema", "run_id", "arm", "task_started_ns", "worlds"}, "source")
    if (type(source["schema"]) is not int or source["schema"] != 1
            or type(source["run_id"]) is not str or not source["run_id"]
            or source["arm"] != "candidate"
            or type(source["task_started_ns"]) is not int or source["task_started_ns"] <= 0
            or type(source["worlds"]) is not list or not source["worlds"]):
        raise ValueError("invalid source identity")
    ids, roots, facts, values, checks, protected = set(), set(), [], {}, [], []
    for world in source["worlds"]:
        _keys(world, {"id", "environment", "latest"}, "world")
        wid, env, witness = world["id"], world["environment"], world["latest"]
        if type(wid) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", wid) or wid in ids:
            raise ValueError("invalid or duplicate world id")
        ids.add(wid)
        _keys(env, _ENV, "world environment")
        if any(type(value) is not str for value in env.values()) or not env["SPRECON_RPC"]:
            raise ValueError("invalid world environment")
        root, wallet = _path(env["SPRECON_ROOT"]), _path(env["SPRECON_WALLET"])
        if root in roots:
            raise ValueError("duplicate world root")
        roots.add(root)
        world_roots = [root, wallet]
        books = {"spend": str(wallet / "spend.log")}
        required = {str(root / name) for name in ("findings.log", "checkpoints.json", "funding.log")}
        required.update({str(wallet / "spend.log"), str(wallet / "keystore.json")})
        if env["SPRECON_PSERV"]:
            pserv = _path(env["SPRECON_PSERV"])
            world_roots.append(pserv)
            books.update(earnings=str(pserv / "earnings.log"), refunds=str(pserv / "refunds.log"))
            required.update((books["earnings"], books["refunds"]))
        if env["SPRECON_PROV"]:
            prov = _path(env["SPRECON_PROV"])
            world_roots.append(prov)
            books["instances"] = str(prov / "instances.log")
            required.add(books["instances"])
        if any(base == prior or base in prior.parents or prior in base.parents
               for base in world_roots for prior in protected):
            raise ValueError("world roots overlap another declared world")
        protected.extend(world_roots)
        _keys(witness, {"run_id", "world_id", "sequence", "observed_ns", "command",
                        "result", "backing", "witness_sha256"}, "latest witness")
        if (witness["run_id"] != source["run_id"] or witness["world_id"] != wid
                or type(witness["sequence"]) is not int or witness["sequence"] <= 0
                or type(witness["observed_ns"]) is not int
                or witness["observed_ns"] < source["task_started_ns"]):
            raise ValueError("stale or misbound witness")
        unsigned = {key: value for key, value in witness.items() if key != "witness_sha256"}
        if witness["witness_sha256"] != _hash(_canonical(unsigned)):
            raise ValueError("witness integrity mismatch")
        statement = _command(witness["command"], env)
        if statement:
            required.add(statement)
        result, backing = witness["result"], witness["backing"]
        _envelope(result)
        _keys(result["scope"], {"wallet_address", "billing_statement_supplied", "books"}, "scope")
        scoped = result["scope"]["books"]
        if type(scoped) is not dict or "spend" not in scoped:
            raise ValueError("missing audited book scope")
        expected_names = set(books) - {"refunds"}
        if set(scoped) not in (expected_names, set(books)):
            raise ValueError("book scope differs from declared world")
        if result["scope"]["billing_statement_supplied"] is not bool(statement):
            raise ValueError("statement scope differs from actual command")
        for name, entry in scoped.items():
            _keys(entry, {"path", "present", "counted", "invoice_joined"}, "book scope")
            if entry["path"] != books[name] or any(type(entry[key]) is not bool for key in ("present", "counted", "invoice_joined")):
                raise ValueError("book scope differs from declared world")
        if type(backing) is not dict or not required <= set(backing):
            raise ValueError("incomplete backing identities")
        for path, identity in backing.items():
            candidate = _path(path)
            if type(identity) is not dict:
                raise ValueError("invalid backing identity")
            if path in required and identity.get("kind") not in ("file", "absent"):
                raise ValueError("audited backing must be a regular file or absent")
            if (path == str(wallet / "keystore.json") or path == statement) and identity.get("kind") != "file":
                raise ValueError("completed reconcile input must be a regular file")
            if candidate in world_roots and identity.get("kind") != "directory":
                raise ValueError("declared root must be a directory")
            # Approval tokens are never read, even if a forged index requests it.
            if any(candidate != base / "approvals" and base / "approvals" in candidate.parents for base in world_roots):
                raise ValueError("approval token backing refused")
            if path not in required and candidate not in world_roots and candidate != root / "approvals":
                raise ValueError("undeclared backing path")
            if (candidate == root / "approvals"
                    and (type(identity) is not dict
                         or identity.get("kind") not in ("absent", "directory"))):
                raise ValueError("approval directory cannot be a file source")
            _check_backing(path, identity)
            checks.append((path, identity))
        for name, entry in scoped.items():
            if entry["present"] is (backing[books[name]]["kind"] == "absent"):
                raise ValueError("book presence contradicts backing identity")
        facts.append({"world": wid, "result": result})
        values.update({wid + "." + name: None for name in _PROSE})
    values.update({"overall.completed": None, "overall.remaining": None})
    doc = {"schema": 1, "sources_sha256": _hash(raw), "facts": facts, "values": values}
    return doc, raw, checks, protected


def source_view(source_path=DEFAULT_SOURCE):
    """Return the exact current template projection; never write state or output."""
    return _view(source_path)[0]


def _output(out, inputs, protected):
    path = _path(str(Path(out).absolute()))
    _no_symlinks(path)
    if path.exists() and not path.is_file():
        raise ValueError("output must be a regular file")
    for source in inputs:
        source = Path(source).absolute()
        if path == source or (path.exists() and os.path.samefile(path, source)):
            raise ValueError("output aliases an input")
    if any(path == base or base in path.parents for base in protected):
        raise ValueError("output would change audited or approval state")
    return path


def _write(path, data, source_path, raw, checks, inputs):
    # Validate everything before staging, then again immediately before replace.
    def current():
        if _read(source_path)[0] != raw:
            raise ValueError("operator source view changed")
        for source, expected in inputs:
            if _read(source)[0] != expected:
                raise ValueError("presentation input changed")
        for source, expected in checks:
            _check_backing(source, expected)
        _no_symlinks(path)
    current()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".sprecon-report-", delete=False) as stream:
            temporary = stream.name
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        current()
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)
    return {"path": str(path), "sha256": _hash(data), "evidence_sha256": _hash(raw)}


def template(source_path=DEFAULT_SOURCE, *, out):
    """Write a null-prose template only from a complete current source view."""
    doc, raw, checks, protected = _view(source_path)
    path = _output(out, [source_path], protected)
    data = (json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    return _write(path, data, source_path, raw, checks, [])


def render(source_path=DEFAULT_SOURCE, *, slots, values, out):
    """Regenerate facts and insert only the exact supplied nonempty prose slots."""
    doc, raw, checks, protected = _view(source_path)
    for input_path in (slots, values):
        candidate = _path(str(Path(input_path).absolute()))
        if any(candidate == base or base in candidate.parents for base in protected):
            raise ValueError("presentation inputs cannot read audited or approval state")
    slot_raw, _ = _read(slots)
    value_raw, _ = _read(values)
    supplied_slots, supplied_values = _json(slot_raw), _json(value_raw)
    if _canonical(supplied_slots) != _canonical(doc):
        raise ValueError("template differs from current source view")
    _keys(supplied_values, doc, "values object")
    _keys(supplied_values["values"], doc["values"], "prose values")
    filled = supplied_values["values"]
    if any(type(value) is not str or not value.strip() for value in filled.values()):
        raise ValueError("all prose slots require nonempty strings")
    regenerated = dict(doc, values=filled)
    if _canonical(supplied_values) != _canonical(regenerated):
        raise ValueError("values facts differ from current source view")
    lines = ["# Spend reconciliation report", "", "Sources SHA-256: " + doc["sources_sha256"], ""]
    for fact in doc["facts"]:
        wid = fact["world"]
        lines.extend(["## World " + wid, "", "### Completed reconcile envelope", "", "```json"])
        # JSON escaping also keeps embedded Markdown fences inside string data.
        envelope = json.dumps(fact["result"], indent=2, ensure_ascii=False, allow_nan=False)
        lines.extend([envelope, "```", ""])
        for name in _PROSE:
            lines.extend(["### " + wid + "." + name, "", filled[wid + "." + name], ""])
    for name in ("overall.completed", "overall.remaining"):
        lines.extend(["## " + name, "", filled[name], ""])
    path = _output(out, [source_path, slots, values], protected)
    return _write(path, "\n".join(lines).encode("utf-8"), source_path, raw, checks,
                  [(slots, slot_raw), (values, value_raw)])
