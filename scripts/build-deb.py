#!/usr/bin/python3
"""Build a Debian/Ubuntu/Mint package without installing it or requiring root."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

from package_common import HOMEPAGE, application_version, stage_payload


def build(output_dir: Path) -> Path:
    if not shutil.which("dpkg-deb"):
        raise RuntimeError("dpkg-deb is required (on Debian/Ubuntu: sudo apt install dpkg)")
    version = application_version()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"jumpkut_{version}_all.deb"
    with tempfile.TemporaryDirectory(prefix="jumpkut-deb-") as temporary:
        staging = Path(temporary) / "package"
        stage_payload(staging)
        files = sorted(path for path in staging.rglob("*") if path.is_file())
        installed_size = (sum(path.stat().st_size for path in files) + 1023) // 1024
        control = staging / "DEBIAN"
        control.mkdir(mode=0o755)
        (control / "control").write_text(
            f"Package: jumpkut\nVersion: {version}\nArchitecture: all\n"
            "Maintainer: Patrick Hudson <5728403+patrick-hudson@users.noreply.github.com>\n"
            "Section: utils\nPriority: optional\n"
            f"Installed-Size: {installed_size}\nHomepage: {HOMEPAGE}\n"
            "Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, python3-xlib, librsvg2-common\n"
            "Description: Keyboard-first clipboard manager for Linux/X11\n"
            " Jumpkut keeps a local text clipboard archive, an Alt+C quick picker,\n"
            " a system tray menu, a searchable history window, and JSON backups.\n"
            " It requires an X11 session; native Wayland sessions are not supported.\n",
            encoding="utf-8")
        (control / "md5sums").write_text(
            "".join(f"{hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()}  {path.relative_to(staging).as_posix()}\n"
                    for path in files), encoding="utf-8")
        for path in control.iterdir():
            path.chmod(0o644)
        artifact = Path(temporary) / target.name
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build",
                        str(staging), str(artifact)], check=True)
        shutil.copyfile(artifact, target)
        target.chmod(0o644)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"),
                        help="artifact directory (default: dist)")
    options = parser.parse_args()
    try:
        artifact = build(options.output_dir)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Debian package build failed: {error}\n")
    print(artifact)


if __name__ == "__main__":
    main()
