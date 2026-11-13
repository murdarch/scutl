# Install inbox triage in Hermes

From **this recipe directory** (`recipes/inbox-triage/`), run:

```sh
HERMES_HOME=/absolute/path/to/intended/profile python3 install_hermes.py
```

Use your actual absolute profile path. The command delegates to the maintained
[`hermes/install.py`](hermes/install.py); it does not generate an adapter or
skill. From another working directory, use the absolute path to
`install_hermes.py`. No files are resolved relative to your working directory.

## Prerequisites and limits

- Hermes must already be installed and its `hermes` command on `PATH`. Native
  enable also needs a writable managed store and available dependencies, from
  an existing cache or permitted package indexes. The command does not install
  Hermes or change its runtime policy.
- The fixed component path is `$HOME/.scutl/sift-venv/bin/sift`. Follow
  [ADAPT Step 1](ADAPT.md#step-1--make-the-component-present) if it is absent.
  The plugin can register without component configuration; that alone does not
  make mailbox operations ready.
- The operator places mailbox credentials and approves configuration. This
  command does neither, grants no approval tokens and touches no real mailbox.
  No live mailbox provider is blessed by this revision.

The command copies the shipped plugin and namespaced skill into
`$HERMES_HOME/plugins/sift-native`, then invokes **Hermes' own**
`hermes plugins enable sift-native`. An existing slot is refused, never
merged or overwritten. A nonzero exit is a failed installation, not acceptance.
If native enable fails after copying, the files remain with the original
error; resolve that prerequisite and use `hermes plugins enable sift-native`
in the same explicit profile. Do not rerun this copy command over the slot.

Keep `HERMES_HOME` set to that profile for subsequent Hermes commands. Restart
an already-running Hermes session after enable. In the next session, explicitly
preload `sift-native:inbox-triage` with `--skills sift-native:inbox-triage` (or
load it through `skill_view`); the native toolset is `sift-native`.

Continue with [ADAPT](ADAPT.md#step-3--carry-the-guardrails-into-system-context)
for human setup and scoped acceptance. Detailed transport and source contracts
are in the [native integration reference](hermes/README.md). Installation or a
no-model smoke is not fresh-agent acceptance, full ADAPT acceptance or a
successful mailbox-task comparison. The component confines its own operations,
not Hermes' other tools or the whole agent.
