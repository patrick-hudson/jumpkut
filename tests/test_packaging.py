"""Build real .deb/.rpm packages from the built binary and inspect them."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(os.environ.get("JUMPKUT_BINARY", ROOT / "target/release/jumpkut"))
sys.path.insert(0, str(ROOT / "scripts"))
from package_common import application_version  # noqa: E402

VERSION = application_version()
PAYLOAD = {
    "usr/bin/jumpkut",
    "usr/share/applications/jumpkut.desktop",
    "usr/share/icons/hicolor/scalable/apps/jumpkut.svg",
    "usr/share/doc/jumpkut/copyright",
    "usr/share/doc/jumpkut/changelog.gz",
    "usr/share/doc/jumpkut/70-jumpkut-uinput.rules",
}


def build(script, output):
    result = subprocess.run(
        ["/usr/bin/python3", str(ROOT / "scripts" / script), "--output-dir", str(output), "--binary", str(BINARY)],
        capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return Path(result.stdout.strip().splitlines()[-1])


class PackageChecks:
    def check_payload(self, root: Path):
        files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(files, PAYLOAD, "only the application files; no user data, no Python app")
        command = root / "usr/bin/jumpkut"
        self.assertEqual(command.read_bytes(), BINARY.read_bytes())
        self.assertTrue(os.access(command, os.X_OK))
        environment = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        result = subprocess.run([str(command), "--version"], env=environment, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.stdout.strip(), f"Jumpkut {VERSION}")
        desktop = (root / "usr/share/applications/jumpkut.desktop").read_text()
        for line in ("Exec=jumpkut --history", "StartupWMClass=jumpkut", "X-KDE-Wayland-Interfaces=org_kde_kwin_fake_input"):
            self.assertIn(line + "\n", desktop)
        self.assertEqual((root / "usr/share/icons/hicolor/scalable/apps/jumpkut.svg").read_bytes(),
                         (ROOT / "assets/jumpkut.svg").read_bytes())


@unittest.skipUnless(BINARY.is_file() and shutil.which("dpkg-deb"), "needs the built binary and dpkg-deb")
class DebianPackageTests(unittest.TestCase, PackageChecks):
    def test_deb_metadata_and_payload(self):
        with tempfile.TemporaryDirectory(prefix="jumpkut-deb-") as temporary:
            package = build("build-deb.py", Path(temporary) / "out")
            self.assertEqual(package.name, f"jumpkut_{VERSION}_amd64.deb")

            def field(name):
                return subprocess.check_output(["dpkg-deb", "--field", str(package), name], text=True).strip()

            self.assertEqual((field("Package"), field("Version"), field("Architecture")), ("jumpkut", VERSION, "amd64"))
            for dependency in ("libc6 (>= 2.", "libegl1", "libwayland-client0", "libxkbcommon0", "libfontconfig1", "libx11-6"):
                self.assertIn(dependency, field("Depends"))
            self.assertIn("wl-clipboard (>= 2.3.0)", field("Recommends"))
            for gone in ("python3", "gtk"):
                self.assertNotIn(gone, field("Depends"))
            unpacked = Path(temporary) / "root"
            subprocess.run(["dpkg-deb", "--extract", str(package), str(unpacked)], check=True, timeout=60)
            self.check_payload(unpacked)


@unittest.skipUnless(BINARY.is_file() and shutil.which("rpmbuild") and shutil.which("rpm2cpio"),
                     "needs the built binary, rpmbuild and rpm2cpio")
class RpmPackageTests(unittest.TestCase, PackageChecks):
    def test_rpm_metadata_and_payload(self):
        with tempfile.TemporaryDirectory(prefix="jumpkut-rpm-") as temporary:
            package = build("build-rpm.py", Path(temporary) / "out")
            self.assertEqual(package.name, f"jumpkut-{VERSION}-1.x86_64.rpm")
            requires = subprocess.check_output(["rpm", "-qp", "--requires", str(package)], text=True)
            for dependency in ("libglvnd-egl", "libwayland-client", "libxkbcommon", "fontconfig", "libX11"):
                self.assertIn(dependency, requires.splitlines())
            self.assertNotIn("python3", requires)
            self.assertIn("wl-clipboard >= 2.3.0",
                          subprocess.check_output(["rpm", "-qp", "--recommends", str(package)], text=True))
            unpacked = Path(temporary) / "root"
            unpacked.mkdir()
            archive = subprocess.run(["rpm2cpio", str(package)], capture_output=True, check=True).stdout
            subprocess.run(["cpio", "-idm", "--quiet"], input=archive, cwd=unpacked, check=True)
            self.check_payload(unpacked)


if __name__ == "__main__":
    unittest.main()
