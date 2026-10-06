"""Clipboard archive with private, atomic JSON persistence."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Clip:
    id: str
    text: str
    created_at: float


class History:
    """Keep newest copies first; ``path=None`` disables disk storage.

    A repeated copy retains its id and receives a new timestamp. Failed writes
    raise ``OSError`` while retaining the current history in memory. Invalid
    stored data is preserved before a subsequent write can replace it.
    """

    def __init__(self, path: Path | None):
        self.path = Path(path) if path is not None else None
        self.load_error: str | None = None
        self._items: list[Clip] = []
        self._unsafe_to_overwrite = False
        if self.path is not None:
            self._load()

    @property
    def items(self) -> list[Clip]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def recent(self, limit: int) -> list[Clip]:
        """Return a snapshot of the newest clippings without trimming the archive."""
        return self._items[:limit]

    def add(self, text: str) -> Clip | None:
        if not text.strip():
            return None
        previous = next((clip for clip in self._items if clip.text == text), None)
        clip = Clip(previous.id if previous else uuid.uuid4().hex, text, time.time())
        self._items = [clip, *(item for item in self._items if item.text != text)]
        self._save()
        return clip

    def get(self, clip_id: str) -> Clip | None:
        return next((clip for clip in self._items if clip.id == clip_id), None)

    def remove(self, clip_id: str) -> None:
        remaining = [clip for clip in self._items if clip.id != clip_id]
        if len(remaining) != len(self._items):
            self._items = remaining
            self._save()

    def clear(self) -> None:
        self._items.clear()
        self._save()

    def export_to(self, path: Path) -> None:
        """Write a portable backup without changing an existing directory mode."""
        self._write(Path(path), self._items, private_parent=False)

    def import_from(self, path: Path) -> int:
        """Merge a backup and return the number of new texts added to the archive.

        Parse the whole backup before touching current history. For an existing
        text, retain its local id and the latest copy's timestamp.
        """
        imported = self._decode(Path(path).read_bytes())
        existing_texts = {clip.text for clip in self._items}
        merged = {clip.text: clip for clip in self._items}
        used_ids = {clip.id for clip in self._items}
        for clip in imported:
            previous = merged.get(clip.text)
            if previous is not None:
                if clip.created_at > previous.created_at:
                    merged[clip.text] = Clip(previous.id, clip.text, clip.created_at)
            else:
                clip_id = clip.id
                while clip_id in used_ids:
                    clip_id = uuid.uuid4().hex
                merged[clip.text] = Clip(clip_id, clip.text, clip.created_at)
                used_ids.add(clip_id)
        self._items = sorted(merged.values(), key=lambda clip: clip.created_at, reverse=True)
        self._save()
        return sum(clip.text not in existing_texts for clip in self._items)

    def _load(self) -> None:
        assert self.path is not None
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as error:
            self.load_error = f"Could not read clipboard history: {error}"
            self._unsafe_to_overwrite = True
            return

        try:
            self._items = self._decode(raw)
        except (ValueError, UnicodeError) as error:
            self.load_error = f"Invalid clipboard history: {error}"
            try:
                backup = self._preserve_corrupt(raw)
            except OSError as backup_error:
                self.load_error += f"; could not preserve original: {backup_error}"
                self._unsafe_to_overwrite = True
            else:
                self.load_error += f"; original preserved at {backup}"

    @staticmethod
    def _decode(raw: bytes) -> list[Clip]:
        data = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(data, dict)
            or type(data.get("version")) is not int
            or data["version"] != 1
            or not isinstance(data.get("clips"), list)
        ):
            raise ValueError("unrecognized history format")

        items: list[Clip] = []
        ids: set[str] = set()
        texts: set[str] = set()
        for entry in data["clips"]:
            if not isinstance(entry, dict):
                raise ValueError("invalid clipboard entry")
            clip_id, text, created_at = (
                entry.get("id"), entry.get("text"), entry.get("created_at")
            )
            if (
                not isinstance(clip_id, str)
                or not clip_id.strip()
                or clip_id in ids
                or not isinstance(text, str)
                or not text.strip()
                or text in texts
                or type(created_at) not in (int, float)
            ):
                raise ValueError("invalid or duplicate clipboard entry")
            try:
                timestamp = float(created_at)
            except OverflowError as error:
                raise ValueError("invalid clipboard timestamp") from error
            if not math.isfinite(timestamp):
                raise ValueError("invalid clipboard timestamp")
            ids.add(clip_id)
            texts.add(text)
            items.append(Clip(clip_id, text, timestamp))
        return items

    @staticmethod
    def _prepare_parent(path: Path, *, private_parent: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if private_parent:
            path.parent.chmod(0o700)

    def _preserve_corrupt(self, raw: bytes) -> Path:
        assert self.path is not None
        self._prepare_parent(self.path)
        backup = self.path.with_name(f"{self.path.name}.corrupt-{time.time_ns()}")
        # Exclusive creation avoids overwriting an earlier recovery copy.
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
        return backup

    def _save(self) -> None:
        if self.path is None:
            return
        if self._unsafe_to_overwrite:
            raise OSError("Clipboard history could not be read or backed up; original will be preserved")
        self._write(self.path, self._items)

    @classmethod
    def _write(cls, path: Path, items: list[Clip], *, private_parent: bool = True) -> None:
        cls._prepare_parent(path, private_parent=private_parent)
        content = {"version": 1, "clips": [asdict(clip) for clip in items]}
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                os.fchmod(output.fileno(), 0o600)
                json.dump(content, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
