#!/usr/bin/env python3
"""Release the pending changelog notes and bump Jumpkut's canonical version.

The version lives in Cargo.toml (`[package] version`); Cargo.lock's entry for
the jumpkut package is updated to match, without network access.
"""

import argparse
from datetime import date as calendar_date
import os
from pathlib import Path
import re
import stat
import tempfile


VERSION = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"


def _write_atomic(path: Path, text: str):
    temporary = None
    original = path.stat()
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), stat.S_IMODE(original.st_mode))
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def package_version(manifest: str) -> "_Shifted":
    """The `version = "X.Y.Z"` match inside Cargo.toml's [package] table."""
    table = re.search(r"(?ms)^\[package\][ \t]*\n(.*?)(?=^\[|\Z)", manifest)
    if table is None:
        raise ValueError("Cargo.toml must contain a [package] table")
    versions = list(re.finditer(rf'(?m)^version[ \t]*=[ \t]*"({VERSION})"[ \t]*$', table.group(1)))
    if len(versions) != 1:
        raise ValueError("Cargo.toml [package] must contain one numeric X.Y.Z version")
    return _Shifted(versions[0], table.start(1))


class _Shifted:
    """A regex match whose positions are shifted into the whole file."""

    def __init__(self, match: re.Match, offset: int):
        self._match, self._offset = match, offset

    def group(self, index=0):
        return self._match.group(index)

    def start(self, index=0):
        return self._match.start(index) + self._offset

    def end(self, index=0):
        return self._match.end(index) + self._offset


def release(root: Path, kind: str, date: str | None = None) -> str:
    """Bump patch/minor/major and move Unreleased notes into the dated release."""
    if kind not in {"patch", "minor", "major"}:
        raise ValueError("release kind must be patch, minor, or major")
    release_date = calendar_date.fromisoformat(date) if date else calendar_date.today()
    manifest_path = root / "Cargo.toml"
    lock_path = root / "Cargo.lock"
    changelog_path = root / "CHANGELOG.md"
    manifest = manifest_path.read_text(encoding="utf-8")
    lock = lock_path.read_text(encoding="utf-8")
    changelog = changelog_path.read_text(encoding="utf-8")
    name = re.search(r'(?ms)^\[package\][ \t]*\n.*?^name[ \t]*=[ \t]*"([^"]+)"', manifest)
    if name is None:
        raise ValueError("Cargo.toml [package] must have a name")
    assignment = package_version(manifest)
    current = assignment.group(1)
    locked = list(re.finditer(
        rf'(?m)^name = "{re.escape(name.group(1))}"\nversion = "({VERSION})"$', lock))
    if len(locked) != 1 or locked[0].group(1) != current:
        raise ValueError("Cargo.lock must list the package once, at the Cargo.toml version")
    headings = re.findall(rf"(?m)^## \[({VERSION})\](?: - \d{{4}}-\d{{2}}-\d{{2}})?[ \t]*$", changelog)
    released = [heading[0] for heading in headings]
    if len(released) != len(set(released)) or current not in released:
        raise ValueError("CHANGELOG.md must contain the current release without duplicate versions")
    pending = list(re.finditer(r"(?m)^## \[Unreleased\][ \t]*$", changelog))
    if len(pending) != 1:
        raise ValueError("CHANGELOG.md must contain exactly one Unreleased heading")
    start = pending[0].end()
    next_heading = re.search(r"(?m)^## ", changelog[start:])
    end = start + next_heading.start() if next_heading else len(changelog)
    notes = changelog[start:end].strip()
    meaningful = re.sub(r"<!--[\s\S]*?-->", "", notes)
    if not re.sub(r"(?m)^\s*#+[^\n]*", "", meaningful).strip():
        raise ValueError("add release notes under Unreleased before releasing")
    parts = list(map(int, current.split(".")))
    index = {"major": 0, "minor": 1, "patch": 2}[kind]
    parts[index] += 1
    parts[index + 1:] = [0] * (2 - index)
    version = ".".join(map(str, parts))
    if version in released:
        raise ValueError(f"CHANGELOG.md already contains release {version}")
    new_manifest = manifest[:assignment.start(1)] + version + manifest[assignment.end(1):]
    new_lock = lock[:locked[0].start(1)] + version + lock[locked[0].end(1):]
    new_changelog = (changelog[:start] + f"\n\n## [{version}] - {release_date.isoformat()}\n\n"
                     + notes + "\n\n" + changelog[end:].lstrip("\n"))
    written = []
    try:
        for path, text in ((changelog_path, new_changelog), (manifest_path, new_manifest), (lock_path, new_lock)):
            _write_atomic(path, text)
            written.append(path)
    except OSError:
        originals = {changelog_path: changelog, manifest_path: manifest, lock_path: lock}
        for path in written:
            _write_atomic(path, originals[path])
        raise
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("patch", "minor", "major"))
    options = parser.parse_args()
    try:
        version = release(Path(__file__).resolve().parent.parent, options.kind)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Release failed: {error}\n")
    print(f"Released Jumpkut {version}.")


if __name__ == "__main__":
    main()
