# spend-reconciliation — Claude Code integration

Installed 2026-10-13 against recipe `sprc` rev 1
(`recipes/spend-reconciliation/recipe.yaml`, status reference-green),
following `ADAPT-draft.md` in the recipe directory. Acceptance ran
inside this harness (Claude Code's Bash tool is the tool surface) and
PASSED all 12 scenarios against the catalog's servable test chain
(`tools/sprc_test_chain.py --port 8454`).

## What was set up

1. **Component present.** The `sprecon` Python package installed into
   a dedicated venv:

   ```bash
   python3 -m venv ~/.scutl/sprecon-venv
   ~/.scutl/sprecon-venv/bin/pip install ./sprecon   # from the recipe dir
   ```

   (The acceptance run used a session-scratch venv; for a durable
   install use `~/.scutl/sprecon-venv` as above.)

   Probe: `sprecon status` with no env answers on stderr with exit 2,
   `{"error": "not-configured", ...}` — healthy; a traceback is not.

   The install puts two executables on the venv bin: `sprecon` (the
   agent-facing tool) and `sprecon-approve` (the HUMAN's helper that
   grants one-shot tokens for `resolve` and `rebaseline`; never
   exposed to the agent as a tool).

2. **Tool exposure.** Claude Code offers a generic shell (Bash) tool,
   not per-tool typed exposure. Per the ADAPT doc this degradation is
   ACCEPTABLE: the no-fudge arithmetic, read-only books, and token
   gates hold in the component's own code. The mapping of the four
   recipe tools (sr_status, sr_reconcile, sr_findings, sr_resolve) to
   exact `sprecon` invocations, the exit-code taxonomy (2/4/6/1), and
   the human-gated baseline/rebaseline ops live in `SKILL.md` next to
   this file — drop it into the project's `.claude/skills/
   spend-reconciliation/` to put it in front of the agent. Claude Code
   has no per-subcommand tool tiers for Bash, so resolve/baseline/
   rebaseline cannot be placed behind a harness confirmation tier;
   the guardrail text is carried instead (stated per the ADAPT doc),
   and the component's own one-shot token gate (exit 4 without a
   token) is the enforcement.

3. **Guardrails carried verbatim.** `execute.guardrails` and the
   `execute.loop` description from recipe.yaml are copied verbatim
   into SKILL.md (not paraphrased — graded wording).

4. **Configuration.** Environment variables only, no secrets:
   `SPRECON_WALLET` (required), `SPRECON_ROOT`, `SPRECON_RPC`,
   `SPRECON_PSERV`/`SPRECON_PROV` (optional), `SPRECON_USDC`. They
   must be set in the tool-execution environment (e.g. project
   `.claude/settings.json` `env` block), and the human's
   `sprecon-approve` must run with the SAME `SPRECON_ROOT` as the
   agent's op — the token lands inside it. Reference `sprecon` by
   absolute path.

## Setup flow (with the human)

Per `setup:` in recipe.yaml: `sprecon status` (books present, no
baseline) → human runs `sprecon-approve rebaseline`, then
`sprecon baseline --opening-balance <USDC> --block <N> --attestor
<name>` → `sprecon reconcile` (conservation line, invariant checked)
→ `sprecon reconcile --billing-file <statement.json>` when a provider
statement is available.

## Acceptance summary

All 12 ADAPT scenarios passed inside this harness: clean pass
(conservation 3=3, residue 0.00, books byte-identical by sha256),
drain escalation with verbatim tx, late-settle as
unrecorded-merchant-settle, settled-tx-failed past deadline vs honest
pending-tx inside it, tamper (both digests), missing book (absent in
status; the orphan transfer escalates), rpc-flap exit 6 then clean
retry, balance-residue escalation (-0.63), unattested-deposit closed
by funding.log attestation, billing clean (estimate-vs-actual honest)
vs statement-absent escalation, foreign-invoice-line with the memo
quoted as data, billing-after-destroy, and the token gates (exit 4
without, append with).

Known doc nit found during acceptance: for the missing-log scenario
the reconcile report's `legs.books` still reads "present"; the book's
absence surfaces in `sprecon status` (`books.spend.present: false`),
not in the reconcile report itself. The orphan chain transfer
escalates as `unlogged-transfer-out` as documented.
