"""KDE Plasma Wayland backend: shortcut encoding, clipboard framing, and paste safety."""

import base64
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")

from gi.repository import GLib

from jumpkut import kde
from jumpkut.__main__ import is_kde_wayland, wayland_problem


def run_loop_until(condition, timeout=5.0):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)
    return condition()


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
    result._qt_key = kde.qt_key_code("c", {"Alt"})
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

    def watch(self, state, text):
        environment = {"PATH": os.environ["PATH"]}
        if state is not None:
            environment["CLIPBOARD_STATE"] = state
        return subprocess.run(["sh", "-c", kde._WATCH_SCRIPT], input=text.encode(), env=environment,
                              capture_output=True, check=True, timeout=5).stdout.decode()

    def test_watch_script_emits_text_only_for_the_data_state(self):
        line = self.watch("data", "a\nb")
        self.assertEqual(kde.parse_watch_line(line.rstrip("\n")), ("data", "a\nb"))

    def test_watch_script_fails_closed_for_sensitive_unknown_and_unset_states(self):
        # wl-paste reports sensitivity from the same offer that feeds stdin;
        # without a state (old wl-clipboard) nothing may be recorded.
        self.assertEqual(self.watch("sensitive", "hunter2"), "sensitive\n")
        self.assertEqual(self.watch("someday", "hunter2"), "someday\n")
        self.assertEqual(self.watch(None, "hunter2"), "unknown\n")
        self.assertNotIn("--list-types", kde._WATCH_SCRIPT)

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

    def test_never_types_its_own_shortcut(self):
        errors = []
        keyboard = FakeKeyboard()
        subject = backend({"id": "{a}", "class": "konsole", "pid": "1"}, keyboard)
        subject._qt_key = kde.qt_key_code("v", {"Control", "Shift"})
        subject._reported_conflict = False
        subject._keyboard_error = errors.append
        self.assertFalse(subject.paste("{a}"))
        self.assertFalse(subject.paste("{a}"))
        self.assertEqual(keyboard.taps, [])
        self.assertEqual(len(errors), 1)
        subject._windows.active["class"] = "org.mozilla.firefox"  # Ctrl+V is still free.
        self.assertTrue(subject.paste("{a}"))

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
            with patch("jumpkut.__main__.wl_clipboard_version", return_value=(2, 3, 0)):
                self.assertIsNone(wayland_problem({**kde_session, "DISPLAY": ":0"}))
            # 2.2.x never reports password-manager copies as sensitive.
            with patch("jumpkut.__main__.wl_clipboard_version", return_value=(2, 2, 1)):
                self.assertIn("2.3.0 or newer", wayland_problem({**kde_session, "DISPLAY": ":0"}))
            with patch("jumpkut.__main__.wl_clipboard_version", return_value=None):
                self.assertIn("unknown version", wayland_problem({**kde_session, "DISPLAY": ":0"}))


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



class AcceptWhileModifiersHeldTests(unittest.TestCase):
    def test_enter_with_a_held_modifier_waits_for_release(self):
        from jumpkut.app import Jumpkut
        held = [True]
        accepted = []
        popup = SimpleNamespace(get_visible=lambda: True, is_active=lambda: True, selected=None)
        app = SimpleNamespace(
            backend=SimpleNamespace(reads_modifiers_from_focus=True, any_modifier_down=lambda: held[0]),
            config=SimpleNamespace(settings=SimpleNamespace(auto_paste=True)),
            popup=popup, _accept_source=0,
            cancel_popup=lambda restore=True: accepted.append(restore),
        )
        app._paste_waits_for_modifiers = lambda: Jumpkut._paste_waits_for_modifiers(app)
        app.accept_popup = lambda: Jumpkut.accept_popup(app)
        app._accept_when_released = lambda: Jumpkut._accept_when_released(app)
        app.accept_popup()
        self.assertNotEqual(app._accept_source, 0)
        self.assertEqual(accepted, [])
        held[0] = False
        self.assertTrue(run_loop_until(lambda: accepted))
        self.assertEqual(app._accept_source, 0)


