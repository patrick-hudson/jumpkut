"""X11 shortcuts, focus restoration, and optional XTEST paste support.

GTK owns the popup and clipboard; this separate X connection only handles the
desktop shortcut and returns paste input to the window that opened the popup.
"""

from __future__ import annotations

import logging
from itertools import product
from typing import Callable

from gi.repository import GLib
from Xlib import X, XK, Xatom, display, error, protocol
from Xlib.ext import xtest  # noqa: F401: registers the extension methods


XK.load_keysym_group("xkb")

_MODIFIER_NAMES = {
    "Control": ("Control_L", "Control_R"),
    "Alt": ("Alt_L", "Alt_R"),
    "Shift": ("Shift_L", "Shift_R"),
    "Super": ("Super_L", "Super_R"),
}
_LOCK_NAMES = ("Caps_Lock", "Shift_Lock", "Num_Lock", "Scroll_Lock")
_TERMINAL_CLASSES = frozenset(
    {
        "alacritty", "blackbox", "com.raggesilver.blackbox", "foot", "footclient",
        "gnome-terminal", "gnome-terminal-server", "kgx", "kitty", "konsole",
        "lxterminal", "mate-terminal", "org.gnome.console", "org.gnome.ptyxis",
        "org.gnome.terminal", "org.wezfurlong.wezterm", "ptyxis", "qterminal",
        "rxvt", "st", "st-256color", "terminator", "terminology", "tilix",
        "urxvt", "wezterm", "xfce4-terminal", "xterm",
    }
)
_LOG = logging.getLogger(__name__)


