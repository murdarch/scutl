"""Presentation only: source-bound prose slots and deterministic Markdown."""

import hashlib
import html
import json
import re


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def load_slots(blob):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    return json.loads(blob, object_pairs_hook=unique,
                      parse_constant=invalid_constant)


def template(facts, binding):
    digest = hashlib.sha256(canonical(facts)).hexdigest()

    def ref(paths, entry_id=None):
        result = {"evidence_sha256": digest, "paths": paths}
        if entry_id is not None:
            result["entry_id"] = entry_id
        return result

    return {
        "schema_version": 1,
        "binding": binding,
        "evidence_sha256": digest,
        "facts": facts,
        "slots": {
            "stage": {
                "source_ref": ref(["/handoff/report/document", "/handoff/bindings"]),
                "interpretation": "",
            },
            "entries": {
                row["id"]: {
                    "source_ref": ref([
                        f"/handoff/report/document/entries/{index}"], row["id"]),
                    "interpretation": "", "next_step": "",
                }
                for index, row in enumerate(facts["handoff"]["report"]["document"]["entries"])
            },
            "authority": {
                "source_ref": ref(["/handoff/authority", "/handoff/bindings",
                                   "/handoff/journal/latest_released"]),
                "reachability_or_override": "", "release_or_takeover": "",
            },
            "final_state": {
                "source_ref": ref(["/handoff/lease", "/journal_tail",
                                   "/handoff/journal/succession_events"]),
                "completed_and_remaining": "",
            },
        },
    }


def validate_slots(actual, expected):
    """Only named prose may differ; compare every other JSON value exactly."""
    try:
        actual_slots = actual["slots"]
        pairs = [(actual_slots["stage"], expected["slots"]["stage"],
                  ("interpretation",))]
        pairs.extend((actual_slots["entries"][key], target,
                      ("interpretation", "next_step"))
                     for key, target in expected["slots"]["entries"].items())
        pairs.extend([
            (actual_slots["authority"], expected["slots"]["authority"],
             ("reachability_or_override", "release_or_takeover")),
            (actual_slots["final_state"], expected["slots"]["final_state"],
             ("completed_and_remaining",)),
        ])
        for source, target, fields in pairs:
            for field in fields:
                text = source[field]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError(f"prose slot {field} must be a nonblank string")
                target[field] = text
        # Canonical bytes distinguish JSON true from 1, unlike Python equality.
        if canonical(actual) != canonical(expected):
            raise ValueError("slots schema, binding, facts or source references changed")
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError("invalid report slots shape") from error


def _block(value, prose=False):
    # JSON quoting also neutralizes control characters and bidi source text.
    text = json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False)
    # No source/prose run can close its fence and inject a fixed label.
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{'' if prose else 'json'}\n{text}\n{fence}\n"

def _cell(value):
    text = html.escape(json.dumps(value, ensure_ascii=True), quote=False)
    return "".join(f"&#{ord(char)};" if char in "\\`|[]()!*_~#{}"
                   else char for char in text)



def markdown(document, manifest_path):
    facts = document["facts"]
    handoff = facts["handoff"]
    report = handoff["report"]
    stage = report["document"]
    slots = document["slots"]
    sections = [
        "# Baton handoff report\n",
        "Generated facts and model-authored prose are separate. Presentation is not "
        "a takeover, permission, task outcome, or task-start freshness certificate. "
        "This is one status read, not an atomic multi-file snapshot or a future-freshness guarantee.\n",
        "## Stage and source bindings — generated facts\n",
        _block({"manifest_path": str(manifest_path),
                "manifest_sha256": handoff["manifest_sha256"],
                "report_path": report["path"],
                "report_file_sha256": report["file_sha256"],
                "expected_binding": document["binding"],
                "evidence_sha256": document["evidence_sha256"],
                "stage_time_facts": {key: value for key, value in stage.items()
                                     if key != "entries"},
                "bindings": handoff["bindings"],
                "latest_staged": handoff["journal"]["latest_staged"]}),
        "Manifest age/stale above are recorded stage-time facts, not advancing current age.\n",
        "### Stage interpretation — model-authored prose\n",
        _block(slots["stage"]["interpretation"], prose=True),
        "## Every registered entry — generated facts in report order\n",
        "| ID | Class | Verdict | Observed SHA256 | Baseline SHA256 | Deliberate reason/authority |\n"
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in stage["entries"]:
        sections.append("| " + " | ".join(_cell(row.get(key)) for key in
                        ("id", "class", "verdict", "observed_sha256",
                         "baseline_sha256", "deliberate")) + " |")
    sections.append("\nHashes and nulls do not establish raw file meaning, balances, due/status, "
                    "or service UP/DOWN. Deliberate states are not instructions to repair.\n")
    for index, row in enumerate(stage["entries"]):
        sections.extend([
            f"### Entry {index + 1} — generated facts\n", _block(row),
            "#### Interpretation — model-authored prose\n",
            _block(slots["entries"][row["id"]]["interpretation"], prose=True),
            "#### Next step — model-authored prose, not a completed action\n",
            _block(slots["entries"][row["id"]]["next_step"], prose=True),
        ])
    sections.extend([
        "## Independent authority facts — generated\n",
        _block({"authority": handoff["authority"], "bindings": handoff["bindings"],
                "latest_released": handoff["journal"]["latest_released"],
                "overrides_in_source_order": handoff["journal"]["overrides"]}),
        "Reachability/override and release/takeover are independent requirements. "
        "Token presence is an existence observation only; no token was read or consumed.\n",
        "### Reachability or override — model-authored prose\n",
        _block(slots["authority"]["reachability_or_override"], prose=True),
        "### Release or takeover — model-authored prose\n",
        _block(slots["authority"]["release_or_takeover"], prose=True),
        "## Actual lease and journal — generated facts\n",
        _block({"lease": handoff["lease"], "journal_tail": facts["journal_tail"],
                "succession_events_in_source_order": handoff["journal"]["succession_events"]}),
        "Lists retain their own source order; timestamps do not establish a global chronology. "
        "Historical committed/unwound events without a report hash remain report-unbound. "
        "An older commit or a lease does not certify that the current stage committed.\n",
        "### Completed and remaining — model-authored prose\n",
        _block(slots["final_state"]["completed_and_remaining"], prose=True),
        "Requested contact, approval or future work in prose is not evidence it occurred.\n",
    ])
    return "\n".join(sections).encode("utf-8")
