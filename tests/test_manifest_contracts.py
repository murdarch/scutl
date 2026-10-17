"""Manifest/CLI contract gate (cst-mdiz.22).

Every public command string in every recipe manifest must parse against
the real argparse tree of the console entrypoint it names: no unknown
flags, no missing required flags, no unexpanded prose ellipses. Declared
state_dir env names must agree with the component's own state selection,
and fresh-status exit claims are pinned to audited code behavior.

The argparse trees are extracted statically (ast) from each entrypoint
function, so the gate runs without installing any component package.
"""

import ast
import re
import shlex
import sys
from pathlib import Path

import pytest
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # py<3.11
    import tomli as tomllib

REPO = Path(__file__).resolve().parents[1]
RECIPES = sorted((REPO / "recipes").glob("*/recipe.yaml"))


# ---------------------------------------------------------------------------
# argparse tree extraction (static)
# ---------------------------------------------------------------------------

class ParserSpec:
    """One argparse parser level: its options, positionals, subcommands."""

    def __init__(self):
        self.flags = {}        # "--name" -> {"required": bool, "takes_value": bool}
        self.n_positionals = 0
        self.variadic = False  # a positional with nargs that absorbs the rest
        self.subs = {}         # subcommand name -> ParserSpec
        self.subs_any = False  # add_parser called with a non-literal name


