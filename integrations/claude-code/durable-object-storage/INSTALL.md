# durable-object-storage → Claude Code integration

Installed 2026-10-13 against the recipe at
`recipes/durable-object-storage/` (recipe.yaml rev 1, status
reference-green), following that directory's ADAPT-draft.md.

## What was set up

1. **Component present** — venv at `~/.scutl/silo-venv`, installed
   from the recipe directory:

   ```bash
   python3 -m venv ~/.scutl/silo-venv
   ~/.scutl/silo-venv/bin/pip install ./silo   # from recipes/durable-object-storage/
   ```

   Two executables land on the venv bin: `silo` (agent-facing) and
   `silo-approve` (the HUMAN's helper — grants one-shot tokens for
   `configure` / `provision` / `delete` / `teardown`; never exposed to
   the agent as a tool). State: `${SCUTL_SILO_STATE:-~/.scutl/silo}`;
   `silo-approve` must run with the same `SCUTL_SILO_STATE`. Always
   reference the binaries by absolute path.

2. **Tool exposure** — Claude Code has no per-tool typed exposure for
   shell components; tools are invoked through the generic **Bash
   tool** plus the skill text in `SKILL.md` alongside this file. The
   ADAPT document names this generic-shell degradation ACCEPTABLE:
   read-back verification, caps, deny-list, token gates, and the
   teardown proof all hold in the component's own code. No wrapper was
   invented. `si_delete`, `si_teardown`, and provisioning have no
   harness confirmation tier here; the guardrail text is carried in
   SKILL.md and that is stated per the document.

3. **Guardrails** — `execute.guardrails` and the `execute.loop`
   description from recipe.yaml are carried **verbatim** in SKILL.md.

4. **Setup ceremony (human-gated, live)** — in order:
   `silo-approve configure` → `silo admin configure` with ratified
   caps (defaults 20 GB, $10/mo, weekly rehearsal, 256 MB single put);
   `silo-approve provision` + human places the 0600 rail key file →
   `silo admin provision --cluster-id <N> --tier-id <N> --key-file
   <path>` (billing accrues from then until teardown); then one canary
   put and one green rehearsal.

## Human-side acts performed by the integrating agent (standing in)

- Granting every `silo-approve` token (configure, provision, delete
  implicit refusals, teardown) during acceptance.
- Writing the per-scenario rail key files (0600) from the test
  server's published per-world keys.

In real use these are the owner's acts, out of band.

## Acceptance

The 12-scenario battery from ADAPT-draft.md §Step 5 was run inside
this harness against `tools/silo_test_store.py` on port 8506, each
scenario in a fresh `mktemp -d` `SCUTL_SILO_STATE`. Two verify items
are bench-covered and not reproducible here, as the document states:
`rehearsal overdue` (needs clock control) and `spend anomaly` (the
anomalous-growth cell). Results and stumbles are in the session
report delivered with this install.
