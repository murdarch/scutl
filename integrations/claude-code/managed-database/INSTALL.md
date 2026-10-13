# managed-database (keep) — Claude Code integration install record

Integrated 2026-10-13, per `recipes/managed-database/ADAPT-draft.md`.
Harness: Claude Code (shell access via the Bash tool).

## What was set up

### Step 0 — composition prerequisites

- PostgreSQL 15 already installed and online on this host (cluster
  `15/main`, port 5432; `psql`, `pg_dump`, `pg_restore` on PATH).
- silo installed and configured per the silo ADAPT Steps 1 + 4 against
  silo's test store (`tools/silo_test_store.py`, `/clean` world,
  rail key `railkey-clean`), in a dedicated `SCUTL_SILO_STATE` exported
  for every `keep` command. `silo admin configure` with defaults, then
  `silo admin provision --cluster-id 1 --tier-id 1 --key-file <0600 file>`
  → `{"provisioned": true, "subscription_id": "sub-clean-1"}`.

### Step 1 — component present

```bash
python3 -m venv ~/.scutl/keep-venv
~/.scutl/keep-venv/bin/pip install recipes/durable-object-storage/silo
~/.scutl/keep-venv/bin/pip install recipes/managed-database/keep
```

Probe: `keep status` in a fresh `SCUTL_KEEP_STATE` answered exit 0
`{"configured": false}` — healthy per the ADAPT.

Two executables on the venv bin: `keep` (agent tool, referenced by
absolute path `~/.scutl/keep-venv/bin/keep`) and `keep-approve`
(the human's helper; never exposed as an agent tool).

### Step 2 — tool exposure

Claude Code offers a generic shell tool (Bash), not per-tool typed
exposure. Per ADAPT Step 2's explicit allowance, this generic-shell
degradation is ACCEPTABLE: the ledger walls, token gates, scrubbing,
and final-dump-first teardown hold in the component's own code. The
tool mapping (kp_status … kp_teardown), the exit-code taxonomy, and
the data-not-instructions rule are carried in `SKILL.md` beside this
file, for inclusion in the system/skill context of any session that
keeps the database.

Claude Code has no human-confirmation tool tier; money ops and
destructive migrations rest on the component's own `keep-approve`
one-shot token gates, and the SKILL.md says so (guardrail text
carried verbatim).

### Step 3 — guardrails

`execute.guardrails`, `execute.loop`, and the three `on_*` conduct
lines from `recipe.yaml` are copied VERBATIM into `SKILL.md`.

### Step 4 — setup

Run with the integrating agent standing in for the human at the desk
(token grants and key-file placement noted as human-side acts):
`keep-approve configure` + `keep admin configure --migrations-dir <dir>
--database <name>` (defaults accepted), then `keep-approve provision` +
`keep provision --trusted-ip 127.0.0.1/32 --key-file <0600 key file>`,
then the full first loop (migrate, dump, rehearse) green.

### Step 5 — acceptance

Run inside this harness (the Bash tool IS the shell) against
`tools/keep_test_rail.py --port 8514 --bootstrap` (scripted Vultr wire
in front of the real local PostgreSQL) with dumps riding the real silo
against `tools/silo_test_store.py --port 8507` (`/clean` world).
Default ports 8507/8514 were free, so the fallback ports (8508/8516)
were not needed. All 11 scenarios ran; results recorded in the
acceptance report delivered with this integration. Bench-covered,
not reproduced here (per the ADAPT): phantom migration,
rehearsal/dump overdue, provider-backup-trust prose, restore-fork
temptation, rotate-to-fit, headroom park.

## Operational notes

- State: `${SCUTL_KEEP_STATE:-~/.scutl/keep}`; the human's
  `keep-approve` must run with the SAME `SCUTL_KEEP_STATE`.
- `SCUTL_SILO_STATE` must be exported for every `keep` command
  (dumps ride silo's put/verify path).
- Live rail selection: `SCUTL_KEEP_RAIL_API` (unset = the live Vultr
  rail; nothing in this integration provisions or bills anything —
  acceptance ran entirely against the servable test rail).
- Rail key arrives in a 0600 FILE passed to `keep provision --key-file`.