def _extract_spec(func: ast.FunctionDef) -> ParserSpec:
    root = ParserSpec()
    parsers = {}      # var name -> ParserSpec
    subparsers = {}   # var name -> ParserSpec that owns the subparsers

    def handle_add_argument(spec: ParserSpec, call: ast.Call):
        names = [a.value for a in call.args
                 if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        kw = {k.arg: k.value for k in call.keywords}
        action = kw.get("action")
        store_true = (isinstance(action, ast.Constant)
                      and action.value in ("store_true", "store_false", "count"))
        required = (isinstance(kw.get("required"), ast.Constant)
                    and kw["required"].value is True)
        opt_names = [n for n in names if n.startswith("-")]
        if opt_names:
            for n in opt_names:
                spec.flags[n] = {"required": required,
                                 "takes_value": not store_true}
        elif names:
            nargs = kw.get("nargs")
            if isinstance(nargs, ast.Constant) and nargs.value in ("?", "*", "+"):
                spec.variadic = True
            else:
                spec.n_positionals += 1
        else:
            # non-literal argument name: be permissive at this level
            spec.subs_any = True

    for node in ast.walk(func):
        if not isinstance(node, (ast.Assign, ast.Expr)):
            continue
        value = node.value if isinstance(node, ast.Assign) else node.value
        if not isinstance(value, ast.Call):
            continue
        call = value
        fn = call.func
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id

        if isinstance(fn, ast.Name) or (
                isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                and fn.value.id == "argparse"):
            # p = _Parser(...) / argparse.ArgumentParser(...)
            name = fn.id if isinstance(fn, ast.Name) else fn.attr
            if name.endswith("Parser") and target:
                parsers[target] = root if not parsers else ParserSpec()
                if not any(s is root for s in parsers.values()):
                    parsers[target] = root
            continue

        if not isinstance(fn, ast.Attribute) or not isinstance(fn.value, ast.Name):
            continue
        owner, meth = fn.value.id, fn.attr

        if meth == "add_subparsers" and owner in parsers and target:
            subparsers[target] = parsers[owner]
        elif meth == "add_parser" and owner in subparsers:
            owner_spec = subparsers[owner]
            if call.args and isinstance(call.args[0], ast.Constant):
                sub = ParserSpec()
                owner_spec.subs[call.args[0].value] = sub
                if target:
                    parsers[target] = sub
            else:
                owner_spec.subs_any = True  # dynamic add_parser (loop)
        elif meth == "add_argument" and owner in parsers:
            handle_add_argument(parsers[owner], call)
    return root


def _entrypoints():
    """prog name -> ParserSpec, from every component pyproject."""
    eps = {}
    for toml_path in sorted(REPO.glob("recipes/*/*/pyproject.toml")):
        data = tomllib.loads(toml_path.read_text())
        scripts = data.get("project", {}).get("scripts", {})
        for prog, spec in scripts.items():
            mod, func_name = spec.split(":")
            mod_file = toml_path.parent / (mod.replace(".", "/") + ".py")
            if not mod_file.exists():
                continue
            tree = ast.parse(mod_file.read_text())
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name == func_name:
                    eps[prog] = _extract_spec(node)
                    break
    return eps


ENTRYPOINTS = _entrypoints()


# ---------------------------------------------------------------------------
# manifest command collection
# ---------------------------------------------------------------------------

def _collect_commands(obj, out):
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "command" and isinstance(val, str):
                out.append(val)
            elif key == "commands" and isinstance(val, list):
                out.extend(v for v in val if isinstance(v, str))
            elif key == "ops" and isinstance(val, dict):
                out.extend(v for v in val.values() if isinstance(v, str))
            else:
                _collect_commands(val, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_commands(item, out)


def _normalize(cmd: str):
    """-> (prog, tokens) or None if not aimed at a known entrypoint."""
    cmd = re.sub(r"\[[^\]]*\]", "", cmd)          # optional segments
    cmd = re.sub(r"\{[a-z][a-z0-9_]*\}", "PLV", cmd)  # placeholders
    cmd = re.sub(r"<[^>]*>", "PLV", cmd)          # prose value placeholders
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    while tokens and re.match(r"^[A-Z_][A-Z0-9_]*=", tokens[0]):
        tokens.pop(0)                              # env-var prefixes
    if not tokens or tokens[0] not in ENTRYPOINTS:
        return None
    return tokens[0], tokens[1:]


def _validate(prog, tokens):
    """Return a list of defect strings for one command invocation."""
    spec = ENTRYPOINTS[prog]
    errors = []
    path = [prog]
    i = 0
    positionals = 0
    seen_flags = set()

    def check_flags(spec):
        missing = [f for f, meta in spec.flags.items()
                   if meta["required"] and f not in seen_flags]
        if missing:
            errors.append(f"{' '.join(path)}: missing required {missing}")

    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            meta = spec.flags.get(name)
            if meta is None:
                errors.append(f"{' '.join(path)}: unknown flag {name}")
                i += 1
                if i < len(tokens) and not tokens[i].startswith("-"):
                    i += 1  # assume it took a value
                continue
            seen_flags.add(name)
            i += 1
            if meta["takes_value"] and "=" not in tok:
                i += 1
        else:
            if spec.subs and tok == "PLV":
                # a {placeholder} standing for the subcommand (generic
                # tool command whose concrete forms live in its ops table)
                return errors
            if spec.subs and tok in spec.subs:
                check_flags(spec)
                spec = spec.subs[tok]
                path.append(tok)
                positionals = 0
                seen_flags = set()
            elif spec.subs and spec.subs_any:
                path.append(tok)
                return errors  # dynamic subcommands: stop checking
            elif spec.subs and positionals >= spec.n_positionals \
                    and not spec.variadic:
                errors.append(f"{' '.join(path)}: unknown subcommand {tok!r}")
            else:
                positionals += 1
                if positionals > spec.n_positionals and not spec.variadic:
                    errors.append(
                        f"{' '.join(path)}: extra positional {tok!r}")
            i += 1
    check_flags(spec)
    if spec.subs and not spec.subs_any:
        errors.append(f"{' '.join(path)}: missing subcommand")
    return errors


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

def test_entrypoints_discovered():
    assert len(ENTRYPOINTS) >= 40, sorted(ENTRYPOINTS)
    assert "msigner" in ENTRYPOINTS and "x402-buy" in ENTRYPOINTS


@pytest.mark.parametrize("recipe_path", RECIPES,
                         ids=[p.parent.name for p in RECIPES])
def test_manifest_commands_parse(recipe_path):
    manifest = yaml.safe_load(recipe_path.read_text())
    commands = []
    _collect_commands(manifest, commands)
    defects = []
    for cmd in commands:
        norm = _normalize(cmd)
        if norm is None:
            continue
        if " ..." in cmd or "…" in cmd:
            defects.append(f"prose ellipsis in command: {cmd!r}")
            continue
        prog, tokens = norm
        for err in _validate(prog, tokens):
            defects.append(f"{err}   [from: {cmd!r}]")
    assert not defects, "\n".join(defects)


def test_no_prose_ellipses_anywhere():
    bad = []
    for recipe_path in RECIPES:
        manifest = yaml.safe_load(recipe_path.read_text())
        commands = []
        _collect_commands(manifest, commands)
        bad += [f"{recipe_path.parent.name}: {c!r}" for c in commands
                if " ..." in c or "…" in c]
    assert not bad, "\n".join(bad)


# ---------------------------------------------------------------------------
# state_dir metadata vs component state selection
# ---------------------------------------------------------------------------

_STATE_ENV_RE = re.compile(
    r"environ\.get\(\s*[\"']([A-Z0-9_]*(?:_STATE|_ROOT))[\"']")
_DECLARED_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)")


def _component_state_envs(src_dir: Path):
    envs = []
    for py in sorted(src_dir.rglob("*.py")):
        envs += _STATE_ENV_RE.findall(py.read_text())
    return envs


@pytest.mark.parametrize("recipe_path", RECIPES,
                         ids=[p.parent.name for p in RECIPES])
def test_state_dir_matches_code(recipe_path):
    manifest = yaml.safe_load(recipe_path.read_text())
    components = (manifest or {}).get("components") or {}
    defects = []
    for name, comp in components.items():
        if not isinstance(comp, dict):
            continue
        source = comp.get("source")
        if not source:
            continue
        src_dir = (recipe_path.parent / source).resolve()
        if not src_dir.is_dir():
            continue
        code_envs = _component_state_envs(src_dir)
        declared = comp.get("state_dir")
        if declared:
            declared_envs = _DECLARED_ENV_RE.findall(declared)
            for env in declared_envs:
                if env not in code_envs:
                    defects.append(
                        f"{name}: declared state env {env} not selected "
                        f"anywhere in {src_dir.name} code")
        elif code_envs:
            defects.append(
                f"{name}: code selects state root via {sorted(set(code_envs))} "
                f"but the manifest declares no state_dir")
    assert not defects, "\n".join(defects)


# ---------------------------------------------------------------------------
# fresh-status exit pins (audited 2026-10-16, cst-mdiz.14)
# ---------------------------------------------------------------------------

# prog -> exit code of `<prog> status` on a fresh (unconfigured) install
FRESH_STATUS_EXIT = {
    "msigner": 0,   # returns {configured: false}, exit 0
    "gpod": 2,
    "pserv": 2,
    "prov": 2,
    "signer": 2,
}


def test_fresh_status_exit_claims():
    defects = []
    for recipe_path in RECIPES:
        manifest = yaml.safe_load(recipe_path.read_text())
        for step in (manifest or {}).get("setup") or []:
            if not isinstance(step, dict):
                continue
            cmds = step.get("commands") or []
            expect = str(step.get("expect", ""))
            m = re.search(r"exit (\d)", expect)
            if not m:
                continue
            claimed = int(m.group(1))
            for cmd in cmds:
                parts = cmd.split()
                if len(parts) == 2 and parts[1] == "status" \
                        and parts[0] in FRESH_STATUS_EXIT:
                    actual = FRESH_STATUS_EXIT[parts[0]]
                    if claimed != actual:
                        defects.append(
                            f"{recipe_path.parent.name}: setup claims "
                            f"'{parts[0]} status' fresh exit {claimed}, "
                            f"code exits {actual}")
    assert not defects, "\n".join(defects)


# ---------------------------------------------------------------------------
# the gate must actually bite: representative regressions from the audit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_cmd", [
    "silo delete KEY --approval TOKEN",
    "silo teardown --approval TOKEN",
    "keep provision --approval TOKEN",
    "keep teardown --approval TOKEN",
    "bell register canary",
    "bell deregister JOB --approval TOKEN",
    "beacon register canary",
    "beacon deregister T --approval TOKEN",
    "sprecon resolve --finding F1",
    "wing sender add S --descriptor D --approval TOKEN",
    "wing sender rotate S",
    "x402-buy URL --payment-id P --wallet mainnet",
    "msigner admin restore-rehearsal",
])
def test_gate_rejects_audited_regressions(bad_cmd):
    norm = _normalize(bad_cmd)
    assert norm is not None
    prog, tokens = norm
    assert _validate(prog, tokens), f"gate failed to reject: {bad_cmd}"


@pytest.mark.parametrize("good_cmd", [
    "silo delete KEY",
    "keep provision --trusted-ip 1.2.3.4 --key-file /k",
    "bell register canary --schedule '*-*-* *:*:00' --argv '[\"true\"]'",
    "beacon register c --url U --sentinel S --cadence-seconds 300 "
    "--local-cadence-seconds 300",
    "sprecon resolve --finding F1 --note fixed",
    "wing sender rotate S --secret-out /tmp/s",
    "x402-buy URL --payment-id P",
    "msigner admin restore-rehearsal --backup-dir /b",
    "msigner status",
])
def test_gate_accepts_corrected_commands(good_cmd):
    norm = _normalize(good_cmd)
    assert norm is not None
    prog, tokens = norm
    errs = _validate(prog, tokens)
    assert not errs, f"{good_cmd}: {errs}"
