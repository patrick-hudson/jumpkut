"""KDE Plasma Wayland backend: shortcut encoding, clipboard framing, and paste safety."""

import base64
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from jumpkut import kde
from jumpkut.__main__ import is_kde_wayland, wayland_problem


class FakeKeyboard:
    def __init__(self, ready=True):
        self.ready = ready
        self.starting = False
        self.taps = []

    def tap(self, keysyms):
        self.taps.append(list(keysyms))
        return True


def backend(active, keyboard=None, owners=None):
    result = kde.KdeWaylandBackend.__new__(kde.KdeWaylandBackend)
    result._closed = False
    result._keyboard = keyboard or FakeKeyboard()
    result._windows = SimpleNamespace(active=active, owners=owners or {})
    return result


CTRL, SHIFT, V = 0xFFE3, 0xFFE1, 0x76


class ShortcutEncodingTests(unittest.TestCase):
    def test_letters_use_uppercase_qt_keys_with_modifier_bits(self):
        self.assertEqual(kde.qt_key_code("c", {"Alt"}), 0x08000000 | 0x43)
        self.assertEqual(kde.qt_key_code("v", {"Control", "Shift"}), 0x04000000 | 0x02000000 | 0x56)
        self.assertEqual(kde.qt_key_code("1", {"Super"}), 0x10000000 | 0x31)

    def test_named_and_function_keys(self):
        self.assertEqual(kde.qt_key_code("space", {"Control"}), 0x04000000 | 0x20)
        self.assertEqual(kde.qt_key_code("F12", {"Alt"}), 0x08000000 | 0x0100003B)
        self.assertEqual(kde.qt_key_code("Insert", {"Shift"}), 0x02000000 | 0x01000006)

    def test_rejects_unsupported_modifiers_and_keys(self):
        with self.assertRaises(RuntimeError):
            kde.qt_key_code("c", {"Hyper"})
        with self.assertRaises(RuntimeError):
            kde.qt_key_code("VoidSymbol", {"Alt"})


class ClipboardFramingTests(unittest.TestCase):
    def test_data_lines_decode_multiline_unicode_exactly(self):
        text = "héllo\n\twörld\n"
        line = "data " + base64.b64encode(text.encode()).decode()
        self.assertEqual(kde.parse_watch_line(line), ("data", text))
        self.assertEqual(kde.parse_watch_line("data "), ("data", ""))

    def test_states_without_text(self):
        self.assertEqual(kde.parse_watch_line("sensitive"), ("sensitive", None))
        self.assertEqual(kde.parse_watch_line("nil"), ("nil", None))
        self.assertEqual(kde.parse_watch_line("data not*base64"), ("invalid", None))

    def test_watch_script_marks_password_manager_types_sensitive(self):
        for mime in ("x-kde-passwordManagerHint", "application/x-nspasteboard-concealed-type",
                     "application/x-keepassxc-secret"):
            self.assertIn(mime, kde._WATCH_SCRIPT)

    def test_kwin_script_strings_are_quoted(self):
        script = kde._ACTIVATE_SCRIPT % {"target": kde._js_string('{x"); evil(); ("}')}
        self.assertIn('const target = "{x\\"); evil(); (\\"}";', script)


class PasteSafetyTests(unittest.TestCase):
    def test_pastes_ctrl_v_into_the_active_target(self):
        keyboard = FakeKeyboard()
        subject = backend({"id": "{a}", "class": "org.mozilla.firefox", "pid": "1"}, keyboard)
        self.assertTrue(subject.paste("{a}"))
        self.assertEqual(keyboard.taps, [[CTRL, V]])

    def test_terminals_get_ctrl_shift_v(self):
        for resource_class in ("org.kde.konsole", "com.mitchellh.ghostty", "kitty"):
            keyboard = FakeKeyboard()
            subject = backend({"id": "{t}", "class": resource_class, "pid": "1"}, keyboard)
            self.assertTrue(subject.paste("{t}"))
            self.assertEqual(keyboard.taps, [[CTRL, SHIFT, V]])

    def test_refuses_other_windows_our_windows_and_missing_permission(self):
        keyboard = FakeKeyboard()
        self.assertFalse(backend({"id": "{b}", "class": "x", "pid": "1"}, keyboard).paste("{a}"))
        own = {"id": "{a}", "class": "jumpkut", "pid": str(os.getpid())}
        self.assertFalse(backend(own, keyboard).paste("{a}"))
        self.assertFalse(backend({"id": "{a}", "class": "x", "pid": "1"}, keyboard).paste(None))
        self.assertEqual(keyboard.taps, [])
        unready = FakeKeyboard(ready=False)
        self.assertFalse(backend({"id": "{a}", "class": "x", "pid": "1"}, unready).paste("{a}"))
        self.assertEqual(unready.taps, [])

    def test_own_windows_are_recognised_by_process(self):
        subject = backend({}, owners={"{mine}": str(os.getpid()), "{theirs}": "1"})
        self.assertTrue(subject.is_own_window("{mine}"))
        self.assertFalse(subject.is_own_window("{theirs}"))
        self.assertFalse(subject.is_own_window("{unknown}"))


