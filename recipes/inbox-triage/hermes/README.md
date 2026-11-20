# Canonical Sift native plugin

This directory is the maintained Hermes integration for the source-bound
experimental `recipes/inbox-triage/recipe.yaml`, not a Hermes fork.
It ships seven native tools and the read-only namespaced skill
`sift-native:inbox-triage`. Installing it replaces model-written adapter/skill
boilerplate with maintained source. That changes the measurement: installation
and a no-model smoke do not establish fresh-model acceptance, model recovery,
or a semantic comparison with previous trials. No live mailbox provider is
blessed by this recipe revision.

Use fresh isolated candidate state/profile only. There is no legacy verdict
backfill, old-summary shim or production-state migration. Earlier adopted
source acceptance is not candidate qualification.

## Install from the source checkout

The recipe-root quick start is [`../INSTALL.md`](../INSTALL.md):

```sh
HERMES_HOME=/absolute/path/to/intended/profile python3 install_hermes.py
```

Run that command from `recipes/inbox-triage/`; the root entry delegates to
this directory's maintained `install.py`, with identical refusal and native
enable behavior. The detailed commands below run from the **scutl repository
root**. Hermes must already be installed and on `PATH`. Choose the intended
profile explicitly; the installer never guesses a sticky-active profile.

1. If Sift is not already installed at its fixed location, install the canonical
   component using the recipe's component-install procedure. Its executable
   **must** be `$HOME/.scutl/sift-venv/bin/sift`; the plugin has no executable
   override. For a new component venv, the equivalent commands are:

   ```sh
   python3 -m venv "$HOME/.scutl/sift-venv"
   "$HOME/.scutl/sift-venv/bin/python" -m pip install recipes/inbox-triage/sift
   ```

2. Install the shipped plugin into a new plugin slot and enable it through
   Hermes' own command:

   ```sh
   export HERMES_HOME=/absolute/path/to/intended/profile
   python3 recipes/inbox-triage/hermes/install.py
   ```

   `install.py` copies `__init__.py`, `plugin.yaml`, and the bundled `skills/`
   into `$HERMES_HOME/plugins/sift-native`, then runs
   `hermes plugins enable sift-native`. Existing destinations are refused,
   never merged or overwritten. The copy step does not generate Python/skills,
   install or configure Sift, place credentials, or grant approval tokens.
   Native enable still performs Hermes' own environment admission: it needs a
   writable managed store and its required dependencies available from cache
   or package indexes, even for this stdlib-only external-runtime plugin.
   If native enable refuses, the copied files remain; fix its reported cause
   and run `hermes plugins enable sift-native` in that same profile.
   To replace a reviewed installation, first disable it and remove that plugin
   through Hermes' normal plugin-management commands, then install the new
   source. Sift's state lives separately and is not part of the plugin copy.

3. Follow the canonical `ADAPT.md` for component configuration, the operator's
   human approval, credential placement, and the recipe acceptance procedure.
   Plugin registration itself requires none of those state/credential
   prerequisites. `sift_status` remains callable before configure and after
   decommission. This plugin never exposes `sift-approve` or any token reader.

Keep `HERMES_HOME` set to the same profile for subsequent Hermes commands.
Restart an existing session after native enable, as directed by Hermes.

To use the plugin in a Hermes session, explicitly preload the shipped skill
with `--skills sift-native:inbox-triage` (or explicitly load it with
`skill_view("sift-native:inbox-triage")`). The toolset is `sift-native`.
Do not create a second unnamespaced skill in the profile's global `skills/`.

## Handler and transport contract

The module exports `register(ctx)` and
`handle_status`, `handle_fetch`, `handle_read`, `handle_triage`, `handle_report`,
`handle_draft`, `handle_admin`, taking an argument object plus optional Hermes
context. Seven raw `{name, description, parameters}` schemas are registered.

The executable is fixed from `Path.home()` when the module is imported. Each
valid operation makes exactly one real CLI call with a literal argv list, no
shell and no retry. Every user-value flag is one `--flag=value` argv element:
bare `--leading-dash` or `-x`, dollars, quotes, spaces, and newlines remain data.
Paths are literal local paths, not shell expressions; prefer absolute paths for
body files. Sift controls recipient confinement, append-only verdicts,
duplicate refusal, cap refusal, and human approval; the plugin does not replace
those checks.

Completed calls return a JSON string with the actual integer `exit_code` and
exact UTF-8-decoded `stdout` and `stderr` strings, preserving stream separation
and line endings. The CLI's stdout JSON is not parsed, summarized, or reworded.
Invalid native inputs or a failed subprocess launch/decode instead return an
explicit `error: adapter-error`, message, `exit_code: 1`, and empty streams;
that is an adapter refusal, not a fabricated CLI result. There is no arbitrary
command, recipient, admin operation, or approval-token tool.

