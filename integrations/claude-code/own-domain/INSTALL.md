# own-domain (odom) — Claude Code integration install record

Integrated 2026-10-13 from `recipes/own-domain/ADAPT-draft.md`
(manifest authority: `recipes/own-domain/recipe.yaml`, rev 1).
Harness: Claude Code (Fable 5 seat), Linux, Python 3 venv.

## What was set up

1. **Component present** (ADAPT Step 1): `pip install ./odom` into a
   dedicated venv. On this acceptance run the venv lived in session
   scratch (`<scratchpad>/odom-venv`); for a durable install use
   `python3 -m venv ~/.scutl/odom-venv && ~/.scutl/odom-venv/bin/pip
   install <scutl-repo>/recipes/own-domain/odom`. The install puts two
   executables on the venv bin: `odom` (agent-facing) and
   `odom-approve` (the human's approval helper — never exposed to the
   agent as a tool). Reference `odom` by absolute path.
   - Probe note: unconfigured `odom status` on the installed build
     answers exit 0 with `{"configured": false, "key_present": false}`
     on stdout, not the documented "stderr, exit 2 naming
     `odom admin configure`". No traceback; reported as a doc stumble.

2. **Tool exposure** (ADAPT Step 2): Claude Code offers no per-tool
   typed exposure or tool tiers; the odom CLI is driven through the
   generic Bash tool. Per the ADAPT document this generic-shell
   degradation is ACCEPTABLE — the pin, the ceiling, the idempotency
   key, and the escalation arithmetic hold in the component's own
   code. No wrapper was invented. The tool map, exit-code taxonomy,
   and human-confirmation expectation for `od_buy`/`od_renew` are
   carried in `SKILL.md` alongside this file.

3. **Guardrails in system context** (ADAPT Step 3):
   `execute.guardrails` and the `execute.loop` description from
   recipe.yaml are copied VERBATIM into `SKILL.md`. To arm a Claude
   Code session, install `SKILL.md` as a skill (e.g.
   `.claude/skills/own-domain/SKILL.md` in the project that will hold
   the domain) or paste its text into the session's system context.

4. **Setup with the human** (ADAPT Step 4): performed against the
   test registrar (the integrating agent stood in for the human at
   the desk — both `odom-approve` grants and key-file placement were
   human-side steps executed by the agent):
   - `odom-approve configure` then
     `odom admin configure --tld-allowlist com,net,org --ceiling-usd 15.00`
     (defaults ratified: horizon 45d, floor $20.00, max-domains 1 —
     raised to 2 on the /clean world per the acceptance text).
   - `odom-approve set-key` then
     `odom admin set-key --key-file <path>` (file consumed/deleted).
   - First `odom watch`: green, live numbers, no escalation.

5. **State**: `${SCUTL_ODOM_STATE:-~/.scutl/odom}`. Acceptance used
   fresh throwaway state dirs per scenario under session scratch. The
   human's `odom-approve` must run with the same `SCUTL_ODOM_STATE`
   as the agent's `odom`.

## Acceptance (ADAPT Step 5)

Run from inside the harness (Bash tool) against the servable test
registrar, started with the same venv from the scutl repo root:

```bash
<venv>/bin/python tools/odom_test_registrar.py --port 8495
```

(Port 8495 used instead of the documented 8494, which may be busy.)
All ten scenarios of the battery were executed and PASSED on
2026-10-13; full outputs are in the integration report delivered with
this install. `SCUTL_ODOM_API` was pointed ONLY at the local test
registrar — never at the live Porkbun rail. Nothing was registered.

## Live-rail go-live (not done here; owner steps)

- Scope a real Porkbun API key (IP-restricted), place it as a JSON
  file, and run the Step 4 ceremony with real `odom-approve` grants.
- Ratify all five walls deliberately (allowlist, ceiling, horizon,
  floor, max-domains).
- Leave `SCUTL_ODOM_API` unset (defaults to the real API) only when
  the owner has gated it: a real registration is real money.
