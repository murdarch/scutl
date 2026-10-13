# identity-backup-restore — Claude Code integration

Integration of recipe `idbr` rev 1 (catalog recipe #3) into Claude
Code, per `recipes/identity-backup-restore/ADAPT-draft.md` (recipe.yaml
is the authority). Performed and accepted 2026-10-13.

## What was set up

1. **Component install** (ADAPT Step 1). A dedicated venv with the
   sibling signer installed first, then idbackup:

   ```bash
   python3 -m venv ~/.scutl/idbackup-venv
   ~/.scutl/idbackup-venv/bin/pip install <scutl>/recipes/wallet-base-sepolia/signer
   ~/.scutl/idbackup-venv/bin/pip install <scutl>/recipes/identity-backup-restore/idbackup
   ```

   This puts `idbackup` (agent-facing) plus two HUMAN-ONLY helpers
   (`idbackup-approve`, `idbackup-attest`) and `signer`/`signer-approve`
   on the venv bin. For the acceptance run the venv was created in the
   session scratchpad instead of `~/.scutl` (throwaway); a live install
   should use the path above and reference `idbackup` by absolute path.

2. **Tool exposure** (ADAPT Step 2). Claude Code offers no per-tool
   typed exposure for shell components; the tool surface is the Bash
   tool. Per the ADAPT document this generic-shell degradation is
   ACCEPTABLE (the approval gate, read-only live dir, and secret-free
   verify are enforced in the component's code). The four tools
   (ib_status, ib_manifest, ib_verify, ib_rehearse) are mapped as
   distinct documented invocations in `SKILL.md` alongside the
   exit-code taxonomy. Claude Code also has no tool-tier mechanism for
   ib_rehearse's human-confirmation tier; per the document, the
   guardrail text is carried instead and this is stated here: the
   rehearse gate is the component's own one-shot token from
   `idbackup-approve rehearse`, which only the human runs.

3. **Guardrails in system context** (ADAPT Step 3).
   `execute.guardrails` and the `execute.loop` description were copied
   VERBATIM from recipe.yaml into `SKILL.md`. To load the skill into a
   Claude Code session, place/symlink this directory under
   `.claude/skills/identity-backup-restore/` of the project that runs
   the ceremony (not done here — this repo's shared skills are out of
   scope for this integration).

4. **Setup + acceptance** (ADAPT Steps 4–5). Run entirely against a
   scratch identity in `/tmp/claude-1003` scratch space; no live
   wallet was read or written. All 12 acceptance scenarios passed (see
   the integration report delivered with this install). The
   integrating agent stood in for the human for the human-side
   commands: `signer-approve keygen`, `idbackup-attest --location …`,
   `idbackup-approve rehearse` (x3), `signer-approve revoke`, and the
   offline artifact copies. On a live integration those are never the
   agent's to run.

## State resolution

`SCUTL_IDBACKUP_STATE`, else `SCUTL_STATE`, else `~/.scutl/wallet`.
Set nothing for the live sepolia wallet; point `SCUTL_IDBACKUP_STATE`
at the msigner root for mainnet.

## Documented-vs-observed notes (reported upstream as doc bugs)

- `idbackup status` on an empty state dir returns
  `{"configured": false, …, "next": "run 'idbackup manifest' …"}`
  exit 0, not the ADAPT-documented `{"error": "not-setup", …}`.
- A passed rehearsal writes a `rehearsal.json` record into the live
  state dir (manifested artifact digests unchanged, `live_untouched:
  true`); "live dir digests did not change" holds for every manifested
  artifact but not file-set-literally.
- Rehearse over a corrupt copy that also post-dates a manifest refresh
  reports the mixed verdict as "partial" in its refusal message while
  still exiting 10 as documented.