`sift_read({"message_id": ...})` returns top-level `source_ref` and
`source_version`, retaining provenance, untrusted body/banner and complete
indexed `untrusted_content.passages` with exact line endings. Receipt
persistence changes managed-state revision but does not mark seen.

`sift_triage` requires exactly `message_id`, `category`, `source_ref`,
`passages` (integer array) and `missing_context` (enum array). No free
`summary`, identity/referent alias or factual prose fallback remains.
Passages are at most four distinct nonnegative complete-line indices, with
1024 selected UTF-8 bytes; source subjects above 512 UTF-8 bytes fail, never
truncate. Enum values are only `calendar`, `sent-history`, `answer-context`.
Booleans are not integer selectors. Unknown/stale/foreign/tampered receipts
and invalid evidence fail before append. The component creates visibly
quoted message id/from/full subject and exact selected source text.

After every managed-state mutation, including reads, obtain real
`sift_status({})`. Use its `snapshot_revision` in
`sift_report({"checkpoint": ...})`; this invokes literal argv
`report`, `--checkpoint=<value>`, `--output=/work/report.md`, with no shell.
No native output/path override exists. The runtime provides writable `/work`
outside managed state. The real renderer returns `path`, `sha256`,
`snapshot_revision`, `source_bindings`, `record_bindings`, `draft_bindings`,
`state_unchanged`, projecting actual accepted records/drafts and the five
counts `triaged`, `suspicious`, `drafts`, `batch.size`, `batch.remaining`.
The first three are cumulative; batch counts describe the saved fetch.
The report file is observed separately; projection leaves managed state
unchanged. Stale state needs a new real in-budget checkpoint/projection,
never an operator repair, hidden retry or free paraphrase.

An empty or incomplete passage selection may be structurally valid and
still fail semantic/materiality review. Body-only identifiers, referents,
uncertainty, negations, sensible categories and useful concision remain the
operator's responsibility. Missing/rewritten/stale reports and unsupported
final-chat claims remain consumer failures. Safe component operations do
not confine other Hermes tools or prove whole-agent safety.

## Canonical context regeneration

`skills/inbox-triage/SKILL.md` is a shipped generated artifact. Its thin native
invocation mapping is followed by **unchanged** smol output from the existing
`tools/emit.py` lowerer, using the canonical recipe's default resolution, plus
the canonical `execute.loop` and every `execute.guardrails` entry. This retains
`smol.state_rule`, `exit_codes`, `action`, and `emergency`. Defaults are not
permission to overwrite an existing configuration: query `sift_status` for
actual state. The recipe manifest remains the only workflow authority.

After changing the canonical manifest or lowering implementation, regenerate
from the source checkout using the repository environment (including PyYAML):

```sh
.venv/bin/python recipes/inbox-triage/hermes/refresh_context.py
```

The command emits the canonical smol profile to a temporary directory, appends
the execute loop and guardrails directly from the same recipe manifest, and
adds the native invocation mapping. Its original `bundle.json` metadata plus
the generator path is shipped as `skills/inbox-triage/bundle.json`; the SHA-256
identifies the exact recipe source. Do not hand-edit the generated skill or
treat it as an independently maintained recipe. The installed plugin needs
only the stdlib, its bundled files, and the external Sift CLI; it never imports
the lowerer or generates context at runtime.

## Source-backed integration and qualification boundary

The supported directory/enable path was inspected in genuine Hermes commit
`eaac327fac2c2db0b77ee143989d49b29a6f36cb`:

- `website/docs/developer-guide/plugins/index.md`: "Step 1: Create the plugin
  directory", "Step 2: Write the manifest", "Bundle skills", and the
  `python_runtime: external` reference.
- `hermes_cli/plugins_discovery.py`: `scan_directory` and
  `collect_directory_manifests` discover `$HERMES_HOME/plugins/<name>`.
- `hermes_cli/plugins_cmd.py`: `cmd_enable` owns native activation/configuration.
- `hermes_cli/plugins_context.py`: `register_tool` registers native raw schemas.
- `hermes_cli/plugins_content.py`: `register_skill` registers the bundled path
  under the plugin namespace without copying it into global skills.
- `hermes_cli/_parser.py`: `--skills` explicitly preloads named skills.

`hermes plugins install` in that source is Git-clone-based; this procedure does
**not** claim it accepts a raw local directory. It uses the documented native
directory layout followed by native enable, with no Hermes patch or model code
generation.

The previously adopted six-tool/free-summary smoke and its test totals do not
apply to this candidate. Qualify the actual installed seven schemas, generated
skill and real HTTP/native/CLI/component/report path in disposable state.
Permanent regression tests cover strict admission, durable literal source
content, duplicate/error nonmutation, recipient/path confinement and stale
checkpoint refusal, not incidental wording or mock forwarding echoes.

Installation, no-model qualification and a source-bound renderer do not
establish a semantic mailbox-task result, model reliability, full ADAPT
acceptance, live-provider acceptance or production migration.
