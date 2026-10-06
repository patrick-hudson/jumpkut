#!/usr/bin/python3
"""Copy relocatable Python payloads and retain Debian runtime license notices."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig


def copy_payload(project: Path, appdir: Path) -> None:
    stdlib = Path(sysconfig.get_path("stdlib"))
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "test", "tests", "dist-packages", "site-packages")
    shutil.copytree(stdlib, appdir / "usr/lib" / stdlib.name, ignore=ignore)
    modules = appdir / "usr/lib/python3/dist-packages"
    for name in ("gi", "Xlib"):
        spec = importlib.util.find_spec(name)
        if spec is None or spec.origin is None:
            raise RuntimeError(f"Missing Python dependency: {name}")
        shutil.copytree(Path(spec.origin).parent, modules / name, ignore=ignore)
    # python-xlib requires six on Ubuntu 22.04 and Debian.
    six = importlib.util.find_spec("six")
    if six and six.origin:
        shutil.copy2(six.origin, modules / "six.py")
    shutil.copytree(project / "jumpkut", appdir / "usr/share/jumpkut/jumpkut", ignore=ignore)


def copy_licenses(project: Path, appdir: Path) -> None:
    """Match Debian file ownership, including linuxdeploy's flattened libraries.

    Match exact installed paths for data/Python files and library basenames for
    linuxdeploy's flattened multiarch libraries. The latter may retain a few
    extra notices when more than one installed package has the same library.
    """
    files = [path for path in appdir.rglob("*") if path.is_file()]
    installed_paths = {"/" + str(path.relative_to(appdir)) for path in files}
    relocated_names = {path.name for path in files if ".so" in path.name}
    relocated_names.update(("python3", "gdk-pixbuf-query-loaders", "gtk-query-immodules-3.0"))
    packages = set()
    for listing in Path("/var/lib/dpkg/info").glob("*.list"):
        if any(
            line in installed_paths or Path(line).name in relocated_names
            for line in listing.read_text(errors="replace").splitlines()
        ):
            packages.add(listing.name.removesuffix(".list"))
    destination = appdir / "usr/share/doc/jumpkut"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(project / "LICENSE", destination / "LICENSE")
    notices = destination / "third-party"
    notices.mkdir()
    for package in sorted(packages):
        copyright_file = Path("/usr/share/doc") / package.split(":")[0] / "copyright"
        if copyright_file.is_file():
            package_dir = notices / package
            package_dir.mkdir()
            shutil.copy2(copyright_file, package_dir / "copyright")
    shutil.copytree("/usr/share/common-licenses", notices / "common-licenses", symlinks=False)
    manifest = subprocess.check_output(
        ["dpkg-query", "-W", "-f=${Package}\t${Version}\t${source:Package}\t${source:Version}\n", *sorted(packages)],
        text=True,
    )
    (destination / "bundled-packages.tsv").write_text(
        "# Binary package\tBinary version\tSource package\tSource version\n" + manifest,
        encoding="utf-8",
    )
    (destination / "THIRD-PARTY.txt").write_text(
        "Jumpkut bundles Python, PyGObject, python-xlib, GTK3 and their runtime dependencies.\n"
        "Their license/copyright notices are in third-party/, with Debian's referenced\n"
        "license texts in third-party/common-licenses/. bundled-packages.tsv records\n"
        "the binary/source packages and versions, including some extra notices when\n"
        "file names match more than one installed package.\n\n"
        "For the official Ubuntu 22.04 build, source packages are available from\n"
        "https://archive.ubuntu.com/ubuntu/pool/ and https://launchpad.net/ubuntu/+source/ .\n"
        "Install source repositories on Ubuntu and use 'apt source PACKAGE=VERSION'\n"
        "with the source columns from bundled-packages.tsv. You may also extract the\n"
        "AppImage with --appimage-extract to replace its dynamically linked libraries.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    action, project_argument, appdir_argument = sys.argv[1:]
    {"copy": copy_payload, "licenses": copy_licenses}[action](Path(project_argument), Path(appdir_argument))
