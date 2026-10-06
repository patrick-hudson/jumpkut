"""Real user installs, command launch, and application-menu registration."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from gi.repository import Gio, GLib

from install import install
from jumpkut import __version__


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jumpkut-install-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prefix = self.root / "apps with spaces '$HOME `literal` \"quote\" back\\slash %f"
        self.launcher = install(self.prefix)
        self.application = self.prefix / "share/jumpkut/app"
        self.desktop = self.prefix / "share/applications/jumpkut.desktop"
        self.other_directory = self.root / "unrelated directory"
        self.other_directory.mkdir()

    def test_custom_prefix_command_and_scissors_icon(self):
        environment = dict(os.environ)
        environment.pop("DISPLAY", None)
        environment.pop("WAYLAND_DISPLAY", None)
        result = subprocess.run(
            [str(self.launcher), "--version"], cwd=self.other_directory,
            env=environment, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"Jumpkut {__version__}")
        self.assertTrue(os.access(self.launcher, os.X_OK))
        source = Path(__file__).resolve().parents[1] / "jumpkut/assets/jumpkut.svg"
        icon = self.prefix / "share/icons/hicolor/scalable/apps/jumpkut.svg"
        self.assertEqual(icon.read_bytes(), source.read_bytes())

    def test_desktop_menu_launch_preserves_special_characters_and_opens_full_history(self):
        entry = GLib.KeyFile()
        entry.load_from_file(str(self.desktop), GLib.KeyFileFlags.NONE)
        self.assertEqual(entry.get_string("Desktop Entry", "Type"), "Application")
        self.assertEqual(entry.get_string("Desktop Entry", "Name"), "Jumpkut")
        self.assertEqual(entry.get_string("Desktop Entry", "Icon"), "jumpkut")
        self.assertFalse(entry.get_boolean("Desktop Entry", "Terminal"))

        # Let the desktop library parse and execute the generated entry. The
        # capture launcher avoids needing a display and records the requested
        # action, while retaining the real installation's difficult pathname.
        captured = self.root / "desktop-arguments.json"
        self.launcher.write_text(
            "#!/usr/bin/python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            f"Path({str(captured)!r}).write_text(json.dumps(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        application = Gio.DesktopAppInfo.new_from_filename(str(self.desktop))
        self.assertIsNotNone(application)
        self.assertTrue(application.launch([], None))
        deadline = time.monotonic() + 5
        while not captured.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(captured.exists(), "the application menu did not launch the installed command")
        self.assertEqual(json.loads(captured.read_text()), ["--history"])

    def test_reinstall_refreshes_application_and_preserves_history_and_backups(self):
        history = self.application.parent / "history.json"
        history.write_text('{"saved": "clipboard history"}\n', encoding="utf-8")
        backup = self.application.parent / "backups/history.json"
        backup.parent.mkdir()
        backup.write_text('{"saved": "backup"}\n', encoding="utf-8")
        version = self.application / "jumpkut/__init__.py"
        version.write_text("stale application\n", encoding="utf-8")
        source = Path(__file__).resolve().parents[1] / "jumpkut/__init__.py"

        self.assertEqual(install(self.prefix), self.launcher)
        self.assertEqual(version.read_bytes(), source.read_bytes())
        self.assertEqual(history.read_text(), '{"saved": "clipboard history"}\n')
        self.assertEqual(backup.read_text(), '{"saved": "backup"}\n')

    def test_installer_refuses_to_replace_symlink_targets(self):
        targets = (
            self.application,
            self.launcher,
            self.desktop,
            self.prefix / "share/icons/hicolor/scalable/apps/jumpkut.svg",
        )
        for index, target in enumerate(targets):
            with self.subTest(target=target):
                sentinel = self.root / f"unrelated-{index}"
                if target.is_dir():
                    sentinel.mkdir()
                    target.rename(self.root / f"original-{index}")
                else:
                    sentinel.write_text("unrelated content", encoding="utf-8")
                    target.unlink()
                target.symlink_to(sentinel)
                with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                    install(self.prefix)
                self.assertTrue(target.is_symlink())
                if sentinel.is_file():
                    self.assertEqual(sentinel.read_text(), "unrelated content")
                target.unlink()
                install(self.prefix)


if __name__ == "__main__":
    unittest.main()
