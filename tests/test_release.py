"""Release version and changelog changes against real temporary project files."""

import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("jumpkut_release", Path(__file__).parents[1] / "scripts/release.py")
release_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_module)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jumpkut-release-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "jumpkut").mkdir()
        self.version_path = self.root / "jumpkut/__init__.py"
        self.changelog_path = self.root / "CHANGELOG.md"
        self.source = '"""Clipboard manager."""\n\n__version__ = "1.2.3"\n'
        self.changelog = ("# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n"
                          "- Start each popup at the most recent clipping.\n\n"
                          "## [1.2.3] - 2026-10-05\n\n- Previous release.\n")
        self.version_path.write_text(self.source, encoding="utf-8")
        self.changelog_path.write_text(self.changelog, encoding="utf-8")

    def assert_unchanged(self, source=None, changelog=None):
        self.assertEqual(self.version_path.read_text(), self.source if source is None else source)
        self.assertEqual(self.changelog_path.read_text(), self.changelog if changelog is None else changelog)

    def test_semantic_bumps_roll_notes_into_a_dated_release(self):
        for kind, expected in (("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")):
            with self.subTest(kind=kind):
                self.version_path.write_text(self.source, encoding="utf-8")
                self.changelog_path.write_text(self.changelog, encoding="utf-8")
                self.assertEqual(release_module.release(self.root, kind, date="2026-10-06"), expected)
                self.assertEqual(self.version_path.read_text(), self.source.replace("1.2.3", expected))
                result = self.changelog_path.read_text()
                self.assertIn(f"## [Unreleased]\n\n## [{expected}] - 2026-10-06\n\n### Fixed", result)
                self.assertEqual(result.count("Start each popup"), 1)
                self.assertTrue(result.endswith("## [1.2.3] - 2026-10-05\n\n- Previous release.\n"))
                with self.assertRaisesRegex(ValueError, "release notes"):
                    release_module.release(self.root, "patch", date="2026-10-06")

    def test_empty_or_placeholder_notes_do_not_change_files(self):
        for notes in ("", "\n\n", "### Added\n\n<!-- Add notes here. -->"):
            with self.subTest(notes=notes):
                changelog = f"# Changelog\n\n## [Unreleased]\n{notes}\n## [1.2.3] - 2026-10-05\n"
                self.changelog_path.write_text(changelog, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "release notes"):
                    release_module.release(self.root, "patch")
                self.assert_unchanged(changelog=changelog)

    def test_malformed_version_does_not_change_files(self):
        source = self.source.replace("1.2.3", "1.2.dev")
        self.version_path.write_text(source, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "numeric X.Y.Z"):
            release_module.release(self.root, "patch")
        self.assert_unchanged(source=source)

    def test_missing_current_release_does_not_change_files(self):
        changelog = self.changelog.replace("[1.2.3]", "[1.2.2]")
        self.changelog_path.write_text(changelog, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "current release"):
            release_module.release(self.root, "patch")
        self.assert_unchanged(changelog=changelog)

    def test_duplicate_release_does_not_change_files(self):
        changelog = self.changelog + "\n## [1.2.3] - 2026-10-04\n"
        self.changelog_path.write_text(changelog, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate versions"):
            release_module.release(self.root, "patch")
        self.assert_unchanged(changelog=changelog)

    def test_failed_version_write_restores_changelog(self):
        replace = release_module.os.replace

        def reject_version(source, destination):
            if destination == self.version_path:
                raise PermissionError("Cannot replace version file")
            replace(source, destination)

        with patch.object(release_module.os, "replace", side_effect=reject_version):
            with self.assertRaises(PermissionError):
                release_module.release(self.root, "patch")
        self.assert_unchanged()
        self.assertEqual(list(self.root.glob(".*")), [])
        self.assertEqual(list((self.root / "jumpkut").glob(".*")), [])

    def test_same_second_same_size_bump_invalidates_real_python_cache(self):
        old_timestamp = 1_700_000_000_500_000_000
        fsync = release_module.os.fsync

        def same_second_write(descriptor):
            fsync(descriptor)
            # Keep actual staged files in the cached source's second, regardless
            # of how slowly the test runs; this simulates a same-second release.
            os.utime(descriptor, ns=(old_timestamp, old_timestamp))

        for external_cache in (False, True):
            with self.subTest(external_cache=external_cache):
                self.version_path.write_text(self.source, encoding="utf-8")
                self.changelog_path.write_text(self.changelog, encoding="utf-8")
                os.utime(self.version_path, ns=(old_timestamp, old_timestamp))
                environment = dict(os.environ)
                environment.pop("PYTHONDONTWRITEBYTECODE", None)
                environment.pop("PYTHONPYCACHEPREFIX", None)
                if external_cache:
                    environment["PYTHONPYCACHEPREFIX"] = str(self.root / "external-cache")

                def imported_version():
                    return subprocess.check_output(
                        [sys.executable, "-c", "import jumpkut; print(jumpkut.__version__); print(jumpkut.__cached__)"],
                        cwd=self.root, env=environment, text=True, timeout=10,
                    ).splitlines()

                version, cache_path = imported_version()
                self.assertEqual(version, "1.2.3")
                cached_timestamp, cached_size = struct.unpack("<II", Path(cache_path).read_bytes()[8:16])
                self.assertEqual(cached_timestamp, old_timestamp // 1_000_000_000)
                self.assertEqual(cached_size, self.version_path.stat().st_size)
                with patch.object(release_module.os, "fsync", side_effect=same_second_write):
                    release_module.release(self.root, "patch", date="2026-10-06")
                self.assertEqual(self.version_path.stat().st_size, len(self.source.encode("utf-8")))
                self.assertNotEqual(int(self.version_path.stat().st_mtime), old_timestamp // 1_000_000_000)
                self.assertEqual(imported_version()[0], "1.2.4")


if __name__ == "__main__":
    unittest.main()
