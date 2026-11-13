"""Recipe-root entry point for the shipped Hermes plugin installer.

From this recipe directory, with Hermes on PATH:
    HERMES_HOME=/absolute/profile python3 install_hermes.py
"""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "hermes" / "install.py"),
                   run_name="__main__")
