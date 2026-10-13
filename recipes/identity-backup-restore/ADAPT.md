# ADAPT — make this recipe available in your harness

You are an agent integrating the **identity-backup-restore** recipe
into a harness (Hermes, OMP, Claude Code, Codex, or any agent stack
that can run shell commands and carry a system prompt). This document
is addressed to you, the integrating agent. The human who pointed you
here wants one outcome: after you finish, an agent session in their
harness can keep the wallet's durable identity backed up, **prove**
any offline copy against a digest manifest without touching a secret,
and rehearse a true restore on the human's cadence — so that the day
the live state dir dies is a procedure, not a catastrophe.

`recipe.yaml` in this directory is the authority. This file never
overrides it; where they disagree, the manifest wins and the
disagreement is a bug worth reporting.

## What you are installing (30 seconds of orientation)

The organizing fact of this recipe is that **a backup that has never
restored is a hope, not a backup** — and the second fact is that **the
agent never moves key material**. The component (`idbackup`) writes a
backup **manifest** — a digest for every identity artifact, the kek
included, plus log checkpoints and the restore procedure — and the
HUMAN performs the copy. Verification then needs no secrets at all:
`ib_verify` hashes a copy and compares, naming exactly what is stale,
partial, corrupt, or foreign. The one op that decrypts anything is the
**rehearsal**: a true restore into a fresh temp dir that must derive
the right address, produce one real signature, and reconcile the spend
counters — human-approval-gated, with the live state dir read-only
throughout and proven untouched afterward.

The one hard boundary: **key material appears in no output, ever.**
Digests stand in for secrets everywhere — that is what makes the kek
backup verifiable at all. If a tool result would echo a secret, that
is a bug to report, not a value to relay.

**Rails, honestly stated:** this recipe has no network counterparty.
It reads and hashes a local wallet state dir (the wallet-base-sepolia
and/or wallet-mainnet layouts) and backup copies the human makes. The
optional live owned-resource probe inside the rehearsal is the only
network touch and is skipped when the registry is empty. Acceptance
below runs entirely against a **scratch identity** — no live wallet is
read, written, or needed.

## Step 1 — Make the component present

The component is a small Python package at `idbackup/` in this recipe
directory (repo: `recipes/identity-backup-restore/idbackup/`). It
depends on `scutl-signer`, the sibling wallet-base-sepolia component,
which is not on PyPI — install the signer **first, from its recipe
directory**, then idbackup:

```bash
python3 -m venv ~/.scutl/idbackup-venv                 # or any venv you manage
~/.scutl/idbackup-venv/bin/pip install ../wallet-base-sepolia/signer
~/.scutl/idbackup-venv/bin/pip install ./idbackup      # from this recipe directory
~/.scutl/idbackup-venv/bin/idbackup status
```

Expected first answer (pretty-printed; compare content, not
formatting): `idbackup status` is read-only and succeeds (exit 0) even
before an identity exists —

```json
{"configured": false, "tombstoned": false, "panicked": false,
 "manifest": null, "next": "run 'idbackup manifest' to author the backup manifest"}
```

— with `"configured": true` when pointed at an existing wallet state
dir. A traceback or a nonzero exit is not healthy — stop and report it.

The install puts **three** executables on the venv's bin: `idbackup`
(the agent-facing tool) and two **human-only** helpers — `idbackup-approve`
(grants the one-shot `rehearse` token) and `idbackup-attest` (records
that an offline copy exists at a location). Never expose the two
helpers to the agent as tools. The signer install adds `signer` and
`signer-approve`, used once below to create the scratch identity.

State resolution: `SCUTL_IDBACKUP_STATE`, else `SCUTL_STATE`, else
`~/.scutl/wallet`. The component reads the SAME state dir the wallet
lives in — that is the point — so for a live integration set nothing
and it finds the sepolia wallet; for mainnet point
`SCUTL_IDBACKUP_STATE` at the msigner root. For the acceptance below,
export one throwaway `SCUTL_STATE` and keep it identical for every
command, **the human's approve/attest commands included** — the tokens
and attestations land inside that state dir.

As in every recipe in this catalog: if your harness runs tools under a
restricted PATH (systemd units, containers), reference `idbackup` by
**absolute path**. Bare names resolved through PATH are the most
common integration failure in this catalog's history.

## Step 2 — Expose the tools

Map each entry in `components.idbackup.tools` (recipe.yaml) into your
harness's tool format. Expose them as **distinct** tools; do not merge
them into one free-form "run idbackup" tool:

- **`ib_status`** — `idbackup status` — ceremony + backup health:
  manifest age, drift since manifest, attestation count vs required,
  last rehearsal, owned-resource count. Read-only, never gated.