class ActiveWindowReportTests(unittest.TestCase):
    def make(self):
        windows = kde._ActiveWindows.__new__(kde._ActiveWindows)
        windows._kwin_owner = ":1.16"
        windows.active = {}
        windows.owners = {}
        return windows

    def invoke(self, windows, sender, values):
        replies = []
        invocation = SimpleNamespace(
            return_value=lambda value: replies.append("ok"),
            return_dbus_error=lambda name, message: replies.append(name),
        )
        parameters = SimpleNamespace(unpack=lambda: values)
        windows._method(None, sender, None, None, "Activated", parameters, invocation)
        return replies

    def test_only_kwin_may_report_the_active_window(self):
        windows = self.make()
        replies = self.invoke(windows, ":1.99", ("{fake}", "konsole", "", "1"))
        self.assertEqual(replies, ["org.freedesktop.DBus.Error.AccessDenied"])
        self.assertEqual(windows.active, {})

    def test_reports_update_active_window_and_owner_cache(self):
        windows = self.make()
        self.assertEqual(self.invoke(windows, ":1.16", ("{a}", "kitty", "", "42")), ["ok"])
        self.assertEqual(windows.active, {"id": "{a}", "class": "kitty", "x11": "", "pid": "42"})
        self.invoke(windows, ":1.16", ("", "", "", "0"))
        self.assertEqual(windows.active, {})
        self.assertEqual(windows.owners, {"{a}": "42"})
        for index in range(100):
            self.invoke(windows, ":1.16", (f"{{w{index}}}", "x", "", "1"))
        self.assertEqual(len(windows.owners), 64)
        self.assertIn("{w99}", windows.owners)


class SessionDetectionTests(unittest.TestCase):
    def test_detects_kde_wayland_only(self):
        self.assertTrue(is_kde_wayland({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}))
        self.assertFalse(is_kde_wayland({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "KDE"}))
        self.assertFalse(is_kde_wayland({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}))

    def test_explains_unsupported_wayland_sessions(self):
        self.assertIsNone(wayland_problem({"XDG_SESSION_TYPE": "x11"}))
        self.assertIn("KDE Plasma", wayland_problem({"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"}))
        kde_session = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}
        self.assertIn("XWayland", wayland_problem(kde_session))
        with patch("jumpkut.__main__.shutil.which", return_value=None):
            self.assertIn("wl-clipboard", wayland_problem({**kde_session, "DISPLAY": ":0"}))
        with patch("jumpkut.__main__.shutil.which", return_value="/usr/bin/wl-paste"):
            self.assertIsNone(wayland_problem({**kde_session, "DISPLAY": ":0"}))


class WaylandRecordingTests(unittest.TestCase):
    def make_app(self):
        from jumpkut.app import Jumpkut
        added = []
        app = SimpleNamespace(
            paused=False, _generation=0, _ignore_text=None, _last_selected_clip_id="old",
            history=SimpleNamespace(add=added.append), _history_changed=lambda: None,
        )
        app._record_text = lambda text: Jumpkut._record_text(app, text)
        changed = lambda state, text: Jumpkut._wayland_clipboard_changed(app, state, text)
        return app, changed, added

    def test_records_text_but_skips_sensitive_paused_and_own_selection(self):
        app, changed, added = self.make_app()
        changed("data", "one")
        changed("sensitive", None)
        changed("nil", None)
        app._ignore_text = "chosen"
        changed("data", "chosen")
        app.paused = True
        changed("data", "while paused")
        self.assertEqual(added, ["one"])
        self.assertIsNone(app._ignore_text)
        self.assertIsNone(app._last_selected_clip_id)


if __name__ == "__main__":
    unittest.main()
