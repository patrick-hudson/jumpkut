#!/usr/bin/env python3
"""Build and install Jumpkut for the current user without changing saved history.

Installs `<prefix>/bin/jumpkut`, its application-menu entry and icon. Saved
history, backups and preferences (`<prefix>/share/jumpkut/*`, `~/.config/jumpkut`)
are never touched; only the old Python app directory `<prefix>/share/jumpkut/app`
is removed.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
MIN_WL_CLIPBOARD = (2, 3, 0)


def version() -> str:
    table = re.search(r"(?ms)^\[package\][ \t]*\n(.*?)(?=^\[|\Z)", (ROOT / "Cargo.toml").read_text(encoding="utf-8"))
    match = table and re.search(r'(?m)^version\s*=\s*"([^"]+)"', table.group(1))
    if not match:
        raise RuntimeError("Cargo.toml has no [package] version")
    return match.group(1)


def quote_exec_argument(argument: str) -> str:
    """Quote one desktop-entry Exec argument (both escaping layers, % doubled)."""
    argument = argument.replace("%", "%%")
    escaped = "".join("\\" + c if c in '\\"`$' else c for c in argument)
    escaped = escaped.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")
    return f'"{escaped}"'


def find_cargo() -> str:
    cargo = shutil.which("cargo") or str(Path.home() / ".cargo/bin/cargo")
    if not os.access(cargo, os.X_OK):
        raise RuntimeError(
            "Building Jumpkut needs Rust (cargo). Install it for your user with\n"
            "  curl https://sh.rustup.rs -sSf | sh\n"
            "then run ./install.py again. No system -devel packages are needed.")
    return cargo


def build() -> Path:
    cargo = find_cargo()
    subprocess.run([cargo, "build", "--release", "--locked"], cwd=ROOT, check=True)
    binary = ROOT / "target/release/jumpkut"
    if not binary.is_file():
        raise RuntimeError(f"cargo did not produce {binary}")
    return binary


def wl_clipboard_version() -> tuple[int, ...] | None:
    try:
        output = subprocess.run(["wl-paste", "--version"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"wl-clipboard (\d+)\.(\d+)(?:\.(\d+))?", output)
    return tuple(int(part or 0) for part in match.groups()) if match else None


def session_problem(environ=os.environ) -> str | None:
    """Why this session cannot run Jumpkut, if it cannot (X11 needs nothing)."""
    if environ.get("XDG_SESSION_TYPE") != "wayland":
        return None
    if "KDE" not in environ.get("XDG_CURRENT_DESKTOP", "").split(":"):
        return ("On Wayland, Jumpkut currently supports KDE Plasma. "
                "On other desktops, choose an X11 session at login.")
    found = wl_clipboard_version()
    if found is None or found < MIN_WL_CLIPBOARD:
        detail = ".".join(map(str, found)) if found else "not installed"
        return (f"Jumpkut needs wl-clipboard {'.'.join(map(str, MIN_WL_CLIPBOARD))} or newer on Wayland "
                f"(found: {detail}), so that password copies stay out of history.\n"
                "On Fedora: sudo dnf install wl-clipboard · On Debian/Ubuntu: sudo apt install wl-clipboard")
    return None


def _refuse_links(*paths: Path) -> None:
    for path in paths:
        if path.is_symlink():
            raise RuntimeError(f"Refusing to replace a symbolic link: {path}")
        if path.exists() and path.is_dir():
            raise RuntimeError(f"Installation file is occupied by a directory: {path}")


def _replace_file(source: Path, target: Path, mode: int, text: str | None = None) -> None:
    """Write `target` atomically from `source` (or `text`)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(text.encode("utf-8") if text is not None else source.read_bytes())
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def autostart_path() -> Path:
    override = os.environ.get("JUMPKUT_AUTOSTART_DIR")
    if override and os.path.isabs(override):
        return Path(override) / "jumpkut.desktop"
    config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config) if config and os.path.isabs(config) else Path.home() / ".config"
    return base / "autostart/jumpkut.desktop"


def migrate_autostart(old_app: Path, launcher: Path) -> bool:
    """Point a startup entry written by the Python app (run.py) at the binary."""
    path = autostart_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = text.splitlines(keepends=True)
    changed = False
    for index, line in enumerate(lines):
        # The Python app wrote `/usr/bin/python3 <quoted run.py> --daemon`.
        if line.startswith("Exec=") and quote_exec_argument(str(old_app / "run.py")) in line:
            lines[index] = f"Exec={quote_exec_argument(str(launcher))} --daemon\n"
            changed = True
    if changed and not path.is_symlink():
        _replace_file(path, path, 0o644, "".join(lines))
    return changed


def install(prefix: Path, binary: Path) -> Path:
    launcher = prefix / "bin" / "jumpkut"
    desktop = prefix / "share" / "applications" / "jumpkut.desktop"
    icon = prefix / "share" / "icons" / "hicolor" / "scalable" / "apps" / "jumpkut.svg"
    data = prefix / "share" / "jumpkut"
    old_app = data / "app"
    _refuse_links(launcher, desktop, icon)
    if old_app.is_symlink():
        raise RuntimeError(f"Refusing to remove a symbolic link: {old_app}")

    # The binary replaces the old shell launcher in one rename.
    _replace_file(binary, launcher, 0o755)
    _replace_file(ROOT / "assets/jumpkut.svg", icon, 0o644)
    _replace_file(desktop, desktop, 0o644, (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Jumpkut\n"
        "Comment=Clipboard history and quick selection\n"
        f"Exec={quote_exec_argument(str(launcher))} --history\n"
        "Icon=jumpkut\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "Keywords=clipboard;history;copy;paste;\n"
        "StartupWMClass=jumpkut\n"
        "StartupNotify=false\n"
        # Older KWin grants its fake-input protocol (used to paste) to the
        # executable named here; KWin 6.8 offers it to all native clients.
        "X-KDE-Wayland-Interfaces=org_kde_kwin_fake_input\n"))
    if migrate_autostart(old_app, launcher):
        print(f"Updated the startup entry {autostart_path()} to start the new binary.")
    # Remove only the previous Python application; history.json, backups and
    # everything else in share/jumpkut stay as they are.
    if old_app.is_dir():
        shutil.rmtree(old_app)
    return launcher


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and install Jumpkut for the current user")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local",
                        help="installation base directory (default: ~/.local)")
    parser.add_argument("--binary", type=Path,
                        help="install this already-built binary instead of running cargo")
    options = parser.parse_args()
    try:
        problem = session_problem()
        if problem:
            raise RuntimeError(problem)
        binary = options.binary.resolve() if options.binary else build()
        launcher = install(options.prefix.expanduser().resolve(), binary)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Installation failed: {error}", file=sys.stderr)
        return 1
    print(f"Installed Jumpkut {version()}. Start it with: {shlex.quote(str(launcher))}")
    print("You can also open Jumpkut from your application menu.")
    print("Enable Run at startup in Preferences to start it when you sign in.")
    if (os.environ.get("XDG_SESSION_TYPE") == "wayland" and os.path.exists("/dev/uinput")
            and not os.access("/dev/uinput", os.W_OK)):
        print("Note: if automatic paste ever asks for remote-control permission, see\n"
              "  https://github.com/patrick-hudson/jumpkut/blob/main/docs/packaging.md#uinput-fallback\n"
              "  for an optional udev rule (normally not needed on KDE Plasma 6.8+).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