- **`ib_manifest`** — `idbackup manifest` — write/refresh
  `backup-manifest.json` from the LIVE state dir: every artifact with
  its sha256 (kek included), log line-checkpoints, owner address,
  restore procedure. Reads everything, moves nothing; the only write
  is the manifest itself. Optional knobs: `--locations N`
  (default 2), `--staleness-days N` (default 30). **A refresh resets
  all attestations** — the copies the human attested no longer match
  what must be backed up; the result says so.
- **`ib_verify`** — `idbackup verify --backup-dir {dir}` — check one
  offline copy against the manifest, secret-free (hashes only, no
  decrypt, no approval). Verdicts: `ok | stale | partial | corrupt |
  foreign`, each with the offending artifacts named, plus the exact
  counter delta when a spend log is truncated.
- **`ib_rehearse`** — `idbackup rehearse --backup-dir {dir}` — the
  TRUE restore: copy into a fresh temp dir, decrypt keystore under the
  backed-up kek, address match, one real signature, counter
  reconciliation, registry address checks. Approval-, panic-, and
  tombstone-gated; refuses (exit 10) any copy that does not verify
  clean, with one blessed exception — a copy whose ONLY defect is log
  truncation rehearses so the delta gets named with numbers. If your
  harness distinguishes tool tiers, put `ib_rehearse` behind the
  human-confirmation tier; if it cannot, carry the guardrail text and
  say so in your report.

One more entry point exists for the BUYING recipe, not for the human
loop: `idbackup own --provider P --resource R [--price X]
[--evidence TX]` appends to the owned-resources registry at purchase
time (the x402v2 dependency hook). Expose it only if this harness also
runs a buying recipe.

Preserve two properties whatever your harness's convention:

- **Exit-code taxonomy is the protocol.** 2 not-setup (no identity /
  no manifest — run the missing step) · 3 tombstoned (the identity is
  revoked; the tombstone IS the report — nothing verifies healthy,
  nothing rehearses) · 4 approval-required (ask the human to run
  `idbackup-approve rehearse`, retry once) · 9 panicked (rehearsal
  waits for unpanic) · 10 unverified-backup (fix the copy, never
  rehearse over it) · 1 invalid. Surface exit codes to the agent;
  don't collapse them into a generic error string.
- **Verification is free; rehearsal is deliberate.** Never ask for a
  rehearsal approval to answer a question `ib_verify` answers
  secret-free.

If your harness only offers a generic shell tool (no per-tool
exposure), that degradation is ACCEPTABLE — the approval gate, the
read-only live dir, and the secret-free verify all hold in the
component's own code either way. Say so in your report rather than
inventing a wrapper.

## Step 3 — Carry the guardrails into system context

Copy `execute.guardrails` and the `execute.loop` description from
recipe.yaml **verbatim** into the system prompt / skill text of the
agent that will run the backup ceremony. That text is small on purpose
and is graded wording — do not paraphrase it. It is what keeps "digests
stand in for secrets", "the live state dir is read-only to this
recipe", and "stale is a report, not a freeze" in front of the agent.

## Step 4 — Setup (with the human)

Follow `setup:` in recipe.yaml, in order. For a LIVE integration the
identity already exists (`signer status` answers healthy) and you skip
straight to the first manifest. For the acceptance run, create a
scratch identity first:

```bash
export SCUTL_STATE=$(mktemp -d /tmp/idbr-accept-XXXX)/wallet
# keygen is approval-gated — the HUMAN runs:
#   signer-approve keygen        (same SCUTL_STATE)
signer admin keygen --cap-per-tx 0.10 --cap-daily 1.00
```

Then, scratch or live:

- **`first-manifest`** — `idbackup manifest`. Expected: every identity
  artifact listed with a digest, `kek_digest_recorded: true`, counters
  checkpointed, and the human-copy note naming the locations count.
- **`human-copy`** (actor: human) — the human copies the manifested
  artifact set to the required offline locations and attests each:
  `idbackup-attest --location <label>` (same `SCUTL_STATE`). This step
  is **not yours to perform** — but check `ib_status` first: if
  attestations are already recorded, don't re-ask.
- **`first-verify`** — `idbackup verify --backup-dir <copy>`. Expected
  verdict `ok`.
- **`first-rehearsal`** (approval: human) — ask the human for
  `idbackup-approve rehearse`, then
  `idbackup rehearse --backup-dir <copy>`. Expected:
  `rehearsal_passed: true`, `signature_proven: true`,
  `live_untouched: true`.

## Step 5 — Acceptance (the recipe is not installed until this passes)

Run the `verify:` checks (recipe.yaml) from **inside the target
harness**, entirely against the scratch identity — never a live
wallet. Work in a scratch area; `cp` the state dir's manifested
artifacts to make "offline copies" (for acceptance you are standing in
for the human's copy step; on a live integration you never perform
it). One seeding step is scratch-only and explicitly blessed here: the
scratch identity has no spend history, so **seed a synthetic spend log
before manifesting** to make the counter arithmetic testable —

