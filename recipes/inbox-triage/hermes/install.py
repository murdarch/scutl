"""Install this shipped directory plugin, then use Hermes' native enable command.

Run with an explicit HERMES_HOME and the installed `hermes` command on PATH.
This does not install Sift, configure its mailbox, or grant approval tokens.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    home = os.environ.get("HERMES_HOME", "").strip()
    if not home:
        sys.exit("Set HERMES_HOME explicitly to the intended Hermes profile directory.")
    profile = Path(home).expanduser()
    if not profile.is_absolute():
        sys.exit("HERMES_HOME must be an absolute profile directory.")
    hermes = shutil.which("hermes")
    if hermes is None:
        sys.exit("The installed Hermes CLI must be on PATH before installing this plugin.")
    source = Path(__file__).resolve().parent
    if not (source / "skills" / "inbox-triage" / "SKILL.md").is_file():
        sys.exit("Missing bundled context; regenerate it from the canonical source recipe first.")
    target = profile / "plugins" / "sift-native"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "install.py", "refresh_context.py", "README.md",
        ))
    except OSError as exc:
        sys.exit(f"Plugin copy failed (existing installs are never overwritten): {exc}")
    env = dict(os.environ, HERMES_HOME=str(profile))
    result = subprocess.run([hermes, "plugins", "enable", "sift-native"], env=env, shell=False)
    if result.returncode:
        print(f"Plugin files remain at {target}; native enable did not succeed. "
              "Resolve the reported refusal, then run hermes plugins enable sift-native.", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
