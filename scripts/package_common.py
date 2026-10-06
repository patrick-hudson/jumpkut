"""Stage the shared, architecture-independent native package payload."""

from __future__ import annotations

import ast
import gzip
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[1]
HOMEPAGE = "https://github.com/patrick-hudson/jumpkut"


def application_version(source: Path = ROOT) -> str:
    # Read the sole source of truth without importing desktop dependencies or
    # consulting a possibly stale Python bytecode cache after a release bump.
    tree = ast.parse((source / "jumpkut/__init__.py").read_text(encoding="utf-8"))
    values = [node.value for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "__version__"
                      for target in node.targets)]
    if len(values) != 1:
        raise ValueError("jumpkut/__init__.py must define one application version")
    version = ast.literal_eval(values[0])
    if not isinstance(version, str) or not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ValueError("application version must be a numeric X.Y.Z string")
    return version


def stage_payload(destination: Path, source: Path = ROOT) -> None:
    """Copy application files only; installation never touches any XDG data."""
    application = destination / "usr/share/jumpkut/app"
    application.mkdir(parents=True)
    shutil.copytree(source / "jumpkut", application / "jumpkut",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    shutil.copyfile(source / "run.py", application / "run.py")
    for name in ("LICENSE", "CHANGELOG.md"):
        shutil.copyfile(source / name, application / name)

    launcher = destination / "usr/bin/jumpkut"
    launcher.parent.mkdir(parents=True)
    # Resolving the path relative to the launcher also lets package tests run
    # an extracted archive without installing files into the host's /usr.
    launcher.write_text(
        '#!/bin/sh\n'
        'application_dir="$(CDPATH= cd -- "$(dirname -- "$0")/../share/jumpkut/app" && pwd)" || exit 1\n'
        'exec /usr/bin/python3 "$application_dir/run.py" "$@"\n', encoding="utf-8")

    desktop = destination / "usr/share/applications/jumpkut.desktop"
    desktop.parent.mkdir(parents=True)
    shutil.copyfile(source / "packaging/jumpkut.desktop", desktop)
    icon = destination / "usr/share/icons/hicolor/scalable/apps/jumpkut.svg"
    icon.parent.mkdir(parents=True)
    shutil.copyfile(source / "jumpkut/assets/jumpkut.svg", icon)
    docs = destination / "usr/share/doc/jumpkut"
    docs.mkdir(parents=True)
    shutil.copyfile(source / "LICENSE", docs / "copyright")
    with (docs / "changelog.gz").open("wb") as output:
        with gzip.GzipFile(fileobj=output, filename="", mode="wb", mtime=0) as compressed:
            compressed.write((source / "CHANGELOG.md").read_bytes())

    # Packages have conventional modes independent of a developer's umask.
    for path in destination.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    destination.chmod(0o755)
    launcher.chmod(0o755)
