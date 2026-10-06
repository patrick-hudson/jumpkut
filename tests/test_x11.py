"""Safety checks for the X11 focus and synthetic paste boundary."""

from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

from Xlib import X

from jumpkut.x11 import X11Backend


class FakeWindow:
    def __init__(self, window_id, parent=None, wm_class=None):
        self.id = window_id
        self.parent = parent
        self.wm_class = wm_class
        self.grabbed = set()
        self.actions = []

    def query_tree(self):
        return SimpleNamespace(parent=self.parent)

    def get_wm_class(self):
        return self.wm_class

    def ungrab_key(self, code, mask):
        self.grabbed.discard((code, mask))
        self.actions.append(("ungrab_key", code, mask))

    def grab_key(self, code, mask, _owner_events, _pointer_mode, _keyboard_mode, onerror=None):
        self.grabbed.add((code, mask))
        self.actions.append(("grab_key", code, mask))


class FakeDisplay:
    def __init__(self, focus, windows):
        self.focus = focus
        self.windows = {window.id: window for window in windows}
        self.keymap = [0] * 32
        self.input = []
        self.server_grabbed = False
        self.actions = []
        self.events = []

    def create_resource_object(self, _kind, window_id):
        return self.windows[window_id]

    def get_input_focus(self):
        return SimpleNamespace(focus=self.focus)

    def query_keymap(self):
        return self.keymap

    def grab_server(self):
        self.server_grabbed = True
        self.actions.append("grab_server")

    def ungrab_server(self):
        self.server_grabbed = False
        self.actions.append("ungrab_server")

    def xtest_fake_input(self, event_type, code):
        self.input.append((event_type, code))
        self.actions.append(("input", event_type, code))

    def sync(self):
        self.actions.append("sync")

    def flush(self):
        pass

    def pending_events(self):
        return len(self.events)

    def next_event(self):
        return self.events.pop(0)

    def ungrab_keyboard(self, _time):
        self.actions.append("ungrab")

    def close(self):
        self.closed = True


def make_backend(terminal=False):
    root = FakeWindow(10)
    target = FakeWindow(20, root, ("test", "XTerm") if terminal else ("test", "Editor"))
    child = FakeWindow(21, target)
    other = FakeWindow(30, root, ("other", "Editor"))
    backend = X11Backend.__new__(X11Backend)
    backend._closed = False
    backend._watch_id = 11
    backend._drain_id = 12
    backend._bindings = set()
    backend.has_paste = True
    backend._root = root
    backend._display = FakeDisplay(child, [root, target, child, other])
    root.actions = backend._display.actions
    backend._all_modifier_keycodes = {37, 50, 64, 108, 133}
    backend._configured_keycodes = {64, 108}
    backend._control_keycode = 37
    backend._shift_keycode = 50
    backend._paste_keycode = 55
    return backend, target, child, other


