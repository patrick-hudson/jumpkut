import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jumpkut.settings import (
    Config,
    Settings,
    _quote_exec_argument,
    autostart_enabled,
    autostart_path,
    data_path,
    log_path,
    set_autostart,
)


class SettingsTests(unittest.TestCase):
    def test_resume_default_and_legacy_preferences_preserve_the_original_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = b'{"hotkey": "<Control>v", "sticky": true}'
            path.write_bytes(original)
            config = Config(path)
            self.assertTrue(config.settings.resume_last_selection)
            self.assertIsNone(config.load_error)
            self.assertEqual(path.read_bytes(), original)
            config.save(replace(config.settings, resume_last_selection=False))
            self.assertFalse(Config(path).settings.resume_last_selection)

    def test_defaults_and_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "config.json"
            config = Config(path)
            self.assertEqual(config.settings, Settings())
            self.assertIsNone(config.load_error)
            self.assertEqual(config.settings.history_limit, 30)
            self.assertEqual(config.settings.tray_limit, 10)
            self.assertFalse(config.settings.custom_quick_limit)
            self.assertFalse(config.settings.custom_tray_limit)
            self.assertEqual(config.settings.quick_history_limit, 30)
            self.assertEqual(config.settings.tray_history_limit, 10)
            settings = Settings("<Control><Alt>v", 35, True, False, False,
                                tray_limit=12, custom_quick_limit=True,
                                custom_tray_limit=True)
            config.save(settings)
            self.assertEqual(config.settings, settings)
            reloaded = Config(path)
            self.assertEqual(reloaded.settings, settings)
            self.assertIsNone(reloaded.load_error)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_legacy_custom_limit_preserves_value_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = b'{"history_limit": 42}'
            path.write_bytes(original)
            config = Config(path)
            self.assertIsNone(config.load_error)
            self.assertEqual(config.settings, Settings(history_limit=42,
                                                      custom_quick_limit=True))
            self.assertEqual(config.settings.quick_history_limit, 42)
            self.assertEqual(config.settings.tray_history_limit, 10)
            self.assertEqual(path.read_bytes(), original)

    def test_legacy_default_limit_uses_new_defaults_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for limit in (200, 30):
                with self.subTest(limit=limit):
                    original = ('{"history_limit": ' + str(limit)
                                + ', "hotkey": "<Control>v", "sticky": true}').encode()
                    path.write_bytes(original)
                    config = Config(path)
                    self.assertIsNone(config.load_error)
                    self.assertEqual(config.settings, Settings(hotkey="<Control>v", sticky=True))
                    self.assertEqual(path.read_bytes(), original)

    def test_missing_history_limit_uses_new_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"hotkey": "<Control>v"}', encoding="utf-8")
            config = Config(path)
            self.assertIsNone(config.load_error)
            self.assertEqual(config.settings, Settings(hotkey="<Control>v"))

    def test_overrides_are_independent_and_remember_values_when_disabled(self):
        settings = Settings(history_limit=48, tray_limit=7)
        self.assertEqual(settings.quick_history_limit, 30)
        self.assertEqual(settings.tray_history_limit, 10)
        quick = replace(settings, custom_quick_limit=True)
        self.assertEqual(quick.quick_history_limit, 48)
        self.assertEqual(quick.tray_history_limit, 10)
        tray = replace(settings, custom_tray_limit=True)
        self.assertEqual(tray.quick_history_limit, 30)
        self.assertEqual(tray.tray_history_limit, 7)
        custom = replace(settings, custom_quick_limit=True, custom_tray_limit=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = Config(path)
            disabled = replace(custom, custom_quick_limit=False, custom_tray_limit=False)
            config.save(disabled)
            reloaded = Config(path)
            self.assertIsNone(reloaded.load_error)
            self.assertEqual(reloaded.settings, disabled)
            self.assertEqual(reloaded.settings.history_limit, 48)
            self.assertEqual(reloaded.settings.tray_limit, 7)
            self.assertEqual(reloaded.settings.quick_history_limit, 30)
            self.assertEqual(reloaded.settings.tray_history_limit, 10)
            reenabled = replace(reloaded.settings, custom_quick_limit=True,
                                custom_tray_limit=True)
            self.assertEqual(reenabled.quick_history_limit, 48)
            self.assertEqual(reenabled.tray_history_limit, 7)

    def test_modern_disabled_quick_override_does_not_apply_legacy_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = b'{"history_limit": 200, "custom_quick_limit": false}'
            path.write_bytes(original)
            config = Config(path)
            self.assertIsNone(config.load_error)
            self.assertEqual(config.settings.history_limit, 200)
            self.assertFalse(config.settings.custom_quick_limit)
            self.assertEqual(config.settings.quick_history_limit, 30)
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_values_are_rejected_before_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = Config(path)
            config.save(Settings(history_limit=3))
            original = path.read_bytes()
            invalid_settings = [
                Settings(history_limit=value) for value in (0, 10001, -1, 1.5, True)
            ] + [
                Settings(tray_limit=value) for value in (0, 10001, -1, 1.5, True)
            ] + [
                Settings(hotkey=value) for value in ("", " \t", None, 3)
            ] + [
                Settings(sticky=1), Settings(auto_paste="true"), Settings(persist=None),
                Settings(custom_quick_limit=1), Settings(custom_tray_limit="true"),
                Settings(resume_last_selection=1),
            ]
            for settings in invalid_settings:
                with self.subTest(settings=settings), self.assertRaises(ValueError):
                    config.save(settings)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(config.settings, Settings(history_limit=3))
            for limit in (1, 10000):
                config.save(Settings(history_limit=limit, tray_limit=limit,
                                     custom_quick_limit=True, custom_tray_limit=True))
                self.assertEqual(config.settings.history_limit, limit)
                self.assertEqual(config.settings.tray_limit, limit)
                self.assertEqual(config.settings.quick_history_limit, limit)
                self.assertEqual(config.settings.tray_history_limit, limit)

    def test_invalid_load_uses_defaults_and_preserves_original(self):
        invalid_content = [
            b"broken json\xff", b"[]", b'{"history_limit": 0}',
            b'{"history_limit": true}', b'{"sticky": "true"}',
            b'{"hotkey": ""}', b'{"unrecognized": 10}',
            b'{"history_limit": 200.0}', b'{"tray_limit": 0}',
            b'{"tray_limit": true}', b'{"tray_limit": 10001}',
            b'{"custom_quick_limit": 1}', b'{"custom_tray_limit": "true"}',
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for raw in invalid_content:
                with self.subTest(raw=raw):
                    path.write_bytes(raw)
                    config = Config(path)
                    self.assertEqual(config.settings, Settings())
                    self.assertIsNotNone(config.load_error)
                    self.assertEqual(path.read_bytes(), raw)

    def test_failed_save_keeps_previous_settings_and_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            config = Config(path)
            config.save(Settings(history_limit=3))
            original = path.read_bytes()
            with patch("jumpkut.settings.os.replace", side_effect=OSError("disk failed")):
                with self.assertRaises(OSError):
                    config.save(Settings(history_limit=40))
            self.assertEqual(config.settings, Settings(history_limit=3))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob(".*.tmp")), [])

    def test_xdg_locations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {
                "XDG_CONFIG_HOME": str(root / "configuration"),
                "XDG_DATA_HOME": str(root / "data"),
                "XDG_STATE_HOME": str(root / "state"),
            }):
                self.assertEqual(Config().path, root / "configuration/jumpkut/config.json")
                self.assertEqual(data_path(), root / "data/jumpkut/history.json")
                self.assertEqual(log_path(), root / "state/jumpkut/jumpkut.log")
                self.assertEqual(autostart_path(), root / "configuration/autostart/jumpkut.desktop")

    def test_empty_or_relative_xdg_locations_use_home(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {
                "HOME": directory, "XDG_CONFIG_HOME": "", "XDG_DATA_HOME": "relative", "XDG_STATE_HOME": "relative",
            }):
                self.assertEqual(Config().path, root / ".config/jumpkut/config.json")
                self.assertEqual(data_path(), root / ".local/share/jumpkut/history.json")
                self.assertEqual(log_path(), root / ".local/state/jumpkut/jumpkut.log")
                self.assertEqual(autostart_path(), root / ".config/autostart/jumpkut.desktop")

    def test_autostart_enable_hidden_and_disable_only_our_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}):
                path = autostart_path()
                self.assertFalse(autostart_enabled())
                set_autostart(True)
                self.assertTrue(autostart_enabled())
                text = path.read_text(encoding="utf-8")
                run_path = Path(__file__).resolve().parents[1] / "run.py"
                self.assertIn(f"Exec=/usr/bin/python3 {_quote_exec_argument(str(run_path))} --daemon\n", text)
                self.assertIn("Type=Application\n", text)
                self.assertIn("Terminal=false\n", text)
                other = path.with_name("another.desktop")
                other.write_text("untouched", encoding="utf-8")
                path.write_text(text + "Hidden=true\n", encoding="utf-8")
                self.assertFalse(autostart_enabled())
                set_autostart(False)
                self.assertFalse(autostart_enabled())
                self.assertFalse(path.exists())
                self.assertEqual(other.read_text(encoding="utf-8"), "untouched")
                set_autostart(False)

    def test_autostart_read_tolerates_invalid_desktop_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}):
                path = autostart_path()
                path.parent.mkdir()
                for text in ("garbage", "[Different]\nHidden=false\n", "[Desktop Entry]\nHidden=maybe\n"):
                    with self.subTest(text=text):
                        path.write_text(text, encoding="utf-8")
                        self.assertFalse(autostart_enabled())

    def test_autostart_rejects_non_boolean(self):
        for value in (1, "true", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                set_autostart(value)

    def test_exec_quoting_handles_spaces_backslashes_and_expansion(self):
        self.assertEqual(_quote_exec_argument("/tmp/with spaces/run.py"), '"/tmp/with spaces/run.py"')
        quoted = _quote_exec_argument('/tmp/$user/`command`/"quote"/back\\slash/%f/run.py')
        self.assertEqual(
            quoted,
            '"/tmp/\\\\$user/\\\\`command\\\\`/\\\\"quote\\\\"/back\\\\\\\\slash/%%f/run.py"',
        )
        self.assertEqual(_quote_exec_argument("a\nb\tc\rd"), '"a\\nb\\tc\\rd"')


if __name__ == "__main__":
    unittest.main()
