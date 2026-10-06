#!/usr/bin/python3
"""Build a Fedora RPM package without installing it or requiring root."""

from __future__ import annotations

import argparse
import gzip
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

from package_common import HOMEPAGE, application_version, stage_payload


def build(output_dir: Path) -> Path:
    if not shutil.which("rpmbuild"):
        raise RuntimeError("rpmbuild is required (Fedora: sudo dnf install rpm-build; Ubuntu: sudo apt install rpm)")
    version = application_version()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"jumpkut-{version}-1.noarch.rpm"
    with tempfile.TemporaryDirectory(prefix="jumpkut-rpm-") as temporary:
        top = Path(temporary)
        for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS", "TMP", "DB"):
            (top / name).mkdir()
        staging = top / "payload"
        stage_payload(staging)
        source = top / "SOURCES/payload.tar.gz"
        with source.open("wb") as output:
            with gzip.GzipFile(fileobj=output, filename="", mode="wb", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for path in sorted(staging.rglob("*")):
                        info = archive.gettarinfo(str(path), arcname=path.relative_to(staging).as_posix())
                        info.uid = info.gid = info.mtime = 0
                        info.uname = info.gname = "root"
                        if path.is_file():
                            with path.open("rb") as content:
                                archive.addfile(info, content)
                        else:
                            archive.addfile(info)
        spec = top / "SPECS/jumpkut.spec"
        spec.write_text(
            # Ship source only: runtime caches belong to the Python interpreter,
            # and must not depend on the build host's minor Python version.
            "%global __brp_python_bytecompile %{nil}\n"
            "Name: jumpkut\n"
            f"Version: {version}\nRelease: 1\n"
            "Summary: Keyboard-first clipboard manager for Linux/X11\n"
            f"License: MIT\nURL: {HOMEPAGE}\n"
            "Source0: payload.tar.gz\nBuildArch: noarch\n"
            "Requires: python3 >= 3.10\nRequires: python3-gobject\nRequires: gtk3\n"
            "Requires: python3-xlib\nRequires: librsvg2\n"
            "\n%description\n"
            "Jumpkut keeps a local text clipboard archive, an Alt+C quick picker,\n"
            "a system tray menu, a searchable history window, and JSON backups.\n"
            "It requires an X11 session; native Wayland sessions are not supported.\n"
            "\n%prep\n%build\n\n%install\n"
            "mkdir -p \"%{buildroot}\"\n"
            "tar -xzf \"%{SOURCE0}\" -C \"%{buildroot}\"\n"
            "\n%files\n%defattr(-,root,root,-)\n"
            "/usr/bin/jumpkut\n/usr/share/jumpkut\n"
            "/usr/share/applications/jumpkut.desktop\n"
            "/usr/share/icons/hicolor/scalable/apps/jumpkut.svg\n"
            "%dir /usr/share/doc/jumpkut\n"
            "%doc /usr/share/doc/jumpkut/changelog.gz\n"
            "%license /usr/share/doc/jumpkut/copyright\n",
            encoding="utf-8")
        # There are no BuildRequires: the payload is already staged. Keep RPM's
        # database and shell-script temporary files inside this private build.
        subprocess.run(["rpmbuild", "-bb", "--nodeps", "--define", f"_topdir {top}",
                        "--define", f"_tmppath {top / 'TMP'}", "--define", f"_dbpath {top / 'DB'}",
                        "--define", "_buildhost jumpkut-build", "--define", "_build_id_links none",
                        str(spec)], check=True)
        artifact = top / "RPMS/noarch" / target.name
        if not artifact.is_file():
            raise RuntimeError(f"rpmbuild did not produce {target.name}")
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
        parser.exit(1, f"RPM package build failed: {error}\n")
    print(artifact)


if __name__ == "__main__":
    main()
