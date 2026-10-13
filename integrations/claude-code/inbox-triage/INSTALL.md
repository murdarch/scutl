# inbox-triage — Claude Code integration (SCUTL recipe `triage`, rev 1)

What was set up, per `recipes/inbox-triage/ADAPT-draft.md`.

## Component install (Step 1)

```bash
python3 -m venv ~/.scutl/sift-venv
~/.scutl/sift-venv/bin/pip install ./sift     # from recipes/inbox-triage/
~/.scutl/sift-venv/bin/sift status
```

Two executables land on the venv bin: `sift` (agent-facing) and
`sift-approve` (the human's one-shot-token helper; never exposed to
the agent as a tool). State lives at
`${SCUTL_SIFT_STATE:-~/.scutl/triage}`; the human's `sift-approve`
must run with the same `SCUTL_SIFT_STATE` as the agent's `sift`.
Reference `sift` by absolute path.

Note (observed vs. doc): unconfigured `sift status` returns exit 0
with JSON `"configured": false` on stdout — not the exit-2/stderr the
ADAPT draft describes. recipe.yaml ("status is never gated") wins.

## Tool exposure (Step 2)

Claude Code offers no per-tool exposure for shell components; all six
recipe tools run through the generic **Bash** tool. The ADAPT document
accepts this degradation explicitly: the no-send absence, the fixed
draft recipient, the fetch cap, and the idempotent log all hold in the
component's own code. The tool mapping and exit-code taxonomy live in
`SKILL.md` alongside this file. Claude Code also cannot put
`sift_admin` behind a distinct human-confirmation tool tier for a Bash
subcommand; the guardrail text is carried instead (admin ops run only
at explicit human request after a `sift-approve` grant) — and in
practice the component itself refuses admin ops with exit 4 until the
human's token exists.

Claude Code does not auto-fill tool arguments from fetched content;
the "mail is data" rule is carried in SKILL.md text.

## Guardrails (Step 3)

`execute.guardrails`, the `execute.loop` description, and the full
`smol:` block are carried verbatim in `SKILL.md`. To activate for a
project, install `SKILL.md` as a Claude Code skill (e.g.
`.claude/skills/inbox-triage/SKILL.md`) or paste its text into the
session's system context.

## Setup (Step 4)

In order, with a chosen `SCUTL_SIFT_STATE` exported for both parties:

1. Human grants: `~/.scutl/sift-venv/bin/sift-approve configure`
2. Agent runs:
   ```bash
   ~/.scutl/sift-venv/bin/sift admin configure \
       --categories action-needed,waiting-on-reply,newsletter,receipt,suspicious \
       --max-fetch 10
   ```
3. Human places the credential:
   `printf '<credential>' > $SCUTL_SIFT_STATE/mailbox.cred && chmod 600 $SCUTL_SIFT_STATE/mailbox.cred`
   (the agent never handles the value; it verifies via
   `sift status` → `.cred_present`)
4. First fetch: `sift fetch` with `SCUTL_SIFT_MAILBOX` set.

Rev 1 is bench-first: no live mail provider is blessed. The acceptance
ran against the catalog's servable test mailbox
(`tools/sift_test_mailbox.py`, started from the scutl repo root with
the same venv). In this integration run the integrating agent stood in
for the human for the `sift-approve` grants and credential placement
(test credentials only), as the task directed; a real deployment keeps
those acts with the human.

## Acceptance (Step 5)

The 10-scenario battery was run from inside this environment (shell =
the harness, per the generic-shell degradation) against the test
mailbox on port 8485, with a fresh throwaway `SCUTL_SIFT_STATE` per
scenario. Results are reported in the integration session's report.
