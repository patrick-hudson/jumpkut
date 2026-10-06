#!/usr/bin/env python3
"""Install Jumpkut for the current user without changing saved history."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from jumpkut import __version__
from jumpkut.settings import _quote_exec_argument


def check_dependencies() -> None:
    check = subprocess.run(
        [
            "/usr/bin/python3",
            "-c",
            "import sys; assert sys.version_info >= (3, 10); "
            "import gi; gi.require_version('Gtk', '3.0'); "
            "from gi.repository import Gtk; import Xlib",
        ],
        capture_output=True,
        text=True,
    )
    if check.returncode:
        raise RuntimeError(
            "Jumpkut needs Python 3.10+, GTK 3, PyGObject, and python-xlib "
            "for /usr/bin/python3.\n"
            "On Mint/Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0 python3-xlib\n"
            + check.stderr.strip()
        )


def install(prefix: Path) -> Path:
    source = Path(__file__).resolve().parent
    application_root = prefix / "share" / "jumpkut"
    application = application_root / "app"
    launcher = prefix / "bin" / "jumpkut"
    desktop = prefix / "share" / "applications" / "jumpkut.desktop"
    icon = prefix / "share" / "icons" / "hicolor" / "scalable" / "apps" / "jumpkut.svg"
    for target in (application, launcher, desktop, icon):
        if target.is_symlink():
            raise RuntimeError(f"Refusing to replace a symbolic link: {target}")
    if application.exists() and not application.is_dir():
        raise RuntimeError(f"Installation directory is occupied by a file: {application}")
    for target in (launcher, desktop, icon):
        if target.exists() and not target.is_file():
            raise RuntimeError(f"Installation file is occupied by a directory: {target}")

    application_root.mkdir(parents=True, exist_ok=True)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    icon.parent.mkdir(parents=True, exist_ok=True)
    # Only replace app/: history.json and any backups beside it are preserved.
    with tempfile.TemporaryDirectory(prefix=".install-", dir=application_root) as temporary:
        staging = Path(temporary) / "app"
        staging.mkdir()
        shutil.copytree(
            source / "jumpkut",
            staging / "jumpkut",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        shutil.copy2(source / "run.py", staging / "run.py")
        shutil.copy2(source / "LICENSE", staging / "LICENSE")
        shutil.copy2(source / "CHANGELOG.md", staging / "CHANGELOG.md")
        previous = Path(temporary) / "previous-app"
        if application.exists():
            application.rename(previous)
        try:
            staging.rename(application)
        except OSError:
            if previous.exists():
                previous.rename(application)
            raise

    run_path = str(application / "run.py")
    launcher.write_text(
        f"#!/bin/sh\nexec /usr/bin/python3 {shlex.quote(run_path)} \"$@\"\n",
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    shutil.copyfile(source / "jumpkut" / "assets" / "jumpkut.svg", icon)
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Jumpkut\n"
        "Comment=Clipboard history and quick selection\n"
        # GIO checks the first executable before expanding escaped percent
        # signs. A fixed executable also supports '%' in an install path.
        f"Exec=/usr/bin/env {_quote_exec_argument(str(launcher))} --history\n"
        "Icon=jumpkut\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "Keywords=clipboard;history;copy;paste;\n"
        "StartupWMClass=Jumpkut\n"
        "StartupNotify=false\n",
        encoding="utf-8",
    )
    return launcher


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Jumpkut for the current user")
    parser.add_argument(
        "--prefix",
        type=Path,
        default=Path.home() / ".local",
        help="installation base directory (default: ~/.local)",
    )
    options = parser.parse_args()
    try:
        check_dependencies()
        launcher = install(options.prefix.expanduser().resolve())
    except (OSError, RuntimeError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1
    print(f"Installed Jumpkut {__version__}. Start it with: {shlex.quote(str(launcher))}")
    print("You can also open Jumpkut from your application menu.")
    print("Enable Run at startup in Preferences to start it when you sign in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
