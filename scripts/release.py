#!/usr/bin/env python3
"""Release the pending changelog notes and update Jumpkut's canonical version."""

import argparse
from datetime import date as calendar_date
import os
from pathlib import Path
import re
import stat
import tempfile


VERSION = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"


def _write_atomic(path: Path, text: str, *, invalidate_bytecode=False):
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
            written = os.fstat(stream.fileno())
            # Python's timestamp caches use whole seconds and file size. Version
            # bumps can retain both, including caches outside __pycache__.
            if invalidate_bytecode and int(written.st_mtime) == int(original.st_mtime):
                os.utime(stream.fileno(), ns=(written.st_atime_ns,
                                             written.st_mtime_ns + 1_000_000_000))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def release(root: Path, kind: str, date: str | None = None) -> str:
    """Bump patch/minor/major and move Unreleased notes into the dated release."""
    if kind not in {"patch", "minor", "major"}:
        raise ValueError("release kind must be patch, minor, or major")
    release_date = calendar_date.fromisoformat(date) if date else calendar_date.today()
    version_path = root / "jumpkut" / "__init__.py"
    changelog_path = root / "CHANGELOG.md"
    source = version_path.read_text(encoding="utf-8")
    changelog = changelog_path.read_text(encoding="utf-8")
    assignment = re.search(rf'(?m)^__version__ = "({VERSION})"$', source)
    if assignment is None or len(re.findall(r"(?m)^__version__\s*=", source)) != 1:
        raise ValueError("jumpkut/__init__.py must contain one numeric X.Y.Z version")
    current = assignment.group(1)
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
    new_source = source[:assignment.start(1)] + version + source[assignment.end(1):]
    new_changelog = (changelog[:start] + f"\n\n## [{version}] - {release_date.isoformat()}\n\n"
                     + notes + "\n\n" + changelog[end:].lstrip("\n"))
    _write_atomic(changelog_path, new_changelog)
    try:
        _write_atomic(version_path, new_source, invalidate_bytecode=True)
    except OSError:
        _write_atomic(changelog_path, changelog)
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
