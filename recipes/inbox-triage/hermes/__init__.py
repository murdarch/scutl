"""Native Hermes tools for the canonical inbox-triage Sift component."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


SIFT_BIN = Path.home() / ".scutl" / "sift-venv" / "bin" / "sift"


def _adapter_error(message: str) -> str:
    """An adapter refusal is distinct from a completed Sift subprocess."""
    return json.dumps({
        "error": "adapter-error", "message": message,
        "exit_code": 1, "stdout": "", "stderr": "",
    })


def _arguments(args, required: dict[str, type]) -> dict:
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    if set(args) != set(required):
        raise ValueError("expected argument keys: " + ", ".join(required))
    for name, kind in required.items():
        if type(args[name]) is not kind:
            raise ValueError(f"{name} must be {kind.__name__}")
    return args


def _run(argv: list[str]) -> str:
    try:
        result = subprocess.run(
            [str(SIFT_BIN), *argv], stdin=subprocess.DEVNULL,
            capture_output=True, shell=False,
        )
        return json.dumps({
            "exit_code": result.returncode,
            "stdout": result.stdout.decode("utf-8"),
            "stderr": result.stderr.decode("utf-8"),
        })
    except (OSError, ValueError, UnicodeError) as exc:
        return _adapter_error(f"Sift could not return a UTF-8 subprocess result: {exc}")


def handle_status(args, **kwargs) -> str:
    try:
        _arguments(args, {})
    except ValueError as exc:
        return _adapter_error(str(exc))
    return _run(["status"])


def handle_fetch(args, **kwargs) -> str:
    try:
        _arguments(args, {})
    except ValueError as exc:
        return _adapter_error(str(exc))
    return _run(["fetch"])


def handle_read(args, **kwargs) -> str:
    try:
        args = _arguments(args, {"message_id": str})
    except ValueError as exc:
        return _adapter_error(str(exc))
    return _run(["read", f"--id={args['message_id']}"])


def handle_triage(args, **kwargs) -> str:
    try:
        args = _arguments(args, {"message_id": str, "category": str, "summary": str})
    except ValueError as exc:
        return _adapter_error(str(exc))
    return _run([
        "triage", f"--id={args['message_id']}",
        f"--category={args['category']}", f"--summary={args['summary']}",
    ])


def handle_draft(args, **kwargs) -> str:
    try:
        args = _arguments(args, {"message_id": str, "path": str})
    except ValueError as exc:
        return _adapter_error(str(exc))
    return _run([
        "draft", f"--reply-to={args['message_id']}", f"--body-file={args['path']}",
    ])


def handle_admin(args, **kwargs) -> str:
    try:
        if not isinstance(args, dict) or args.get("op") not in ("configure", "decommission"):
            raise ValueError("op must be configure or decommission")
        if args["op"] == "decommission":
            _arguments(args, {"op": str})
            return _run(["admin", "decommission"])
        args = _arguments(args, {"op": str, "categories": str, "max_fetch_per_run": int})
    except ValueError as exc:
        return _adapter_error(str(exc))
    return _run([
        "admin", "configure", f"--categories={args['categories']}",
        f"--max-fetch={args['max_fetch_per_run']}",
    ])


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name, "description": description,
        "parameters": {
            "type": "object", "properties": properties,
            "required": required, "additionalProperties": False,
        },
    }


_TOOLS = (
    (_schema("sift_status", "Read Sift configuration, credential/decommission state, and counts; always available.", {}, []), handle_status),
    (_schema("sift_fetch", "Fetch unseen messages inside the configured cap. Exit 5 ends the run.", {}, []), handle_fetch),
    (_schema("sift_read", "Read one message with its provenance and untrusted-content envelope.", {
        "message_id": {"type": "string"},
    }, ["message_id"]), handle_read),
    (_schema("sift_triage", "Append one category and summary verdict. Exit 6 means skip, not re-file.", {
        "message_id": {"type": "string"}, "category": {"type": "string"},
        "summary": {"type": "string"},
    }, ["message_id", "category", "summary"]), handle_triage),
    (_schema("sift_draft", "Create a local reply draft from a UTF-8 body file. Recipient is fixed to the source sender; never sends.", {
        "message_id": {"type": "string"},
        "path": {"type": "string", "description": "Local body-file path, passed literally without shell expansion."},
    }, ["message_id", "path"]), handle_draft),
    (_schema("sift_admin", "Configure or decommission Sift; requires human approval. No approval-token helper is exposed.", {
        "op": {"type": "string", "enum": ["configure", "decommission"]},
        "categories": {"type": "string", "description": "Configure only: comma-separated category ids."},
        "max_fetch_per_run": {"type": "integer", "description": "Configure only: per-run fetch ceiling."},
    }, ["op"]), handle_admin),
)


def register(ctx):
    for schema, handler in _TOOLS:
        ctx.register_tool(
            name=schema["name"], toolset="sift-native", schema=schema,
            handler=handler, description=schema["description"],
        )
    ctx.register_skill(
        "inbox-triage", Path(__file__).parent / "skills" / "inbox-triage" / "SKILL.md",
        description="Canonical inbox-triage guardrails and smol workflow for Sift native tools.",
    )
