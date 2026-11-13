"""Regenerate the shipped plugin skill using the canonical recipe lowerer.

From the scutl repository root:
    .venv/bin/python recipes/inbox-triage/hermes/refresh_context.py

The smol section is tools/emit.py output, with canonical execute fields added.
Only the native invocation mapping below is Hermes-specific.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


NATIVE_CONTEXT = """---
name: inbox-triage
description: Canonical inbox-triage guardrails and smol workflow for Sift native tools.
---

# Sift native integration — read before the canonical workflow

This is the shipped `sift-native:inbox-triage` plugin skill, not a second
recipe. The canonical recipe.yaml remains authoritative. The smol profile
below is emitted unchanged by tools/emit.py with the recipe's default
configuration; the execute loop and guardrails are taken from that same
manifest. bundle.json records its SHA-256 and resolved parameters. Regenerate with:
`.venv/bin/python recipes/inbox-triage/hermes/refresh_context.py`.

Use the six native tools for the CLI operations shown in the canonical
workflow. Do not build a shell command or generate an adapter:

| Canonical operation | Native tool arguments |
| --- | --- |
| `sift status` | `sift_status({})` |
| `sift fetch` | `sift_fetch({})` |
| `sift read --id <id>` | `sift_read({"message_id": "<id>"})` |
| `sift triage --id <id> --category <category> --summary <summary>` | `sift_triage({"message_id": "<id>", "category": "<category>", "summary": "<summary>"})` |
| `sift draft --reply-to <id> --body-file <path>` | `sift_draft({"message_id": "<id>", "path": "<path>"})` |
| `sift admin configure --categories <CSV> --max-fetch <integer>` | `sift_admin({"op": "configure", "categories": "<CSV>", "max_fetch_per_run": 25})` with the chosen integer cap |
| `sift admin decommission` | `sift_admin({"op": "decommission"})` |

Argument strings are literal: no shell quoting or expansion is needed. The
adapter uses one `--flag=value` argv element for each value, including bare
`--leading-dash`, dollars, quotes, and newlines. There is no `--to`, send,
executable-selector, arbitrary admin operation, or approval helper tool.
For a reply, write only a local UTF-8 body file outside Sift's managed state,
then pass that file's path to sift_draft; the CLI fixes the recipient to the
source sender and creates the draft. Never edit the generated draft directly.

Each native result is a JSON object containing integer exit_code and exact
UTF-8 stdout and stderr strings from one real CLI subprocess. Read the inner
stdout JSON on success; on nonzero exit preserve and report the exact stderr
and exit_code. An error field equal to adapter-error instead identifies a
native argument or transport failure, not a completed CLI operation. The
adapter never retries or rewords component results; any subsequent operation
must follow the canonical rules below.

Component/plugin installation is an operator prerequisite (see the recipe's
ADAPT.md); no generated Python, skill, or package-install command is needed
during triage. Verify existing setup using sift_status, then follow the
canonical setup prerequisites that are still missing. The operator alone
places credentials and runs sift-approve. Do not read approval tokens or
credentials, and do not implement an approval helper. Defaults in the emitted
profile are not permission to reconfigure an existing installation; status
is the source of its actual configuration and durable state. No live mailbox
provider is blessed in this revision. A successful installation or no-model
smoke is not fresh-model acceptance or a semantic model comparison.

The canonical smol profile supplies the one-step state rule, exit-code actions,
action sequence, and emergency procedure. The appended canonical execute loop
and guardrails also apply.

"""


def main() -> None:
    plugin = Path(__file__).resolve().parent
    recipe = plugin.parent
    repo = plugin.parents[2]
    spec = importlib.util.spec_from_file_location("scutl_canonical_emit", repo / "tools" / "emit.py")
    emitter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(emitter)
    sections = [NATIVE_CONTEXT]
    with tempfile.TemporaryDirectory(prefix="sift-native-context-") as temporary:
        bundle = emitter.emit(recipe, Path(temporary), "smol", {}, {})
        sections.append((bundle / "SKILL.md").read_text(encoding="utf-8"))
        provenance = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
    manifest = emitter.yaml.safe_load((recipe / "recipe.yaml").read_bytes())
    execute = manifest["execute"]
    sections.append("\n".join([
        "## Canonical execute loop and guardrails", "",
        " ".join(execute["loop"].split()), "",
        *("- " + " ".join(rule.split()) for rule in execute["guardrails"]), "",
    ]))
    destination = plugin / "skills" / "inbox-triage"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "SKILL.md").write_text("\n\n".join(sections), encoding="utf-8")
    (destination / "bundle.json").write_text(json.dumps({
        "generator": "recipes/inbox-triage/hermes/refresh_context.py",
        **provenance,
    }, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
