"""Portable launchers must survive the parent AppImage's temporary mount."""

import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from jumpkut.__main__ import start_background
from jumpkut.settings import _quote_exec_argument, autostart_path, set_autostart


class PortableRuntimeTests(unittest.TestCase):
    def test_background_restarts_persistent_launcher_with_its_own_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / 'Jumpkut with spaces % "quotes".AppImage'
            launcher.write_text(
                '#!/usr/bin/python3\n'
                'import json, os, sys\n'
                'print(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd()}), flush=True)\n',
                encoding='utf-8',
            )
            launcher.chmod(0o755)
            interpreter = root / 'interpreter'
            interpreter.write_text(launcher.read_text(), encoding='utf-8')
            interpreter.chmod(0o755)
            children = []
            spawn = subprocess.Popen

            def start_child(*args, **kwargs):
                child = spawn(*args, **kwargs)
                children.append(child)
                return child

            state = root / 'state'
            with patch.dict(os.environ, {
                'JUMPKUT_LAUNCHER': str(launcher), 'XDG_STATE_HOME': str(state),
            }), patch('jumpkut.__main__.sys.executable', str(interpreter)), patch(
                'jumpkut.__main__.subprocess.Popen', side_effect=start_child,
            ):
                self.assertEqual(start_background(), 0)
            for child in children:
                try:
                    child.wait(timeout=3)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
            log = state / 'jumpkut/jumpkut.log'
            self.assertEqual(json.loads(log.read_text()), {
                'args': ['--daemon'], 'cwd': str(root),
            })

    def test_autostart_uses_persistent_launcher_with_desktop_entry_quoting(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = str(Path(directory) / 'Jumpkut $name %f "quotes".AppImage')
            with patch.dict(os.environ, {
                'JUMPKUT_LAUNCHER': launcher, 'XDG_CONFIG_HOME': directory,
            }):
                set_autostart(True)
                entry = autostart_path().read_text(encoding='utf-8')
            self.assertIn(
                f'Exec=/usr/bin/env {_quote_exec_argument(launcher)} --daemon\n', entry,
            )
            self.assertNotIn('/tmp/.mount_', entry)

    def test_portable_autostart_preserves_extraction_mode_on_hosts_without_fuse(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = str(Path(directory) / 'Jumpkut with spaces %f.AppImage')
            with patch.dict(os.environ, {
                'JUMPKUT_LAUNCHER': launcher, 'XDG_CONFIG_HOME': directory,
                'APPIMAGE_EXTRACT_AND_RUN': '1',
            }):
                set_autostart(True)
                entry = autostart_path().read_text(encoding='utf-8')
            self.assertIn(
                'Exec=/usr/bin/env APPIMAGE_EXTRACT_AND_RUN=1 '
                f'{_quote_exec_argument(launcher)} --daemon\n', entry,
            )
