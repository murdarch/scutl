# uptime-monitoring (beacon) — Claude Code integration install record

Integrated 2026-10-19, per `recipes/uptime-monitoring/ADAPT.md`.
Harness: Claude Code (shell access via the Bash tool).

## What was set up

### Step 1 — component present

```bash
python3 -m venv <venv>
<venv>/bin/pip install recipes/uptime-monitoring/beacon
```

Probe: `beacon status` in a fresh `SCUTL_BEACON_STATE` answered
`{"configured": false}` — healthy per the ADAPT. (Without the env var
the CLI found this host's LIVE beacon state at `~/.scutl/beacon` and
reported its real classifications — exactly why the ADAPT requires a
dedicated state dir per world; acceptance ran entirely in throwaway
state dirs and never touched the live deployment.)

Two executables on the venv bin: `beacon` (agent tool) and
`beacon-approve` (the human's helper; never exposed as an agent tool).

### Step 2 — tool exposure

Claude Code offers a generic shell tool (Bash), not per-tool typed
exposure. Per ADAPT Step 2's explicit allowance, this generic-shell
degradation is ACCEPTABLE: the two-domain up-truth, drift hard-fail,
approval scoping, and append-only ledgers hold in the component's own
code. The tool mapping (bc_status … bc_deregister), the exit-code
taxonomy, and the guardrails are carried in `SKILL.md` next to this
file.

### Step 3 — guardrails in system context

`execute.guardrails`, the `execute.loop` description, and the three
`on_*` clauses copied verbatim from recipe.yaml into `SKILL.md`.

### Step 4 — setup

Against the servable test prober (`tools/beacon_test_prober.py`, port
8445), per scenario: `beacon-approve configure` →
`beacon admin configure --prober-api-base http://127.0.0.1:8445/<scn>/v3
...` → key file at `$SCUTL_BEACON_STATE/prober_api.key` (0600) →
register → probe/verify/report. Canary-register idempotence: second
identical register answered `"converged": true`, one monitor at the
ground-truth console.

## Acceptance (ADAPT Step 5) — 16/16, 4 cells bench-covered

| # | Cell | Result |
|---|------|--------|
| 1 | clean end-to-end | `up-corroborated`, coverage full, escalate=false, probe ok=true |
| 2 | idempotent register | `"converged": true`, one monitor in `/clean/account` |
| 3 | 200-from-the-grave (`/corpse-proxy`) | probe ok=false on a 200 parking page; `down-confirmed`, escalate=true |
| 4 | unreachable split | `unreachable`, incident quoted, escalate=true |
| 5 | internal-down split | `internal-down`, escalate=true |
| 6 | stale serial (`/stale-serial`) | probe ok=false with sentinel MATCHED (`serial_fresh=false`); `internal-down` |
| 7 | stale-equals-up (`/prober-deaf`) | `prober-deaf`, "evidence absent (horizon 1200s) — the state label 'up' is not evidence"; coverage degraded |
| 8 | paused monitor (`/paused-silent`) | `prober-deaf` with paused-monitor breach on the second verify |
| 9 | watching the wrong door (`/config-drift`) | drift hard-fail quoting ledger vs monitor; re-register refused exit 5 ("would launder the tamper evidence") |
| 10 | foreign monitor | `legacy-billing-page` breached, never adopted or deleted; own target still `up-corroborated` |
| 11 | zombie watch (`/zombie`) | DELETE no-op at the prober → "the watch outlived its tombstone" breach |
| 12 | prober dark (closed port :1) | register refused exit 1 `prober-unreachable` ("Connection refused") |
| 13 | revoked key (`/revoked-key`) | refused "prober rejected the key (HTTP 401)"; key absent from all output (grepped) |
| 14 | register past cap (max-targets 1) | exit 5 quoting "1 target(s) registered, max_targets is 1 … NOT watched" |
| 15 | duplicate observation (`--oid` reuse) | exit 5 "observations are exactly-once" |
| 16 | deregistration ceremony | no token → exit 4; token for target A spent on B → exit 4, A's token stayed armed and later retired its own watch; correct token deregistered with history (`lifetime_probes` quoted); tombstoned probe refused as zombie-probe |

Bench-covered (graded-model cells, not component refusals):
flap-without-smoothing, deaf-verifier, average-hiding,
disclosure-is-not-alarm — covered by the scutbench beacon slices
(public 72/72 + heldout 15/15, safety pass post-hardening,
`ladder/beacon/qwen36-27b-public-report3.json` /
`-heldout-report2.json`, cst-mdiz.15.1).

## Stumbles worth recording

- `beacon status` with no `SCUTL_BEACON_STATE` reads `~/.scutl/beacon`
  — on a host with a live deployment that is the LIVE ledger. The
  ADAPT now says explicitly: fresh install, fresh state dir, always.
- The `paused-silent` and `config-drift` knobs fire on the account's
  SECOND read; a single verify after register sees the pre-fault
  world. The ADAPT instructs two verifies and judging the second.
- The frozen-evidence scenario must pin its freeze well past the
  horizon (the test prober pins it 2h back); freezing "from now"
  stays inside the horizon and honestly classifies up-corroborated.

Full suite after integration: 1282/1282 green.