def _key_is_down(keymap: list[int], keycode: int) -> bool:
    return bool(keymap[keycode // 8] & (1 << (keycode % 8)))


def _is_terminal_class(wm_class: tuple[str, ...] | None) -> bool:
    return bool(wm_class and any(name.lower() in _TERMINAL_CLASSES for name in wm_class))


class X11Backend:
    """Integrate one global shortcut into the GTK main loop.

    ``modifiers_down`` checks the configured shortcut modifiers. ``paste`` also
    checks every physical modifier key, and the actual input focus, before
    synthesizing input. When XTEST is unavailable, callers can still copy the
    selected history entry and let the user paste it normally.
    """

    def __init__(self, key_name: str, modifiers: set[str], on_hotkey: Callable[[], None]):
        self.key_name = key_name
        self.modifiers = frozenset(modifiers)
        self.on_hotkey = on_hotkey
        self.last_event_time = X.CurrentTime
        self.errors: list[str] = []
        self.last_error: str | None = None
        self.has_paste = False
        self._display = None
        self._watch_id = None
        self._drain_id = None
        self._bindings: set[tuple[int, int]] = set()
        self._closed = False

        unsupported = self.modifiers - _MODIFIER_NAMES.keys()
        if unsupported:
            raise RuntimeError(f"Unsupported shortcut modifier: {', '.join(sorted(unsupported))}")
        try:
            self._display = display.Display()
        except (error.DisplayConnectionError, error.DisplayNameError, OSError) as exc:
            raise RuntimeError(f"Cannot connect to the X11 display: {exc}") from exc

        try:
            self._root = self._display.screen().root
            self._active_atom = self._display.intern_atom("_NET_ACTIVE_WINDOW")
            self.has_paste = self._display.has_extension("XTEST")
            self._register_shortcut()
            self._watch_id = GLib.io_add_watch(
                self._display.fileno(),
                GLib.PRIORITY_DEFAULT,
                GLib.IO_IN | GLib.IO_ERR | GLib.IO_HUP,
                self._on_io,
            )
            # Synchronous Xlib requests can read events into Xlib's own queue,
            # leaving the socket unreadable. The fd watch alone then misses
            # those events; a short timer drains them as well.
            self._drain_id = GLib.timeout_add(50, self._drain_pending_events)
        except Exception:
            self.close()
            raise

    def _record_error(self, message: str) -> None:
        self.last_error = message
        self.errors.append(message)
        _LOG.error(message)

    def _register_shortcut(self) -> None:
        """Re-read the keyboard map and atomically replace this client's grabs."""
        info = self._display.display.info
        rows = self._display.get_keyboard_mapping(
            info.min_keycode, info.max_keycode - info.min_keycode + 1
        )
        symbols = {info.min_keycode + offset: row for offset, row in enumerate(rows)}
        modifier_map = self._display.get_modifier_mapping()

        def codes_for(names: tuple[str, ...]) -> set[int]:
            wanted = {XK.string_to_keysym(name) for name in names}
            wanted.discard(X.NoSymbol)
            return {code for code, values in symbols.items() if wanted.intersection(values)}

        keysym = XK.string_to_keysym(self.key_name)
        keycode = self._display.keysym_to_keycode(keysym) if keysym else 0
        if not keycode:
            raise RuntimeError(f"The X11 keyboard has no key named {self.key_name!r}.")

        configured_codes: set[int] = set()
        possible_masks: list[set[int]] = []
        for name in sorted(self.modifiers):
            codes = codes_for(_MODIFIER_NAMES[name])
            masks = {1 << index for index, group in enumerate(modifier_map) if codes.intersection(group)}
            if not masks:
                raise RuntimeError(f"The X11 keyboard has no {name} modifier mapped.")
            configured_codes.update(codes)
            possible_masks.append(masks)
        base_masks = {0}
        for choices in possible_masks:
            base_masks = {base | choice for base, choice in product(base_masks, choices)}

        lock_codes = codes_for(_LOCK_NAMES)
        lock_masks = {1 << index for index, group in enumerate(modifier_map) if lock_codes.intersection(group)}
        combinations = {0}
        for lock in lock_masks:
            combinations.update(mask | lock for mask in tuple(combinations))
        grab_masks = {base | lock for base, lock in product(base_masks, combinations)}
        requested = {(keycode, mask) for mask in grab_masks}
        newly_added = requested - self._bindings
        catch = error.CatchError()
        for code, mask in newly_added:
            self._root.grab_key(code, mask, False, X.GrabModeAsync, X.GrabModeAsync, onerror=catch)
        self._display.sync()
        if catch.get_error() is not None:
            for code, mask in newly_added:
                self._root.ungrab_key(code, mask)
            self._display.sync()
            shortcut = "+".join(
                [name for name in _MODIFIER_NAMES if name in self.modifiers] + [self.key_name.upper()]
            )
            if isinstance(catch.get_error(), error.BadAccess):
                message = f"Cannot bind {shortcut}: another application already uses this global shortcut."
            else:
                message = f"Cannot bind {shortcut}: the X11 server rejected the shortcut ({catch.get_error()})."
            self._record_error(message)
            raise RuntimeError(message)

        for code, mask in self._bindings - requested:
            self._root.ungrab_key(code, mask)
        self._display.flush()
        self._bindings = requested
        self._hotkey_keycode = keycode
        self._grab_masks = grab_masks
        self._configured_keycodes = configured_codes
        self._all_modifier_keycodes = {code for group in modifier_map for code in group if code}
        self._control_keycode = self._display.keysym_to_keycode(XK.string_to_keysym("Control_L"))
        self._shift_keycode = self._display.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
        self._paste_keycode = self._display.keysym_to_keycode(XK.string_to_keysym("v"))

    def _on_io(self, _source, condition) -> bool:
        if condition & (GLib.IO_ERR | GLib.IO_HUP):
            self._record_error("The X11 display connection closed.")
            self._watch_id = None
            self.close()
            return False
        return self._drain_pending_events()

    def _drain_pending_events(self) -> bool:
        if self._closed:
            return False
        try:
            while not self._closed and self._display.pending_events():
                event = self._display.next_event()
                if event.type == X.MappingNotify:
                    self._display.refresh_keyboard_mapping(event)
                    try:
                        self._register_shortcut()
                    except RuntimeError as exc:
                        if str(exc) != self.last_error:
                            self._record_error(str(exc))
                elif (
                    event.type == X.KeyPress
                    and event.detail == self._hotkey_keycode
                    and (event.state & 0xFF) in self._grab_masks
                ):
                    self.last_event_time = event.time
                    # A passive root grab becomes an active keyboard grab on
                    # KeyPress. Release it before GTK tries to focus the popup.
                    self._display.ungrab_keyboard(event.time)
                    self._display.sync()
                    try:
                        self.on_hotkey()
                    except Exception as exc:
                        self._record_error(f"The shortcut handler failed: {exc}")
                        _LOG.exception("Shortcut handler exception")
        except (error.ConnectionClosedError, OSError) as exc:
            self._record_error(f"The X11 display connection failed: {exc}")
            self.close()
            return False
        return not self._closed

    def focused_window(self) -> int | None:
        """Return the WM's active application window, falling back to X focus."""
        if self._closed:
            return None
        try:
            active = self._root.get_full_property(self._active_atom, Xatom.WINDOW)
            if active is not None and len(active.value):
                window_id = int(active.value[0])
                if window_id > X.PointerRoot and window_id != self._root.id:
                    return window_id
            focus = self._display.get_input_focus().focus
            window_id = int(getattr(focus, "id", focus))
            if window_id > X.PointerRoot and window_id != self._root.id:
                return window_id
        except (error.XError, error.ConnectionClosedError, OSError):
            pass
        return None

    def modifiers_down(self) -> bool:
        """Check both left and right physical keys for the shortcut modifiers."""
        if self._closed:
            return False
        try:
            keymap = self._display.query_keymap()
            return any(_key_is_down(keymap, code) for code in self._configured_keycodes)
        except (error.XError, error.ConnectionClosedError, OSError):
            # A disconnected display cannot prove that the modifiers are up.
            return True

    def _focus_is_target(self, window_id: int) -> bool:
        """Accept input focus in a target's child, never a stale active property."""
        focus = self._display.get_input_focus().focus
        focus_id = int(getattr(focus, "id", focus))
        visited: set[int] = set()
        while focus_id > X.PointerRoot and focus_id not in visited:
            if focus_id == window_id:
                return True
            if focus_id == self._root.id:
                return False
            visited.add(focus_id)
            window = self._display.create_resource_object("window", focus_id)
            focus_id = int(window.query_tree().parent.id)
        return False

    def restore_focus(self, window_id: int | None) -> bool:
        """Ask the WM to activate a still-visible target and restore X focus."""
        if self._closed or not window_id or window_id <= X.PointerRoot or window_id == self._root.id:
            return False
        try:
            target = self._display.create_resource_object("window", window_id)
            if target.get_attributes().map_state != X.IsViewable:
                return False
            catch = error.CatchError()
            message = protocol.event.ClientMessage(
                window=window_id,
                client_type=self._active_atom,
                data=(32, [2, X.CurrentTime, 0, 0, 0]),
            )
            self._root.send_event(
                message, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask, onerror=catch
            )
            # CurrentTime matters: popup focus changes occur after the shortcut
            # timestamp, and X rejects focus requests with older timestamps.
            target.set_input_focus(X.RevertToParent, X.CurrentTime, onerror=catch)
            self._display.sync()
            return catch.get_error() is None and self._focus_is_target(window_id)
        except (error.XError, error.ConnectionClosedError, OSError):
            return False

    def paste(self, window_id: int | None) -> bool:
        """Paste into the original focused target when the physical modifiers are up."""
        if (
            self._closed or not self.has_paste or not window_id
            or window_id <= X.PointerRoot or window_id == self._root.id
        ):
            return False
        if not self._control_keycode or not self._paste_keycode:
            return False
        grabbed = False
        held: list[int] = []
        suspended_bindings: tuple[tuple[int, int], ...] = ()
        try:
            target = self._display.create_resource_object("window", window_id)
            terminal = _is_terminal_class(target.get_wm_class())
            if terminal and not self._shift_keycode:
                return False
            # Keep other clients' focus changes out of the check/injection
            # interval. This server grab lasts only these few X requests.
            self._display.grab_server()
            grabbed = True
            if not self._focus_is_target(window_id):
                return False
            keymap = self._display.query_keymap()
            if any(_key_is_down(keymap, code) for code in self._all_modifier_keycodes):
                return False
            keys = [self._control_keycode]
            if terminal:
                keys.append(self._shift_keycode)
            keys.append(self._paste_keycode)
            # The configured shortcut may itself be Ctrl+V or Ctrl+Shift+V.
            # Suspend our passive grabs so the synthetic paste reaches the
            # target. The server grab prevents anyone taking these bindings
            # before they are restored in finally.
            suspended_bindings = tuple(self._bindings)
            for code, mask in suspended_bindings:
                self._root.ungrab_key(code, mask)
            for code in keys:
                held.append(code)
                self._display.xtest_fake_input(X.KeyPress, code)
            for code in reversed(keys):
                self._display.xtest_fake_input(X.KeyRelease, code)
                held.pop()
            self._display.sync()
            return True
        except (error.XError, error.ConnectionClosedError, OSError):
            return False
        finally:
            if grabbed:
                for code in reversed(held):
                    try:
                        self._display.xtest_fake_input(X.KeyRelease, code)
                    except (error.XError, error.ConnectionClosedError, OSError):
                        pass
                try:
                    if suspended_bindings:
                        # Process every synthetic release before restoring a
                        # grab, including when injection failed partway.
                        self._display.sync()
                except (error.XError, error.ConnectionClosedError, OSError):
                    pass
                try:
                    for code, mask in suspended_bindings:
                        self._root.grab_key(code, mask, False, X.GrabModeAsync, X.GrabModeAsync)
                    if suspended_bindings:
                        self._display.sync()
                except (error.XError, error.ConnectionClosedError, OSError):
                    pass
                finally:
                    try:
                        self._display.ungrab_server()
                        self._display.flush()
                    except (error.XError, error.ConnectionClosedError, OSError):
                        pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._watch_id is not None:
            GLib.source_remove(self._watch_id)
            self._watch_id = None
        if self._drain_id is not None:
            GLib.source_remove(self._drain_id)
            self._drain_id = None
        if self._display is None:
            return
        try:
            for code, mask in self._bindings:
                self._root.ungrab_key(code, mask)
            self._display.ungrab_keyboard(X.CurrentTime)
            self._display.sync()
        except (error.XError, error.ConnectionClosedError, OSError):
            pass
        finally:
            self._bindings.clear()
            self._display.close()