class PasteSafetyTests(unittest.TestCase):
    def test_paste_into_focused_child_of_original_window(self):
        backend, target, _child, _other = make_backend()
        self.assertTrue(backend.paste(target.id))
        self.assertEqual(
            backend._display.input,
            [(X.KeyPress, 37), (X.KeyPress, 55), (X.KeyRelease, 55), (X.KeyRelease, 37)],
        )
        self.assertFalse(backend._display.server_grabbed)

    def test_focus_change_prevents_injection(self):
        backend, target, _child, other = make_backend()
        backend._display.focus = other
        self.assertFalse(backend.paste(target.id))
        self.assertEqual(backend._display.input, [])
        self.assertFalse(backend._display.server_grabbed)

    def test_an_unrelated_held_modifier_prevents_injection(self):
        backend, target, _child, _other = make_backend()
        # Super is not part of the configured Alt+C shortcut.
        backend._display.keymap[133 // 8] = 1 << (133 % 8)
        self.assertFalse(backend.modifiers_down())
        self.assertFalse(backend.paste(target.id))
        self.assertEqual(backend._display.input, [])
        self.assertFalse(backend._display.server_grabbed)

    def test_right_alt_counts_as_held_shortcut_modifier(self):
        backend, _target, _child, _other = make_backend()
        backend._display.keymap[108 // 8] = 1 << (108 % 8)
        self.assertTrue(backend.modifiers_down())

    def test_terminal_paste_includes_shift_and_releases_every_key(self):
        backend, target, _child, _other = make_backend(terminal=True)
        self.assertTrue(backend.paste(target.id))
        self.assertEqual(
            backend._display.input,
            [
                (X.KeyPress, 37), (X.KeyPress, 50), (X.KeyPress, 55),
                (X.KeyRelease, 55), (X.KeyRelease, 50), (X.KeyRelease, 37),
            ],
        )

    def test_no_xtest_does_not_inject(self):
        backend, target, _child, _other = make_backend()
        backend.has_paste = False
        self.assertFalse(backend.paste(target.id))
        self.assertEqual(backend._display.input, [])

    def test_paste_chord_hotkey_is_suspended_until_input_has_been_processed(self):
        for terminal in (False, True):
            with self.subTest(terminal=terminal):
                backend, target, _child, _other = make_backend(terminal=terminal)
                mask = X.ControlMask | (X.ShiftMask if terminal else 0)
                bindings = {(55, mask), (55, mask | X.LockMask)}
                backend._bindings = bindings.copy()
                backend._root.grabbed = bindings.copy()
                self.assertTrue(backend.paste(target.id))
                self.assertEqual(backend._root.grabbed, bindings)
                self.assertEqual(backend._bindings, bindings)
                actions = backend._display.actions
                releases = [index for index, action in enumerate(actions) if isinstance(action, tuple) and action[0] == "ungrab_key"]
                presses = [index for index, action in enumerate(actions) if isinstance(action, tuple) and action[0] == "input"]
                restores = [index for index, action in enumerate(actions) if isinstance(action, tuple) and action[0] == "grab_key"]
                self.assertEqual(len(releases), len(bindings))
                self.assertEqual(len(restores), len(bindings))
                self.assertLess(max(releases), min(presses))
                self.assertLess(max(presses), min(restores))
                self.assertIn("sync", actions[max(presses) + 1:min(restores)])
                self.assertLess(max(restores), actions.index("ungrab_server"))

    def test_failed_paste_releases_keys_and_restores_every_passive_grab(self):
        backend, target, _child, _other = make_backend()
        bindings = {(55, X.ControlMask), (55, X.ControlMask | X.LockMask)}
        backend._bindings = bindings.copy()
        backend._root.grabbed = bindings.copy()
        send_input = backend._display.xtest_fake_input

        def fail_on_v_press(event_type, code):
            if event_type == X.KeyPress and code == 55:
                raise OSError("failed synthetic input")
            send_input(event_type, code)

        with patch.object(backend._display, "xtest_fake_input", side_effect=fail_on_v_press):
            self.assertFalse(backend.paste(target.id))
        self.assertEqual(backend._root.grabbed, bindings)
        self.assertEqual(backend._bindings, bindings)
        self.assertIn((X.KeyRelease, 37), backend._display.input)
        self.assertFalse(backend._display.server_grabbed)

    def test_passive_keyboard_grab_is_released_before_popup_callback(self):
        backend, _target, _child, _other = make_backend()
        backend._hotkey_keycode = 54  # C
        backend._grab_masks = {X.Mod1Mask}
        backend._display.events = [
            SimpleNamespace(type=X.KeyPress, detail=54, state=X.Mod1Mask, time=1234)
        ]
        backend.on_hotkey = lambda: backend._display.actions.append("popup")
        self.assertTrue(backend._on_io(None, 1))
        self.assertEqual(backend._display.actions, ["ungrab", "sync", "popup"])
        self.assertEqual(backend.last_event_time, 1234)

    def test_periodic_drain_handles_hotkey_already_buffered_inside_xlib(self):
        backend, _target, _child, _other = make_backend()
        backend._hotkey_keycode = 54
        backend._grab_masks = {X.Mod1Mask}
        backend._display.events = [
            SimpleNamespace(type=X.KeyPress, detail=54, state=X.Mod1Mask, time=5678)
        ]
        backend.on_hotkey = lambda: backend._display.actions.append("popup")
        # No fd callback occurs when a synchronous request already consumed
        # the socket bytes. The timer must still dispatch the queued event.
        self.assertTrue(backend._drain_pending_events())
        self.assertEqual(backend._display.actions, ["ungrab", "sync", "popup"])
        self.assertEqual(backend.last_event_time, 5678)

    def test_close_removes_both_event_sources(self):
        backend, _target, _child, _other = make_backend()
        with patch("jumpkut.x11.GLib.source_remove") as remove:
            backend.close()
        self.assertEqual(remove.call_args_list, [call(11), call(12)])
        self.assertIsNone(backend._watch_id)
        self.assertIsNone(backend._drain_id)
        self.assertTrue(backend._display.closed)
        self.assertFalse(backend._drain_pending_events())


if __name__ == "__main__":
    unittest.main()
