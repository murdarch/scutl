# agent-email — Claude Code integration (rev 1, bench-first)

What was set up to make the `agent-email` recipe available in Claude
Code, per `recipes/agent-email/ADAPT-draft.md` (recipe.yaml is the
authority).

## Component install (Step 1)

```bash
python3 -m venv ~/.scutl/amail-venv
~/.scutl/amail-venv/bin/pip install <scutl>/recipes/agent-email/amail
~/.scutl/amail-venv/bin/amail status   # probe
```

One executable on the venv bin: `amail`. Reference it by absolute
path (`~/.scutl/amail-venv/bin/amail`) — Claude Code tool calls may
run under a restricted PATH. State lives at
`${SCUTL_AMAIL_STATE:-~/.scutl/amail}`; nothing there is hand-edited
except `provider.cred`, which the human places.

Note: the ADAPT doc says the unconfigured probe exits 2 with
`{"error": "not-configured", ...}` on stderr; the installed component
actually exits 0 with `{"configured": false, ...}` on stdout.
Reported as a documentation stumble.

## Tool exposure (Step 2)

Claude Code offers a generic shell tool (Bash) rather than per-tool
exposure; per the ADAPT doc this degradation is ACCEPTABLE — the
allowlist, ceiling, idempotency, draft-gate, and trust-label walls
hold in the component's own code. The six tools (am_inbox_status,
am_list, am_read, am_send, am_reply, am_log) are mapped as distinct
documented invocations in `SKILL.md` alongside the exit-code
taxonomy. No merged free-form wrapper was invented.

## Guardrails in system context (Step 3)

`execute.guardrails` and the `execute.loop` description from
recipe.yaml are carried **verbatim** into `SKILL.md` in this
directory. Drop `SKILL.md` into `.claude/skills/agent-email/SKILL.md`
(project) or `~/.claude/skills/agent-email/SKILL.md` (global) so the
session that works the inbox loads it.

## Setup (Step 4)

- `wallet-and-client-present` — **config-N/A against the test
  provider** (live-rail: wallet + x402v2 client). Not simulated.
- `inbox-owned` (purchase half) — **config-N/A**: against the test
  provider the inbox exists as the scenario world and the credential
  is the one the scenario index names. The human-side act is placing
  `provider.cred`:
  `printf '<credential>' > $SCUTL_AMAIL_STATE/provider.cred`
- `allowlist-ratified` (OWNER operation):
  ```bash
  amail admin configure --inbox agent@ours.example \
      --allow owner@home.example --allow partner.example \
      --daily-ceiling 20 --first-contact draft-gate
  ```
- `loop-drill` — `amail list --unreplied` (clean read path, auth
  labels visible).

During acceptance the integrating agent stood in for the human for
the owner operations (configure, credential placement), as the task
directed.

## Acceptance (Step 5)

Test provider (needs the same venv, run from the scutl repo root):

```bash
~/.scutl/amail-venv/bin/python tools/amail_test_provider.py --port 8474
```

Per scenario: fresh throwaway `SCUTL_AMAIL_STATE`, export
`SCUTL_AMAIL_PROVIDER=http://127.0.0.1:8474/<scen>`, run the
configure step, place that scenario's credential (`cred-<scen>`).
Proof surfaces: `GET /<scen>/wire` and `GET /<scen>/drafts`. The
10-scenario battery and results are in the acceptance report
delivered with this integration.
