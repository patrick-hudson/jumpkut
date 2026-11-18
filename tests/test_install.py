"""install.py against a temporary prefix: data survives, the Python app goes."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(os.environ.get("JUMPKUT_BINARY", ROOT / "target/release/jumpkut"))
spec = importlib.util.spec_from_file_location("jumpkut_install", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@unittest.skipUnless(BINARY.is_file(), "build first: cargo build --release")
class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="jumpkut-install-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prefix = self.root / "prefix with spaces '$HOME' %f"
        data = self.prefix / "share/jumpkut"
        # What a Python-era installation leaves behind.
        (data / "app/jumpkut").mkdir(parents=True)
        (data / "app/run.py").write_text("print('old app')\n")
        self.history = data / "history.json"
        self.history.write_text('{"version": 1, "clips": []}\n')
        self.backup = data / "backups/history.json"
        self.backup.parent.mkdir()
        self.backup.write_text('{"saved": "backup"}\n')
        launcher = self.prefix / "bin/jumpkut"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\nexec /usr/bin/python3 run.py \"$@\"\n")
        self.autostart_dir = self.root / "autostart"
        self.autostart_dir.mkdir()
        # The Python app quoted run.py with the same desktop-entry quoting.
        run_py = installer.quote_exec_argument(str(data / "app/run.py"))
        (self.autostart_dir / "jumpkut.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Jumpkut\n"
            f"Exec=/usr/bin/python3 {run_py} --daemon\nIcon=edit-paste\n")
        self.environment = dict(os.environ, JUMPKUT_AUTOSTART_DIR=str(self.autostart_dir))
        self.environment.pop("XDG_SESSION_TYPE", None)

    def run_installer(self):
        return subprocess.run(
            ["/usr/bin/python3", str(ROOT / "install.py"), "--prefix", str(self.prefix), "--binary", str(BINARY)],
            env=self.environment, capture_output=True, text=True, timeout=30)

    def test_upgrade_from_python_install_keeps_data_and_replaces_app(self):
        history_before = self.history.read_bytes()
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        launcher = self.prefix / "bin/jumpkut"
        self.assertEqual(launcher.read_bytes(), BINARY.read_bytes())
        self.assertTrue(os.access(launcher, os.X_OK))
        version = subprocess.run([str(launcher), "--version"], capture_output=True, text=True, timeout=5)
        self.assertRegex(version.stdout.strip(), r"^Jumpkut \d+\.\d+\.\d+$")
        self.assertFalse((self.prefix / "share/jumpkut/app").exists())
        self.assertEqual(self.history.read_bytes(), history_before)
        self.assertEqual(self.backup.read_text(), '{"saved": "backup"}\n')

        desktop = (self.prefix / "share/applications/jumpkut.desktop").read_text()
        self.assertIn(f"Exec={installer.quote_exec_argument(str(launcher))} --history\n", desktop)
        self.assertIn("StartupWMClass=jumpkut\n", desktop)
        self.assertIn("X-KDE-Wayland-Interfaces=org_kde_kwin_fake_input\n", desktop)
        icon = self.prefix / "share/icons/hicolor/scalable/apps/jumpkut.svg"
        self.assertEqual(icon.read_bytes(), (ROOT / "assets/jumpkut.svg").read_bytes())
        autostart = (self.autostart_dir / "jumpkut.desktop").read_text()
        self.assertNotIn("run.py", autostart)
        self.assertIn(f"Exec={installer.quote_exec_argument(str(launcher))} --daemon\n", autostart)
        self.assertIn("Icon=edit-paste\n", autostart)

        # Reinstalling is idempotent and still leaves the data alone.
        self.assertEqual(self.run_installer().returncode, 0)
        self.assertEqual(self.history.read_bytes(), history_before)

    def test_refuses_symbolic_links(self):
        sentinel = self.root / "unrelated"
        sentinel.write_text("unrelated content")
        launcher = self.prefix / "bin/jumpkut"
        launcher.unlink()
        launcher.symlink_to(sentinel)
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symbolic link", result.stderr)
        self.assertEqual(sentinel.read_text(), "unrelated content")
        self.assertTrue((self.prefix / "share/jumpkut/app").is_dir(), "nothing is removed after a refusal")


if __name__ == "__main__":
    unittest.main()
