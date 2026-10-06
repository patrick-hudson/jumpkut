import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jumpkut.history import History


class HistoryTests(unittest.TestCase):
    def test_ignores_blank_text_and_preserves_content(self):
        history = History(None)
        self.assertIsNone(history.add(""))
        self.assertIsNone(history.add(" \t\n"))
        content = "  café ☕\n第二行\t  "
        clip = history.add(content)
        self.assertEqual(clip.text, content)
        self.assertEqual(history.items, [clip])

    def test_duplicate_moves_to_front_without_changing_id(self):
        history = History(None)
        first = history.add("first")
        second = history.add("second")
        duplicate = history.add("first")
        self.assertEqual(duplicate.id, first.id)
        self.assertGreaterEqual(duplicate.created_at, first.created_at)
        self.assertEqual([clip.id for clip in history.items], [first.id, second.id])

    def test_exact_text_deduplication(self):
        history = History(None)
        history.add("text")
        history.add(" text ")
        self.assertEqual([clip.text for clip in history.items], [" text ", "text"])
        history.add("third")
        self.assertEqual([clip.text for clip in history.items], ["third", " text ", "text"])

    def test_items_and_clips_cannot_modify_history(self):
        history = History(None)
        clip = history.add("first")
        history.items.clear()
        self.assertEqual(history.items, [clip])
        with self.assertRaises(AttributeError):
            clip.text = "changed"

    def test_recent_snapshot_leaves_older_archive_entries_intact(self):
        history = History(None)
        for text in ("oldest", "middle", "newest"):
            history.add(text)
        recent = history.recent(2)
        self.assertEqual([clip.text for clip in recent], ["newest", "middle"])
        recent.clear()
        self.assertEqual(len(history), 3)
        self.assertEqual([clip.text for clip in history.items], ["newest", "middle", "oldest"])
        self.assertEqual(history.recent(10), history.items)

    def test_get_remove_and_clear(self):
        history = History(None)
        first = history.add("first")
        second = history.add("second")
        self.assertEqual(history.get(first.id), first)
        self.assertIsNone(history.get("missing"))
        history.remove(first.id)
        history.remove("missing")
        self.assertIsNone(history.get(first.id))
        self.assertEqual(history.items, [second])
        history.clear()
        self.assertEqual(history.items, [])

    def test_round_trip_preserves_whitespace_unicode_id_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "history.json"
            history = History(path)
            history.add("  café ☕\n第二行\t  ")
            history.add("second")
            reloaded = History(path)
            self.assertIsNone(reloaded.load_error)
            self.assertEqual(reloaded.items, history.items)

    def test_remove_and_clear_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            history = History(path)
            clip = history.add("private content")
            history.add("keep")
            history.remove(clip.id)
            self.assertEqual([clip.text for clip in History(path).items], ["keep"])
            history.clear()
            self.assertEqual(History(path).items, [])

    def test_archive_preserves_more_than_200_clips_when_added_loaded_and_backed_up(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            backup = Path(directory) / "backup.json"
            restored_path = Path(directory) / "restored.json"
            history = History(None)
            expected = [history.add(f"clipping {number}") for number in range(205)][::-1]
            with self.subTest(operation="add"):
                self.assertEqual(history.items, expected)

            # A complete existing archive also exercises loading and importing
            # independently of whether adding clips retained them all.
            path.write_text(json.dumps({"version": 1, "clips": [
                {"id": clip.id, "text": clip.text, "created_at": clip.created_at}
                for clip in expected
            ]}), encoding="utf-8")
            loaded = History(path)
            with self.subTest(operation="load"):
                self.assertEqual(loaded.items, expected)
            loaded.export_to(backup)
            with self.subTest(operation="export"):
                self.assertEqual(len(json.loads(backup.read_text())["clips"]), 205)

            restored = History(restored_path)
            with self.subTest(operation="import"):
                self.assertEqual(restored.import_from(path), 205)
                self.assertEqual(restored.items, expected)
                self.assertEqual(History(restored_path).items, expected)

    def test_export_import_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / "backup" / "clipboard.json"
            history = History(None)
            history.add("  café ☕\n第二行\t  ")
            history.add("second")
            history.export_to(backup)
            restored = History(None)
            self.assertEqual(restored.import_from(backup), 2)
            self.assertEqual(restored.items, history.items)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

    def test_import_merges_recent_copies_and_reports_all_new_texts(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "backup.json"
            history = History(None)
            existing = history.add("existing")
            older = existing.created_at - 10
            source.write_text(json.dumps({"version": 1, "clips": [
                {"id": "foreign-existing", "text": "existing", "created_at": older + 20},
                {"id": "new", "text": "new", "created_at": older + 30},
                {"id": "too-old", "text": "too old", "created_at": older},
            ]}), encoding="utf-8")
            self.assertEqual(history.import_from(source), 2)
            self.assertEqual([clip.text for clip in history.items], ["new", "existing", "too old"])
            self.assertEqual(history.items[1].id, existing.id)
            self.assertEqual(history.items[1].created_at, older + 20)
            self.assertEqual(history.import_from(source), 0)

    def test_import_handles_foreign_id_collision_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            source = Path(directory) / "backup.json"
            history = History(path)
            existing = history.add("existing")
            source.write_text(json.dumps({"version": 1, "clips": [
                {"id": existing.id, "text": "foreign", "created_at": existing.created_at + 1},
            ]}), encoding="utf-8")
            self.assertEqual(history.import_from(source), 1)
            self.assertEqual(len({clip.id for clip in history.items}), 2)
            self.assertEqual(history.get(existing.id).text, "existing")
            self.assertEqual(History(path).items, history.items)

    def test_invalid_import_does_not_touch_history_or_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "backup.json"
            invalid = b'{"version": 99, "clips": []}'
            source.write_bytes(invalid)
            history = History(None)
            history.add("existing")
            before = history.items
            with self.assertRaises(ValueError):
                history.import_from(source)
            self.assertEqual(history.items, before)
            self.assertEqual(source.read_bytes(), invalid)
            self.assertEqual(list(Path(directory).glob("*.corrupt-*")), [])

    def test_failed_atomic_write_preserves_old_file_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            history = History(path)
            history.add("saved")
            saved = path.read_bytes()
            with patch("jumpkut.history.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    history.add("in memory")
            self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])
            self.assertEqual(history.items[0].text, "in memory")

    @unittest.skipUnless(os.name == "posix", "Unix permission modes")
    def test_export_does_not_change_existing_user_directory_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "downloads"
            parent.mkdir(mode=0o755)
            history = History(None)
            history.add("secret")
            history.export_to(parent / "backup.json")
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((parent / "backup.json").stat().st_mode), 0o600)

    def test_corrupt_data_is_preserved_before_new_history_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            bad_bytes = b'{"clips": broken\xff}'
            path.write_bytes(bad_bytes)
            history = History(path)
            self.assertEqual(history.items, [])
            self.assertIsNotNone(history.load_error)
            backups = list(Path(directory).glob("history.json.corrupt-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), bad_bytes)
            history.add("recovery")
            self.assertEqual(History(path).items, history.items)
            self.assertEqual(backups[0].read_bytes(), bad_bytes)

    def test_invalid_schema_is_preserved(self):
        invalid_documents = [
            [],
            {"version": 99, "clips": []},
            {"version": 1, "clips": [{"id": "one", "text": "okay"}]},
            {"version": 1, "clips": [{"id": "one", "text": "  ", "created_at": 1}]},
            {"version": 1, "clips": [{"id": "one", "text": "okay", "created_at": "yesterday"}]},
            {"version": 1, "clips": [{"id": "one", "text": "okay", "created_at": float("nan")}]},
            {"version": 1, "clips": [
                {"id": "one", "text": "a", "created_at": 1},
                {"id": "one", "text": "b", "created_at": 2},
            ]},
        ]
        for data in invalid_documents:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "history.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                history = History(path)
                self.assertEqual(history.items, [])
                self.assertIsNotNone(history.load_error)
                self.assertEqual(len(list(Path(directory).glob("history.json.corrupt-*"))), 1)

    @unittest.skipUnless(os.name == "posix", "Unix permission modes")
    def test_storage_and_corrupt_backup_have_private_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "private"
            path = parent / "history.json"
            history = History(path)
            history.add("secret")
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            parent.chmod(0o755)
            path.chmod(0o644)
            history.add("second")
            self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            path.write_text("broken", encoding="utf-8")
            History(path)
            backup = next(parent.glob("history.json.corrupt-*"))
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
