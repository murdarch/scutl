# secrets-rotation — Claude Code integration

Integration of recipe `rotor` rev 1 (catalog recipe #28) into Claude
Code, per `recipes/secrets-rotation/ADAPT-draft.md` (recipe.yaml is
the authority; design bead cst-u3xz). Performed and accepted
2026-11-10.

## What was set up

1. **Component install** (ADAPT Step 1). A dedicated durable venv
   with the sibling signer installed first, then rotor:

   ```bash
   python3 -m venv ~/.scutl/rotor-venv
   ~/.scutl/rotor-venv/bin/pip install <scutl>/recipes/wallet-base-sepolia/signer
   ~/.scutl/rotor-venv/bin/pip install <scutl>/recipes/secrets-rotation/rotor
   ```

   `{{BIN}}` = `~/.scutl/rotor-venv/bin`. The install provides
   `rotor` (agent surface: status | begin | commit | recover | abort)
   and `rotor-approve` (HUMAN-ONLY: grants the consumable token out
   of band).

2. **Tool exposure** (ADAPT Step 2). Claude Code offers no per-tool
   typed exposure for shell components; the tool surface is the Bash
   tool. The five tools (rt_status, rt_begin, rt_commit, rt_recover,
   rt_abort) are mapped as distinct documented invocations in
   `SKILL.md` alongside the exit-code taxonomy (1 invalid · 2
   not-ready · 4 approval-required · 5 in-flight · 6 integrity).
   Results are surfaced verbatim. `rotor-approve` is NOT exposed as
   an agent tool; SKILL.md marks it human-only.

3. **Guardrails in system context** (ADAPT Step 3).
   `execute.loop`, `execute.guardrails`, and the refusal / crash /
   escrow-demand stanzas were copied VERBATIM from recipe.yaml into
   `SKILL.md`. To load the skill into a Claude Code session,
   place/symlink this directory under
   `.claude/skills/secrets-rotation/` of the project that runs the
   ceremony (not done here — shared skills are out of scope for this
   integration, matching the idbr precedent).

4. **Setup with the human** (ADAPT Step 4). Documented, not executed
   against a live wallet in this session:
   `rotor status` against the live dir works (configured true, owner
   address, kek_sha256, kek_age_days 90 of 180 horizon — not
   overdue, no in-flight, no tombstone/panic). The remaining live
   steps are the human's: prove the current backup restores (idbr
   verify + rehearse) BEFORE the first rotation; grant the token with
   `rotor-approve rotate`; after any commit, refresh the idbr
   manifest and re-run the copy ceremony.

5. **Acceptance** (ADAPT Step 5). Run entirely against scratch
   identities in the session scratchpad; no live wallet written. All
   six checks green:

   1. `rotor status` → configured true, owner address, kek_sha256,
      exit 0.
   2. `rotor begin` without token → exit 4, nothing journaled
      (status showed `in_flight: null`, empty journal).
   3. `rotor-approve rotate` (agent-run for scratch acceptance only),
      then begin → journal begin/staged/verified, phase "verified";
      commit → `"committed": true`, address unchanged
      (0xE7Bec17E…30eF before and after), old and new kek digests
      both named, `old_kek_destroyed: true`.
   4. Second `rotor begin` → exit 4 again (one token, one ceremony).
   5. `rotor status` → `rotations_committed: 1`, kek_sha256 now the
      new digest, age re-anchored to 0.
   6. Secrecy sweep: a second full ceremony was run with all
      stdout/stderr captured to a log; grep of the log and of
      rotation-journal.jsonl for the old KEK, new KEK, and private
      key hex found nothing. CLEAN.

## State resolution

`SCUTL_ROTOR_STATE`, else `SCUTL_STATE`, else `~/.scutl/wallet`.
Set nothing for the live sepolia wallet; point `SCUTL_ROTOR_STATE`
at `~/.scutl/mwallet` for mainnet.

## Documented-vs-observed notes

- ADAPT Step 1 shows `rotor status` answering
  `{"configured": false, …}` on first run; in this environment the
  default state resolution found the live sepolia wallet at
  `~/.scutl/wallet`, so the first answer was the configured-true
  branch instead. Both branches behaved as documented.
- No other divergences observed; refusal texts, exit codes, journal
  event names, and status fields matched the ADAPT document and
  recipe.yaml exactly.
