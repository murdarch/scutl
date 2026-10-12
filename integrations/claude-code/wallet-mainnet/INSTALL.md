# wallet-mainnet → Claude Code (reference integration)

Produced by the fresh-agent acceptance run of 2026-10-11 (cst-iepc;
receipts/mwallet/2/acceptance.json). The agent received the recipe
directory and "make this available in Claude Code", nothing else.

What the integration consists of:

- `SKILL.md` (this directory) — installed at
  `<project>/.claude/skills/wallet-mainnet/SKILL.md`. Carries the tools
  table with absolute binary paths, the `SCUTL_STATE` requirement for
  `x402-buy`, the 0–9 exit-code taxonomy, and `execute.loop` +
  `execute.guardrails` verbatim from recipe.yaml.
- Project `settings.json` permission allowlist for `msigner panic` and
  `msigner status` (absolute paths), so panic is never behind a
  permission prompt (ADAPT step 3's reachability requirement).
- Degradation note: Claude Code exposes a generic Bash tool rather than
  six named tools; acceptable per ADAPT step 2 — caps, ceremony gate,
  approval gates, and panic all hold in signer code.

Evidence: `acceptance-transcript.jsonl` (the clean-context agent's full
transcript) and `receipts/mwallet/2/acceptance.json` (checklist with
actual outputs, including the live settle tx).
