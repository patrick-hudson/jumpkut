"""Release version and changelog changes against real temporary project files."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("jumpkut_release", Path(__file__).parents[1] / "scripts/release.py")
release_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_module)

MANIFEST = """[package]
name = "jumpkut"
version = "1.2.3"
edition = "2024"

[dependencies]
serde = { version = "=1.0.229" }

[build-dependencies]
version-check = "0.9"
"""

LOCK = """version = 4

[[package]]
name = "jumpkut"
version = "1.2.3"
dependencies = [
 "serde",
]

[[package]]
name = "serde"
version = "1.0.229"
"""


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jumpkut-release-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest_path = self.root / "Cargo.toml"
        self.lock_path = self.root / "Cargo.lock"
        self.changelog_path = self.root / "CHANGELOG.md"
        self.changelog = ("# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n"
                          "- Start each popup at the most recent clipping.\n\n"
                          "## [1.2.3] - 2026-10-05\n\n- Previous release.\n")
        self.write(MANIFEST, LOCK, self.changelog)

    def write(self, manifest, lock, changelog):
        self.manifest_path.write_text(manifest, encoding="utf-8")
        self.lock_path.write_text(lock, encoding="utf-8")
        self.changelog_path.write_text(changelog, encoding="utf-8")

    def assert_unchanged(self, manifest=MANIFEST, lock=LOCK, changelog=None):
        self.assertEqual(self.manifest_path.read_text(), manifest)
        self.assertEqual(self.lock_path.read_text(), lock)
        self.assertEqual(self.changelog_path.read_text(), self.changelog if changelog is None else changelog)

    def test_semantic_bumps_update_manifest_lock_and_changelog(self):
        for kind, expected in (("patch", "1.2.4"), ("minor", "1.3.0"), ("major", "2.0.0")):
            with self.subTest(kind=kind):
                self.write(MANIFEST, LOCK, self.changelog)
                self.assertEqual(release_module.release(self.root, kind, date="2026-10-06"), expected)
                # Only the package's own version changes, not dependency versions.
                self.assertEqual(self.manifest_path.read_text(), MANIFEST.replace('version = "1.2.3"', f'version = "{expected}"'))
                self.assertEqual(self.lock_path.read_text(), LOCK.replace('version = "1.2.3"', f'version = "{expected}"'))
                result = self.changelog_path.read_text()
                self.assertIn(f"## [Unreleased]\n\n## [{expected}] - 2026-10-06\n\n### Fixed", result)
                self.assertEqual(result.count("Start each popup"), 1)
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

    def test_inconsistent_versions_do_not_change_files(self):
        cases = (
            (MANIFEST.replace("1.2.3", "1.2.dev"), LOCK, "numeric X.Y.Z"),
            (MANIFEST, LOCK.replace('name = "jumpkut"\nversion = "1.2.3"', 'name = "jumpkut"\nversion = "1.2.2"'), "Cargo.lock"),
        )
        for manifest, lock, message in cases:
            with self.subTest(message=message):
                self.write(manifest, lock, self.changelog)
                with self.assertRaisesRegex(ValueError, message):
                    release_module.release(self.root, "patch")
                self.assert_unchanged(manifest=manifest, lock=lock)
        self.write(MANIFEST, LOCK, self.changelog.replace("[1.2.3]", "[1.2.2]"))
        with self.assertRaisesRegex(ValueError, "current release"):
            release_module.release(self.root, "patch")

    def test_failed_write_restores_earlier_files(self):
        replace = release_module.os.replace

        def reject_lock(source, destination):
            if destination == self.lock_path:
                raise PermissionError("Cannot replace Cargo.lock")
            replace(source, destination)

        with patch.object(release_module.os, "replace", side_effect=reject_lock):
            with self.assertRaises(PermissionError):
                release_module.release(self.root, "patch")
        self.assert_unchanged()
        self.assertEqual(list(self.root.glob(".*")), [])

    def test_repository_files_are_consistent(self):
        root = Path(__file__).resolve().parents[1]
        manifest = (root / "Cargo.toml").read_text(encoding="utf-8")
        version = release_module.package_version(manifest).group(1)
        self.assertIn(f'name = "jumpkut"\nversion = "{version}"', (root / "Cargo.lock").read_text(encoding="utf-8"))
        self.assertRegex((root / "CHANGELOG.md").read_text(encoding="utf-8"), rf"(?m)^## \[{version}\]")


if __name__ == "__main__":
    unittest.main()
