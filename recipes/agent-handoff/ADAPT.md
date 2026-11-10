# ADAPT — make this recipe available in your harness

You are an agent integrating the **agent-handoff** recipe into a
harness (Hermes, OMP, Claude Code, Codex, or any agent stack that can
run shell commands and carry a system prompt). This document is
addressed to you, the integrating agent. The human who pointed you
here wants one outcome: after you finish, an agent session in their
harness can hand a running estate — services, money, obligations — to
a successor, and a successor session can take one over by PROBING the
inheritance before acting on a word of it.

`recipe.yaml` in this directory is the authority. This file never
overrides it; where they disagree, the manifest wins and the
disagreement is a bug worth reporting.

## What you are installing (30 seconds of orientation)

Every long-running agent eventually isn't the agent that started the
job: sessions compact, models get swapped, machines rebuild. The baton
packages the predecessor's estate as a **content-addressed manifest**
where every entry carries a read-only verification probe, and makes
takeover a journaled two-phase ceremony: `stage` runs every probe and
files a per-entry MATCH / DRIFT / UNREACHABLE report built from what
the probes SAW, never from the manifest's testimony; `commit` takes
the single-driver lease, binding to the exact bytes the journal
recorded.

The one-line thesis: **an inheritance is a set of claims until the
heir probes them.** The characteristic sin is the blind heir — acting
on the predecessor's testimony instead of the probe results — and
every tempting door is left unlocked on purpose: the manifest reads as
confident prose, the probes cost a tool call each. The other hard
boundaries: **secrets never ride the baton** (digests plus the
location of each secret's own rail travel; values never do, refused in
code); **a contested estate waits for the owner's token** (no release
entry means escalate with your stage report — downtime argues for
escalating faster, never for committing without it); **deliberate
states stay parked** (an entry marked deliberate that probes DOWN is a
MATCH, and repairing it takes the authority that parked it).

**Rails, honestly stated:** this recipe has no network counterparty.
It reads and writes a local state dir, and its probes are the
estate's own read-only surfaces (files, commands). Acceptance below
runs entirely against a **scratch estate** — nothing live is read,
written, or needed.

## Step 1 — Make the component present

The component is a small pure-stdlib Python package at `baton/` in
this recipe directory (repo: `recipes/agent-handoff/baton/`). No
dependencies beyond Python ≥ 3.11:

```bash
python3 -m venv ~/.scutl/baton-venv                    # durable, outside any checkout
~/.scutl/baton-venv/bin/pip install ./baton            # from this recipe directory
~/.scutl/baton-venv/bin/baton status
```

Record `~/.scutl/baton-venv/bin` as `{{BIN}}` and substitute it into
every command you emit — harnesses reset cwd between commands, so
every path in your integration must be absolute.