```bash
printf '%s\n' \
 '{"payment_id":"p1","status":"settled","amount":"0.05","to":"0x1111111111111111111111111111111111111111"}' \
 '{"payment_id":"p2","status":"settled","amount":"0.07","to":"0x1111111111111111111111111111111111111111"}' \
 > "$SCUTL_STATE/spend.log"
```

(Never do this to a real state dir; the live spend log belongs to the
signer alone.)

If the harness cannot hold a session yet (no model, no auth), report
the acceptance as **blocked-on-harness**, list what you verified from
outside with that caveat, and never present an outside-the-harness run
as the acceptance:

1. **manifest** — after keygen + seeding: `idbackup manifest` lists
   keystore.json, kek, network.json, caps.json, and spend.log with
   digests; `kek_digest_recorded: true`; counters show `0.12` settled
   over 2 lines. No secret bytes anywhere in the output.
2. **pristine copy verifies ok** — copy every manifested artifact to
   `copy-A/`; `idbackup verify --backup-dir copy-A` → verdict `ok`,
   `healthy: true`.
3. **attest + status** — the human (you, standing in) runs
   `idbackup-attest --location copy-A`; `ib_status` shows 1 of 2
   recorded. Refresh the manifest and confirm `attestations_reset:
   true` — then re-attest and re-copy if you refreshed after seeding.
4. **partial** — `copy-B` missing its `kek` → verdict `partial`,
   `missing: ["kek"]`. A backup missing its kek digest-matches
   nothing; the report says so plainly.
5. **corrupt** — `copy-C` with one byte flipped in `caps.json` →
   verdict `corrupt`, `mismatched: ["caps.json"]`.
6. **planted extra** — `copy-D` with a `planted.json` the manifest
   never listed → verdict `corrupt`, `extra: ["planted.json"]`.
7. **log truncation names the delta** — `copy-E` with only the first
   line of `spend.log` → verdict `corrupt`, `truncated` names the log
   with lines expected vs present, and `counter_delta` quotes
   expected `0.12` vs present `0.05`, delta `0.07` — the exact
   re-armed budget, named with numbers.
8. **wrong wallet** — keygen a SECOND scratch identity in a separate
   state dir, copy it to `copy-F`, verify against the FIRST manifest →
   verdict `foreign` with both addresses quoted. A complete, valid
   backup of the wrong wallet is not a backup.
9. **stale** — against a verdict-ok copy, drift the live state
   (`idbackup own --provider test --resource demo` appends to the
   registry) → `verify` on the same copy now says `stale` with
   `drifted_since_manifest` naming what moved; `ib_status` shows
   `stale: true`. Stale is a report with names, not a failure.
10. **rehearsal is gated, then proves** — `idbackup rehearse
    --backup-dir copy-A` without a token → **exit 4**. Human grants
    `idbackup-approve rehearse`; re-run → `rehearsal_passed: true`,
    `signature_proven: true`, `counter_delta` delta `0` (use a copy
    taken after the final manifest), `live_untouched: true`. Confirm
    the MANIFESTED artifacts' digests did not change — a passed
    rehearsal legitimately writes its own `rehearsal.json` record into
    the state dir (excluded from the identity set, like the manifest
    itself), so "untouched" means the identity artifacts, not the
    directory listing.
11. **rehearsal refuses a bad copy** — grant a fresh token, rehearse
    against `copy-C` (corrupt) → **exit 10** unverified-backup,
    nothing written to live. (The refusal message quotes the verdict
    the copy earns against the CURRENT manifest — a copy taken before
    a later manifest refresh may read `partial` rather than `corrupt`;
    the exit code is the contract, the verdict word tracks the live
    manifest.) (The truncated-only `copy-E` is the one
    blessed exception: with a token it rehearses and its report names
    the delta and the amnesia warning.)
12. **tombstone ends the story** — revoke the scratch identity (the
    human grants `signer-approve revoke`, then `signer admin revoke`)
    → `idbackup verify` reports `tombstoned: true` and `healthy:
    false`; `idbackup rehearse` (with a token) → **exit 3**. Run this
    LAST — it is terminal for the scratch identity.

Report results to the human as a checklist with the actual outputs
(verdicts, the named artifact lists, the counter-delta numbers, exit
codes). Any check you skipped, say so plainly. Then delete the scratch
state dirs and copies — they hold a throwaway key, but tidiness is
part of the ceremony.

## If you get stuck

A stumble in this process is a documentation bug we want. Report it to
the catalog (issue, or tell your human) with: the step, the exact
command, the exact error, your harness and version. The acceptance
standard for this document is that a fresh agent completes it without
outside help.
