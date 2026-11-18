"""Stage the shared native package payload (binary, desktop entry, icon, docs)."""

from __future__ import annotations

import gzip
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[1]
HOMEPAGE = "https://github.com/patrick-hudson/jumpkut"
DEFAULT_BINARY = ROOT / "target/release/jumpkut"
SUMMARY = "Keyboard-first clipboard manager for Linux"
DESCRIPTION = (
    "Jumpkut keeps a local text clipboard archive, an Alt+C quick picker,\n"
    "a system tray menu, a searchable history window, and JSON backups.\n"
    "It runs on X11 sessions and on KDE Plasma Wayland (with wl-clipboard).\n"
)

# Libraries the binary loads at run time (dlopen), so package tools cannot
# detect them: OpenGL/EGL, Wayland, X11, xkbcommon, fontconfig.
DEB_DEPENDS = (
    "libgcc-s1, libegl1, libgl1, libfontconfig1, libwayland-client0, "
    "libwayland-egl1, libxkbcommon0, libxkbcommon-x11-0, libx11-6, libx11-xcb1, "
    "libxcursor1, libxi6, libxrender1"
)
RPM_REQUIRES = (
    "libglvnd-egl", "libglvnd-glx", "fontconfig", "libwayland-client", "libwayland-egl",
    "libxkbcommon", "libxkbcommon-x11", "libX11", "libX11-xcb", "libXcursor", "libXi", "libXrender",
)
WL_CLIPBOARD = "wl-clipboard (>= 2.3.0)"

UINPUT_RULE = """\
# Optional: lets the signed-in user's Jumpkut paste through a /dev/uinput
# keyboard when KWin's fake-input protocol is unavailable (older KWin, or the
# AppImage on KWin versions that check desktop files). Not needed on KDE
# Plasma 6.8+. To enable, as root:
#   install -m 644 70-jumpkut-uinput.rules /etc/udev/rules.d/
#   echo uinput > /etc/modules-load.d/jumpkut-uinput.conf
#   modprobe uinput && udevadm control --reload && udevadm trigger --name-match=uinput
KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"
"""


def application_version(source: Path = ROOT) -> str:
    """The `[package] version` from Cargo.toml (the single source of truth)."""
    manifest = (source / "Cargo.toml").read_text(encoding="utf-8")
    table = re.search(r"(?ms)^\[package\][ \t]*\n(.*?)(?=^\[|\Z)", manifest)
    versions = re.findall(r'(?m)^version\s*=\s*"([^"]*)"', table.group(1)) if table else []
    if len(versions) != 1 or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", versions[0]):
        raise ValueError("Cargo.toml [package] must contain one numeric X.Y.Z version")
    return versions[0]


def glibc_requirement(binary: Path) -> str:
    """The newest GLIBC symbol version the binary needs (e.g. "2.35")."""
    import subprocess
    try:
        symbols = subprocess.run(["objdump", "-T", str(binary)], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError(f"objdump (binutils) is needed to read the glibc requirement: {error}") from error
    versions = {tuple(map(int, v.split("."))) for v in re.findall(r"GLIBC_(\d+(?:\.\d+)+)", symbols)}
    return ".".join(map(str, max(versions))) if versions else "2.17"


def require_binary(binary: Path) -> Path:
    binary = binary.expanduser().resolve()
    if not binary.is_file():
        raise RuntimeError(f"{binary} not found; build it first with: cargo build --release --locked")
    return binary


def stage_payload(destination: Path, binary: Path = DEFAULT_BINARY, source: Path = ROOT) -> None:
    """Copy application files only; installation never touches any XDG data."""
    command = destination / "usr/bin/jumpkut"
    command.parent.mkdir(parents=True)
    shutil.copyfile(require_binary(binary), command)

    desktop = destination / "usr/share/applications/jumpkut.desktop"
    desktop.parent.mkdir(parents=True)
    shutil.copyfile(source / "packaging/jumpkut.desktop", desktop)
    icon = destination / "usr/share/icons/hicolor/scalable/apps/jumpkut.svg"
    icon.parent.mkdir(parents=True)
    shutil.copyfile(source / "assets/jumpkut.svg", icon)
    docs = destination / "usr/share/doc/jumpkut"
    docs.mkdir(parents=True)
    shutil.copyfile(source / "LICENSE", docs / "copyright")
    (docs / "70-jumpkut-uinput.rules").write_text(UINPUT_RULE, encoding="utf-8")
    with (docs / "changelog.gz").open("wb") as output:
        with gzip.GzipFile(fileobj=output, filename="", mode="wb", mtime=0) as compressed:
            compressed.write((source / "CHANGELOG.md").read_bytes())

    # Packages have conventional modes independent of a developer's umask.
    for path in destination.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    destination.chmod(0o755)
    command.chmod(0o755)
