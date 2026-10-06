"""Build real distribution packages without installing onto the host."""

import gzip
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tarfile
import unittest

from jumpkut import __version__


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb is required for package checks")
class DebianPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="jumpkut-deb-check-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.environment = dict(os.environ)
        cls.environment.update(
            HOME=str(cls.root / "home"),
            XDG_DATA_HOME=str(cls.root / "data"),
            XDG_CONFIG_HOME=str(cls.root / "config"),
        )
        cls.sentinels = []
        for relative in ("data/jumpkut/history.json", "config/jumpkut/settings.json"):
            sentinel = cls.root / relative
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text("saved user data\n", encoding="utf-8")
            cls.sentinels.append(sentinel)
        result = subprocess.run(
            ["/usr/bin/python3", str(ROOT / "scripts/build-deb.py"),
             "--output-dir", str(cls.root / "output with spaces")],
            cwd=cls.root, env=cls.environment, capture_output=True, text=True, timeout=60,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        cls.package = cls.root / "output with spaces" / f"jumpkut_{__version__}_all.deb"
        cls.unpacked = cls.root / "extracted"
        subprocess.run(["dpkg-deb", "--raw-extract", str(cls.package), str(cls.unpacked)],
                       check=True, capture_output=True, timeout=20)

    def field(self, name):
        return subprocess.check_output(
            ["dpkg-deb", "--field", str(self.package), name], text=True,
        ).strip()

    def test_metadata_uses_canonical_version_and_declares_desktop_dependencies(self):
        self.assertEqual(self.field("Package"), "jumpkut")
        self.assertEqual(self.field("Version"), __version__)
        self.assertEqual(self.field("Architecture"), "all")
        for dependency in ("python3 (>= 3.10)", "python3-gi", "gir1.2-gtk-3.0",
                           "python3-xlib", "librsvg2-common"):
            self.assertIn(dependency, self.field("Depends"))

    def test_relocated_launcher_works_without_display_or_source_checkout(self):
        launcher = self.unpacked / "usr/bin/jumpkut"
        environment = dict(self.environment)
        environment.pop("DISPLAY", None)
        environment.pop("WAYLAND_DISPLAY", None)
        result = subprocess.run([str(launcher), "--version"], cwd=self.root,
                                env=environment, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"Jumpkut {__version__}")
        self.assertTrue(os.access(launcher, os.X_OK))

    def test_application_menu_assets_and_license_are_present(self):
        desktop = self.unpacked / "usr/share/applications/jumpkut.desktop"
        content = desktop.read_text(encoding="utf-8")
        self.assertIn("Exec=jumpkut --history\n", content)
        self.assertIn("Icon=jumpkut\n", content)
        icon = self.unpacked / "usr/share/icons/hicolor/scalable/apps/jumpkut.svg"
        self.assertEqual(icon.read_bytes(), (ROOT / "jumpkut/assets/jumpkut.svg").read_bytes())
        self.assertEqual((self.unpacked / "usr/share/doc/jumpkut/copyright").read_bytes(),
                         (ROOT / "LICENSE").read_bytes())
        with gzip.open(self.unpacked / "usr/share/doc/jumpkut/changelog.gz", "rb") as source:
            self.assertEqual(source.read(), (ROOT / "CHANGELOG.md").read_bytes())

    def test_payload_has_no_user_data_or_installation_scriptlets(self):
        entries = {path.relative_to(self.unpacked).as_posix() for path in self.unpacked.rglob("*")}
        for forbidden in ("home", "etc", "usr/share/jumpkut/history.json"):
            self.assertFalse(any(path == forbidden or path.startswith(forbidden + "/")
                                 for path in entries), forbidden)
        self.assertFalse(any("__pycache__" in path or path.endswith(".pyc") for path in entries))
        for script in ("preinst", "postinst", "prerm", "postrm"):
            self.assertFalse((self.unpacked / "DEBIAN" / script).exists())
        for sentinel in self.sentinels:
            self.assertEqual(sentinel.read_text(), "saved user data\n")

    def test_package_files_have_root_ownership_and_standard_modes(self):
        payload = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(self.package)])
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            for member in archive.getmembers():
                self.assertEqual((member.uid, member.gid), (0, 0), member.name)
                expected_mode = 0o755 if member.isdir() or member.name == "./usr/bin/jumpkut" else 0o644
                self.assertEqual(member.mode, expected_mode, member.name)


@unittest.skipUnless(shutil.which("rpmbuild") and shutil.which("rpm"),
                     "rpmbuild and rpm are required for RPM checks")
class RpmPackageTests(unittest.TestCase):
    def test_real_build_metadata_and_file_manifest(self):
        with tempfile.TemporaryDirectory(prefix="jumpkut-rpm-check-") as temporary:
            output = Path(temporary) / "output with spaces"
            environment = dict(os.environ)
            environment["HOME"] = temporary
            result = subprocess.run(
                ["/usr/bin/python3", str(ROOT / "scripts/build-rpm.py"),
                 "--output-dir", str(output)],
                cwd=temporary, env=environment, capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            package = output / f"jumpkut-{__version__}-1.noarch.rpm"
            database = str(Path(temporary) / "rpm-db")
            subprocess.run(["rpmdb", "--dbpath", database, "--initdb"], env=environment,
                           check=True, capture_output=True, timeout=10)
            rpm = ["rpm", "--dbpath", database]
            metadata = subprocess.check_output(
                [*rpm, "-qp", "--queryformat", "%{NAME} %{VERSION} %{ARCH}", str(package)],
                env=environment, text=True,
            )
            self.assertEqual(metadata, f"jumpkut {__version__} noarch")
            requirements = subprocess.check_output([*rpm, "-qpR", str(package)], env=environment, text=True)
            for dependency in ("python3 >= 3.10", "python3-gobject", "gtk3", "python3-xlib", "librsvg2"):
                self.assertIn(dependency, requirements)
            manifest = subprocess.check_output([*rpm, "-qpl", str(package)], env=environment, text=True)
            for path in ("/usr/bin/jumpkut", "/usr/share/jumpkut/app/run.py",
                         "/usr/share/applications/jumpkut.desktop",
                         "/usr/share/icons/hicolor/scalable/apps/jumpkut.svg"):
                self.assertIn(path, manifest)
            self.assertNotIn("__pycache__", manifest)
            self.assertNotIn("/home/", manifest)
            self.assertEqual(subprocess.check_output([*rpm, "-qp", "--scripts", str(package)],
                                                    env=environment, text=True), "")
            if shutil.which("rpm2cpio") and shutil.which("cpio"):
                extracted = Path(temporary) / "extracted"
                extracted.mkdir()
                payload = subprocess.check_output(["rpm2cpio", str(package)], env=environment)
                subprocess.run(["cpio", "--extract", "--make-directories", "--no-absolute-filenames", "--quiet"],
                               input=payload, cwd=extracted, env=environment, check=True, capture_output=True)
                launched = subprocess.run([str(extracted / "usr/bin/jumpkut"), "--version"],
                                          cwd=temporary, env=environment, capture_output=True, text=True, timeout=5)
                self.assertEqual(launched.returncode, 0, launched.stderr)
                self.assertEqual(launched.stdout.strip(), f"Jumpkut {__version__}")


if __name__ == "__main__":
    unittest.main()
