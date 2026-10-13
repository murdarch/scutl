# static-website recipe — Claude Code integration

Integrated 2026-10-13 from `recipes/static-website/ADAPT-draft.md`
(recipe.yaml rev 1 is the authority). Serving leaf: **provider-domain**
only; the custom-subzone leaf (and `sw_edge_attach`/`sw_edge_status`)
is NOT covered — edge ops refuse exit 5 here and the edge verify items
are config-N/A for this install.

## What was set up

1. **Component**: `sweb` installed into a venv at `~/.scutl/sweb-venv`:

       python3 -m venv ~/.scutl/sweb-venv
       ~/.scutl/sweb-venv/bin/pip install <scutl-repo>/recipes/static-website/sweb
       ~/.scutl/sweb-venv/bin/sweb status   # probe: configured:false, no traceback

   Two executables land on the venv bin: `sweb` (agent-facing) and
   `sweb-approve` (the HUMAN's one-shot-token helper for `admin
   configure` / `admin set-key`; never exposed to the agent). State:
   `${SCUTL_SWEB_STATE:-~/.scutl/sweb}`; `sweb-approve` must run with
   the same `SCUTL_SWEB_STATE` as the agent's op.

2. **Tool exposure**: Claude Code has no per-tool typed exposure; the
   harness tool is the generic Bash tool plus the skill text in
   `SKILL.md` (this directory), which maps each `components.sweb.tools`
   entry to its exact command. Per ADAPT Step 2, this generic-shell
   degradation is ACCEPTABLE — ceiling, ACL scope, publish-verify,
   export-first destroy, and the append-only log are enforced in the
   component's own code. Claude Code also cannot put `sw_destroy` /
   `sw_provision` behind a distinct tool tier; the guardrail text is
   carried instead (SKILL.md says to ask the human first), plus Claude
   Code's own Bash permission prompting.

3. **Guardrails**: `execute.guardrails` and the `execute.loop`
   description from recipe.yaml are carried **verbatim** in SKILL.md.

4. **Setup steps** (recipe.yaml `setup:`): `rail-present` is
   config-N/A under provider-domain serving (prov rail is
   custom-subzone-only). `ceiling-ratified`, `first-provision`, and
   `hello-page` were exercised against the servable test provider
   during acceptance (see below); no live subscription was created and
   nothing was billed. For a LIVE install the human runs `sweb-approve
   configure` / `sweb-approve set-key` themselves and the env seams
   `SCUTL_SWEB_API` / `SCUTL_SWEB_DATA` stay unset.

## Acceptance

Run from inside Claude Code (shell = the harness) against
`tools/sweb_test_provider.py --port 8464` (scutl repo root), one server
start for the whole battery, ceiling 6.00, fresh throwaway
`SCUTL_SWEB_STATE` per scenario. The integrating agent stood in for the
human on the `sweb-approve` grants (noted per ADAPT Step 4/5).

All 12 scenarios passed (2026-10-13): clean e2e (tier small/5.00,
serving:true, verify + reconcile clean) · pricey exit 5 quoting 18.00
vs 6.00, 0 subs · flaky-create exit 1 then retry `adopted:true`, same
id, exactly 1 sub · acl-drop `serving:false` naming index.html at
verify ("not publicly serving (public 403)") · wrong-ctype names
style.css Content-Type divergence · injection publishes byte-identical,
verify still runs · duplicate publish-id exit 6 · symlink exit 5 naming
the link · residue reconcile `clean:false` naming foreign-object
haxx.html + logged-but-absent index.html; destroy there refused exit 5
(export mismatch), nothing deleted · rotate `old_pair_dead:true`, no
key material in output · destroy on clean exported + hash-matched
first, `billing_stopped_verified_by: "fresh subscription list"`, 0
subs · edge-attach exit 5 naming the serving leaf (custom-subzone
items config-N/A).

## Known stumbles (reported upstream per ADAPT "If you get stuck")

- `sweb provision --cluster {cluster}`: the flag takes an **integer**
  cluster id; a string label errors with argparse usage (exit 1
  invalid). Neither ADAPT nor recipe.yaml says the type.
- flaky-create's documented "Reconcile, then retry": with no local
  subscription record yet, `sweb log --reconcile` exits 2
  not-provisioned rather than reconciling; the adoption actually
  happens via list inside the provision retry. Harmless, but the
  documented intermediate step cannot run as written.
