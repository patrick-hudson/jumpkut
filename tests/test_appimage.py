"""Exercise the AppImage launcher boundary without a desktop or build downloads."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[1]


class AppImageLauncherTests(unittest.TestCase):
    def test_current_mount_replaces_inherited_bundle_paths_and_preserves_user_data(self):
        with tempfile.TemporaryDirectory(prefix="jumpkut appimage ") as temporary:
            root = Path(temporary)
            appdir = root / "current mount"
            bindir = appdir / "usr/bin"
            bindir.mkdir(parents=True)
            shutil.copy2(PROJECT / "packaging/AppRun", appdir / "AppRun")
            for name in ("gdk-pixbuf-query-loaders", "gtk-query-immodules-3.0"):
                query = bindir / name
                query.write_text("#!/bin/sh\nprintf 'synthetic loader cache\\n'\n", encoding="utf-8")
                query.chmod(0o755)
            interpreter = bindir / "python3"
            interpreter.write_text(
                '#!/bin/sh\nexec /usr/bin/env -u PYTHONHOME -u PYTHONPATH /usr/bin/python3 '
                f'"{root / "inspect.py"}" "$@"\n',
                encoding="utf-8",
            )
            interpreter.chmod(0o755)
            (root / "inspect.py").write_text(
                "import json, os, pathlib, sys\n"
                "print(json.dumps({'args':sys.argv[1:], 'env':dict(os.environ), "
                "'cache_exists':pathlib.Path(os.environ['GDK_PIXBUF_MODULE_FILE']).is_file()}))\n",
                encoding="utf-8",
            )
            image = root / "Jumpkut 1.2.3.AppImage"
            environment = os.environ | {
                "APPDIR": "/tmp/expired-mount",
                "APPIMAGE": str(image),
                "JUMPKUT_LAUNCHER": "/tmp/expired-mount/AppRun",
                "LD_LIBRARY_PATH": "/tmp/expired-mount/usr/lib",
                "PYTHONHOME": "/tmp/expired-mount/usr",
                "PYTHONPATH": "/tmp/expired-mount/modules",
                "PYTHONUSERBASE": "/tmp/foreign-python",
                "GI_TYPELIB_PATH": "/tmp/expired-mount/typelibs",
                "GDK_PIXBUF_MODULE_FILE": "/tmp/expired-mount/loaders.cache",
                "GTK_IM_MODULE_FILE": "/tmp/expired-mount/immodules.cache",
                "GTK_MODULES": "a-host-only-module",
                "XDG_CONFIG_HOME": str(root / "private config"),
                "XDG_DATA_HOME": str(root / "private history"),
                "XDG_STATE_HOME": str(root / "private state"),
                "TMPDIR": str(root),
            }
            completed = subprocess.run(
                [str(appdir / "AppRun"), "--history"], env=environment,
                capture_output=True, text=True, check=True,
            )
            captured = json.loads(completed.stdout)
            actual = captured["env"]
            self.assertEqual(captured["args"], ["-m", "jumpkut", "--history"])
            self.assertEqual(actual["APPDIR"], str(appdir))
            self.assertEqual(actual["JUMPKUT_LAUNCHER"], str(image))
            self.assertEqual(actual["LD_LIBRARY_PATH"], str(appdir / "usr/lib"))
            self.assertEqual(actual["GI_TYPELIB_PATH"], str(appdir / "usr/lib/girepository-1.0"))
            for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
                self.assertEqual(actual[name], environment[name])
            self.assertNotIn("PYTHONUSERBASE", actual)
            self.assertNotIn("GTK_MODULES", actual)
            self.assertTrue(captured["cache_exists"])
            self.assertFalse(Path(actual["GDK_PIXBUF_MODULE_FILE"]).parent.exists())


if __name__ == "__main__":
    unittest.main()