class ClipboardPublicationTests(unittest.TestCase):
    """Drive WaylandClipboard against fake wl-paste/wl-copy executables."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bin = Path(self.directory.name)
        self.log = self.bin / "copies.log"
        self.current = self.bin / "current-clipboard"
        # --watch idles; a one-shot read prints the simulated clipboard.
        self.write_tool("wl-paste", f'case " $* " in *" --watch "*) exec sleep 30 ;; esac\n'
                                    f'cat {self.current} 2>/dev/null || exit 1\n')
        path = patch.dict(os.environ, {"PATH": f"{self.bin}:{os.environ['PATH']}"})
        path.start()
        self.addCleanup(path.stop)

    def write_tool(self, name, body):
        tool = self.bin / name
        tool.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        tool.chmod(0o755)

    def clipboard(self):
        failures = []
        clip = kde.WaylandClipboard(lambda state, text: None, failures.append)
        self.addCleanup(clip.close)
        return clip, failures

    def test_confirmed_publication_is_done(self):
        self.write_tool("wl-copy", f"cat >> {self.log}; exec sleep 30\n")
        clip, failures = self.clipboard()
        clip.set_text("chosen")
        self.assertEqual(clip.publish_state, "pending")
        self.assertTrue(run_loop_until(lambda: self.log.exists() and self.log.read_text() == "chosen"))
        clip._handle_line("data " + base64.b64encode(b"other").decode())
        self.assertEqual(clip.publish_state, "pending")
        self.current.write_text("chosen")
        clip._handle_line("data " + base64.b64encode(b"chosen").decode())
        self.assertTrue(run_loop_until(lambda: clip.publish_state != "pending"))
        self.assertEqual(clip.publish_state, "done")
        self.assertEqual(failures, [])
        clip._current.force_exit()

    def test_failed_wl_copy_is_not_success(self):
        self.write_tool("wl-copy", "cat >/dev/null; exit 3\n")
        clip, failures = self.clipboard()
        clip.set_text("chosen")
        self.assertTrue(run_loop_until(lambda: clip.publish_state != "pending"))
        self.assertEqual(clip.publish_state, "failed")
        self.assertIn("status 3", failures[0])

    def test_unconfirmed_publication_times_out_as_failure(self):
        self.write_tool("wl-copy", "cat >/dev/null; exec sleep 30\n")
        clip, failures = self.clipboard()
        clip.CONFIRM_TIMEOUT_MS = 50
        clip.set_text("chosen")
        self.assertTrue(run_loop_until(lambda: clip.publish_state != "pending"))
        self.assertEqual(clip.publish_state, "failed")
        self.assertIn("in time", failures[0])
        clip._current.force_exit()

    def test_stale_confirmation_never_leaves_an_older_publisher_running(self):
        # A queued watcher event for the same text can confirm A before its
        # wl-copy owns the clipboard; B must still wait for A to be gone.
        self.write_tool("wl-copy", f"text=$(cat); echo \"start $text\" >> {self.log}; exec sleep 30\n")
        changes = []
        clip = kde.WaylandClipboard(lambda state, text: changes.append(text), lambda message: None)
        self.addCleanup(clip.close)
        clip.set_text("A")
        self.current.write_text("A")  # An earlier owner still holds the same text.
        clip._handle_line("data " + base64.b64encode(b"A").decode())
        self.assertTrue(run_loop_until(lambda: clip.publish_state == "done"))
        self.assertTrue(run_loop_until(lambda: self.log.exists() and "start A" in self.log.read_text()))
        first = clip._current
        clip.set_text("B")
        self.assertTrue(run_loop_until(lambda: "start B" in self.log.read_text()))
        self.assertTrue(first.get_if_signaled())
        self.assertEqual(self.log.read_text().splitlines(), ["start A", "start B"])
        # Plasma may restore A when its owner stops: that echo is not a new copy.
        clip._handle_line("data " + base64.b64encode(b"A").decode())
        clip._handle_line("data " + base64.b64encode(b"B").decode())
        self.assertEqual(changes, ["A", "B"])
        self.current.write_text("B")
        self.assertTrue(run_loop_until(lambda: clip.publish_state == "done"))
        # After confirmation, a real copy of A elsewhere is recorded again.
        clip._handle_line("data " + base64.b64encode(b"A").decode())
        self.assertEqual(changes, ["A", "B", "A"])
        clip._current.force_exit()

    def test_newer_selection_stops_an_unconfirmed_publisher_before_starting(self):
        self.write_tool("wl-copy", f"text=$(cat); echo \"start $text\" >> {self.log}; exec sleep 30\n")
        clip, failures = self.clipboard()
        clip.set_text("first")
        self.assertTrue(run_loop_until(lambda: self.log.exists() and "first" in self.log.read_text()))
        first = clip._current
        clip.set_text("second")
        self.assertTrue(run_loop_until(lambda: "second" in self.log.read_text()))
        self.assertTrue(first.get_if_signaled())
        self.assertEqual(self.log.read_text().splitlines(), ["start first", "start second"])
        self.assertEqual(clip.publish_state, "pending")
        self.assertEqual(failures, [])
        clip._current.force_exit()


class FakePublisher:
    def __init__(self, text):
        self.text = text
        self.killed = False

    def force_exit(self):
        self.killed = True


class PublicationOrderingTests(unittest.TestCase):
    """Deterministic checks with fake publishers and clipboard reads."""

    def make(self):
        clip = kde.WaylandClipboard.__new__(kde.WaylandClipboard)
        clip.on_change = lambda state, text: None
        failures = []
        clip.on_publish_failed = failures.append
        clip.last_state = None
        clip.publish_state = "idle"
        clip._wanted = None
        clip._publish_generation = 0
        clip._live = {}
        clip._current = None
        clip._next = None
        clip._verifying = False
        clip._reverify = False
        clip._superseded = set()
        clip._confirm_id = 0
        clip._closed = False
        started = []

        def launch(text, on_exit):
            publisher = FakePublisher(text)
            started.append(publisher)
            return publisher

        clip._launch_publisher = launch
        self.addCleanup(clip._clear_confirm_timer)
        return clip, started, failures

    def test_three_quick_selections_wait_for_every_older_publisher(self):
        clip, started, failures = self.make()
        clip.set_text("A")
        a = started[0]
        clip.set_text("B")
        clip.set_text("C")
        # A is still exiting: neither B nor C may start yet.
        self.assertTrue(a.killed)
        self.assertEqual([p.text for p in started], ["A"])
        clip._publisher_exited(a, "stopped")
        self.assertEqual([p.text for p in started], ["A", "C"])  # B was superseded.
        clip.set_text("D")
        c = started[1]
        self.assertTrue(c.killed)
        self.assertEqual(len(started), 2)
        clip._publisher_exited(c, "stopped")
        self.assertEqual([p.text for p in started], ["A", "C", "D"])
        self.assertEqual(failures, [])
        self.assertEqual(clip.publish_state, "pending")

    def test_stale_matching_event_needs_a_current_read(self):
        clip, started, failures = self.make()
        reads = []
        clip._read_current = lambda on_text: reads.append(on_text)
        clip.set_text("A")
        event = "data " + base64.b64encode(b"A").decode()
        clip._handle_line(event)  # Queued before someone copied X.
        self.assertEqual(clip.publish_state, "pending")
        reads.pop(0)("X")
        self.assertEqual(clip.publish_state, "pending")  # Do not paste X.
        clip._handle_line(event)  # Our publisher's own event.
        reads.pop(0)("A")
        self.assertEqual(clip.publish_state, "done")
        self.assertEqual(failures, [])

    def test_event_during_a_read_triggers_another_read(self):
        clip, started, failures = self.make()
        reads = []
        clip._read_current = lambda on_text: reads.append(on_text)
        clip.set_text("A")
        event = "data " + base64.b64encode(b"A").decode()
        clip._handle_line(event)
        clip._handle_line(event)  # Arrives while the first read is running.
        self.assertEqual(len(reads), 1)
        reads.pop(0)("X")
        self.assertEqual(len(reads), 1)  # Re-read rather than miss the newer event.
        reads.pop(0)("A")
        self.assertEqual(clip.publish_state, "done")

    def test_current_publisher_exit_before_confirmation_fails(self):
        clip, started, failures = self.make()
        clip.set_text("A")
        clip._publisher_exited(started[0], "wl-copy failed with status 1")
        self.assertEqual(clip.publish_state, "failed")
        self.assertIn("status 1", failures[0])


class FakeBus:
    def __init__(self):
        self.calls = []
        self.subscriptions = {}
        self.next_subscription = 0
        self.registered = []

    def get_unique_name(self):
        return ":1.42"

    def signal_subscribe(self, _sender, _interface, _member, path, _arg0, _flags, callback, _data):
        self.next_subscription += 1
        self.subscriptions[self.next_subscription] = (path, callback)
        return self.next_subscription

    def signal_unsubscribe(self, subscription):
        self.subscriptions.pop(subscription, None)

    def call(self, _name, path, _interface, method, *_rest):
        self.calls.append((method, path))

    def call_sync(self, _name, path, _interface, method, *_rest):
        self.calls.append((method, path))
        if method == "loadScript":
            return GLib.Variant("(i)", (-1,))
        return GLib.Variant("()", ())

    def respond(self, code, results):
        for path, callback in list(self.subscriptions.values()):
            callback(self, ":1.1", path, None, "Response", SimpleNamespace(unpack=lambda: (code, results)), None)

    def register_object(self, *_args):
        self.registered.append("object")
        return 7

    def unregister_object(self, registration):
        self.registered.remove("object")


class PortalLifecycleTests(unittest.TestCase):
    def test_close_cancels_a_pending_start(self):
        bus = FakeBus()
        keyboard = kde._PortalKeyboard(bus, lambda message: None)
        keyboard.start()
        self.assertTrue(keyboard.starting)
        keyboard.close()
        self.assertFalse(keyboard.starting)
        self.assertEqual(bus.subscriptions, {})
        keyboard.start()  # A later start is allowed again.
        self.assertTrue(keyboard.starting)

    def test_stale_response_closes_its_session(self):
        bus = FakeBus()
        keyboard = kde._PortalKeyboard(bus, lambda message: None)
        keyboard.start()
        (path, callback), = bus.subscriptions.values()
        keyboard.close()
        callback(bus, ":1.1", path, None, "Response",
                 SimpleNamespace(unpack=lambda: (0, {"session_handle": "/session/late"})), None)
        self.assertIn(("Close", "/session/late"), bus.calls)
        self.assertNotIn("SelectDevices", [method for method, _path in bus.calls])
        self.assertIsNone(keyboard._session)
        self.assertFalse(keyboard.ready)

    def test_current_response_continues_the_session(self):
        bus = FakeBus()
        keyboard = kde._PortalKeyboard(bus, lambda message: None)
        keyboard.start()
        bus.respond(0, {"session_handle": "/session/now"})
        self.assertEqual(keyboard._session, "/session/now")
        self.assertEqual([method for method, _path in bus.calls], ["CreateSession", "SelectDevices"])


class ActiveWindowSetupTests(unittest.TestCase):
    def test_failed_script_load_unregisters_the_report_object(self):
        bus = FakeBus()
        bus.call_sync = lambda _name, path, _interface, method, *_rest: (
            GLib.Variant("(s)", (":1.16",)) if method == "GetNameOwner"
            else GLib.Variant("(i)", (-1,)) if method == "loadScript" else GLib.Variant("()", ())
        )
        with tempfile.TemporaryDirectory() as runtime, patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
            with self.assertRaises(RuntimeError):
                kde._ActiveWindows(bus)
        self.assertEqual(bus.registered, [])


if __name__ == "__main__":
    unittest.main()