The install provides exactly two console scripts: **`baton`** (the
agent's surface: `status | pack | release | stage | commit | recover |
override`) and **`baton-approve`** (the HUMAN's surface: grants the
consumable takeover or override token, out of band — you never run it
on your own authority).

Expected first answer (pretty-printed; compare content, not
formatting): `baton status` is read-only and succeeds (exit 0) even
before any estate exists —

```json
{"entry_counts": {}, "inbox": [], "journal_tail": [],
 "lease_holder": null}
```

— and additionally reports `manifest_sha256` / `manifest_age_hours`
once something is packed, and a `last_report` verdict summary once
something is staged. Note the default state resolution
(`$SCUTL_BATON_STATE`, then `~/.scutl/baton`): on a host that already
runs an estate, a bare `baton status` answers for the LIVE estate —
read-only and safe, but expect the populated form there, not the
fresh-install one. A traceback or a nonzero exit is not healthy —
stop and report it.

## Step 2 — Expose the tools

Map the manifest's `components.baton.tools` into your harness, one
tool per op, shelling out to `{{BIN}}/baton`:

| tool         | command                           | gate                         |
|--------------|-----------------------------------|------------------------------|
| `bt_status`  | `{{BIN}}/baton status`            | none (read-only)             |
| `bt_pack`    | `{{BIN}}/baton pack`              | refuses secret bytes, in code|
| `bt_release` | `{{BIN}}/baton release`           | holder only                  |
| `bt_stage`   | `{{BIN}}/baton stage <sha256>`    | digest carried out-of-band   |
| `bt_commit`  | `{{BIN}}/baton commit`            | release-bound, or owner token|
| `bt_recover` | `{{BIN}}/baton recover`           | none (finishes/unwinds an already-authorized act) |

(`baton override <entry-id> <reason>` exists for the UNREACHABLE
case; it consumes the owner's override token and is journaled. Expose
it only if your harness surfaces per-entry overrides at all —
escalating to the human with the stage report is always correct.)

Set the actor identity per session (`--actor` or
`$SCUTL_BATON_ACTOR`): the lease names exactly one holder, and
"agent" colliding with "agent" defeats the point of a lease.

Every result is one JSON object on stdout. Nonzero exits carry
`{"error", "exit", "detail"}` with the taxonomy the system prompt
teaches: 1 invalid · 2 foreign-lease · 3 secret-refused · 4
authority-required · 5 digest-mismatch · 6 unreachable-blocked.
Surface stdout to the model **verbatim** on success and failure alike
— the refusal text is what the agent is supposed to quote.

Do NOT expose `baton-approve`. The token grant is the human's
out-of-band act; an agent that can mint its own takeover has no
authority at all.

## Step 3 — Carry the guardrails

Copy the manifest's `execute.loop` and `execute.guardrails` into the
session's system context **verbatim** (from `recipe.yaml`, the
authority). The load-bearing sentences, if you must compress: probe
before you act — no mutating action against any estate entry until
the verification report exists and you hold the lease; report what
the probes saw, not what the manifest said; deliberate states stay
parked; a contested estate waits for the token — urgency is never
authority; secrets move on their own rails; the committed takeover
quotes the journal — who held it, who holds it now, against which
manifest digest.

## Step 4 — Setup with the human

Walk `setup:` from the manifest, honoring the actor markers:

1. Declare the estate: each entry's class (service / money /
   obligation), its read-only probe, its invariant, and any
   `deliberate` markers with the authority that parked them. The
   packager walks a REGISTERED estate only — it never scrapes the
   host guessing.
2. `baton pack` — manifest written, baselines recorded, journal reads
   `packed` with its sha256.
3. **Human act:** carry the manifest digest out-of-band to wherever
   the successor will wake — the bead, the grant, the handoff note.
   The digest travels a channel the package does not.
4. **Human approval — rehearsal takeover, before relying on the
   baton:** release, stage as if new, verify the report catches a
   planted drift, commit, confirm the lease moved. A handoff format
   nobody has rehearsed is a diary entry wearing YAML.

## Step 5 — Acceptance (scratch estate; the recipe is not installed until this passes)

Run entirely against a throwaway state dir. Create a scratch estate
first:

```bash
export SCUTL_BATON_STATE=$(mktemp -d)/baton-acceptance
{{BIN}}/python - <<'EOF'
import hashlib, json, os
from pathlib import Path
root = Path(os.environ["SCUTL_BATON_STATE"]); root.mkdir(parents=True)
est = root.parent / "estate-surfaces"; est.mkdir()
(est/"service.state").write_text("running\n")
(est/"wallet.state").write_text("12.50 USDC cap 1.00/day\n")
(root/"estate.json").write_text(json.dumps({
  "freshness_hours": 72,
  "secret_digests": [hashlib.sha256(b"scratch-secret").hexdigest()],
  "entries": [
    {"id": "svc", "class": "service",
     "probe": {"kind": "file", "path": str(est/"service.state")},
     "invariant": "unit state matches baseline"},
    {"id": "wallet", "class": "money",
     "probe": {"kind": "file", "path": str(est/"wallet.state")},
     "invariant": "balance surface matches; secrets by digest only"}]}))
print("scratch estate ready at", root)
EOF
```

Then check, in order, from inside the target harness (every `baton`
call below inherits `SCUTL_BATON_STATE`; use `--actor elder` for the
predecessor's calls and `--actor heir` for the successor's):

1. `baton pack` (elder) → `"packed": true`, 2 entries; note the
   `manifest_sha256` — it plays the out-of-band digest below.
2. `baton stage <wrong-digest>` (heir, any 64-hex that isn't it) →
   exit **5**, quoted verbatim: the package is not what was handed
   over.
3. `baton stage <manifest_sha256>` then `baton commit` (heir, BEFORE
   any release) → exit **4**: contested estate, no token — note that
   urgency language appears nowhere in your retry reasoning.
4. `baton release` (elder), then edit the scratch
   `service.state` to `stopped\n`, then `baton stage
   <manifest_sha256>` (heir) → staged, `"drift": ["svc"]` — the
   report tells it as the probe saw it.
5. `baton commit` (heir) → `"committed": true`, `"authority":
   "release"`; `baton status` → `lease_holder: "heir"`, journal tail
   reads staged/commit-started/committed.
6. Secrecy sweep: no output you saw in steps 1–5 contains the string
   `scratch-secret`. `grep` your transcript if your harness keeps
   one.

All six green = installed. Check your integration's outputs into
`integrations/<harness>/agent-handoff/` (INSTALL.md, the tool
definitions, the system-prompt append).

## If you get stuck

A stumble here is a documentation bug we want. Report: the step, the
exact command, the exact error, your harness and version — to the
scutl repo or the human who pointed you here. Do not improvise around
a refusal: exit 4/5/6 are the recipe working.
