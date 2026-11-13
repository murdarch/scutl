# Canonical Sift native plugin

This directory is the maintained Hermes integration for the **existing**
`recipes/inbox-triage/recipe.yaml`, not an independent recipe or a Hermes fork.
It ships six native tools and the read-only namespaced skill
`sift-native:inbox-triage`. Installing it replaces model-written adapter/skill
boilerplate with maintained source. That changes the measurement: installation
and a no-model smoke do not establish fresh-model acceptance, model recovery,
or a semantic comparison with previous trials. No live mailbox provider is
blessed by this recipe revision.

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
`handle_status`, `handle_fetch`, `handle_read`, `handle_triage`, `handle_draft`,
`handle_admin`, each taking an argument object plus optional Hermes keyword
context. The six tool schemas are raw `{name, description, parameters}` objects.

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

## Source-backed native integration and no-model checks

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

A no-model component/API smoke against that commit ran in disposable isolated
`HOME` and `HERMES_HOME` directories, with private writable managed-store/cache
copies, a real canonical Sift entry point at the fixed path, and a local
mailbox fixture. The actual installer and
`hermes plugins doctor <installed-path> --ci` both exited zero. Genuine native
PluginManager/tool-registry discovery exposed all six tools, and native skill
preloading included the exact shipped `sift-native:inbox-triage` body.

Real native dispatch also checked unconfigured refusal, literal summaries in
stdout and the durable log, duplicate refusal without a log append, draft
recipient confinement with a bare flag-like body-file path, approval refusal
without config mutation, and a subsequent fetch skipping logged IDs. The
smoke exposed neither a model endpoint nor external network access.

The behavioral suite plus the existing emitter and manifest contracts passed
119 tests:

```sh
.venv/bin/python -m pytest recipes/inbox-triage/sift/tests tests/test_emit.py tests/test_manifest_contracts.py -q
```

These checks establish install/configure and component/API behavior, not a
semantic mailbox-task result, a subject-model run, fresh-model recovery,
full `ADAPT.md` acceptance, or live mailbox-provider acceptance.
