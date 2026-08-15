"""
Reproducible environment bootstrap for this project.

Creates a project-local virtualenv (.venv) if it doesn't exist yet, then
installs everything listed in requirements.txt into it by invoking the
venv's own pip directly.

Usage:
    python setup_env.py

Note on "activation": a script cannot activate a venv for its parent shell
(activation only sets environment variables in an interactive shell, and a
child process can't persist env changes back into its caller). This script
installs everything you need without activation, and prints the exact
command to activate the venv in your own shell afterwards, for interactive
use (e.g. running `streamlit run app.py` directly instead of via the venv's
full executable path).
"""

import subprocess
import sys
import venv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"

IS_WINDOWS = sys.platform == "win32"
VENV_PYTHON = VENV_DIR / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
ACTIVATE_CMD = (
    rf".venv\Scripts\activate" if IS_WINDOWS else "source .venv/bin/activate"
)


def create_venv():
    if VENV_PYTHON.exists():
        print(f"[setup_env] .venv already exists at {VENV_DIR} — skipping creation.")
        return
    print(f"[setup_env] Creating virtualenv at {VENV_DIR} ...")
    venv.create(str(VENV_DIR), with_pip=True)
    print("[setup_env] Virtualenv created.")


def install_requirements():
    if not REQUIREMENTS_FILE.exists():
        raise FileNotFoundError(f"Could not find {REQUIREMENTS_FILE}")

    print("[setup_env] Upgrading pip in the virtualenv ...")
    subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
    )

    print(f"[setup_env] Installing dependencies from {REQUIREMENTS_FILE.name} ...")
    subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)],
        check=True,
    )
    print("[setup_env] All dependencies installed.")


def main():
    create_venv()
    install_requirements()

    print()
    print("[setup_env] Done. To activate this environment in your own shell, run:")
    print(f"    {ACTIVATE_CMD}")
    print()
    print("[setup_env] Or, without activating, invoke tools directly, e.g.:")
    print(f"    {VENV_PYTHON} src\\build_data.py" if IS_WINDOWS else f"    {VENV_PYTHON} src/build_data.py")


if __name__ == "__main__":
    main()
