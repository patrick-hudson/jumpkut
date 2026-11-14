"""KDE Plasma Wayland integration: shortcut, clipboard, focus, and paste.

GTK still runs through XWayland, which keeps the tray icon and the popup
working. KWin does not let XWayland clients see global keys or clipboard
changes while they are unfocused, so this backend uses session services:

* KGlobalAccel owns the global shortcut. It also appears in System Settings.
* ``wl-paste --watch`` (ext-data-control) records clipboard changes, and
  ``wl-copy`` publishes the chosen clipping to every Wayland and X11 app.
* A small KWin script reports the active window, like ``_NET_ACTIVE_WINDOW``.
* The RemoteDesktop portal types the paste keystroke after one approval.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
import re
from typing import Callable

from gi.repository import Gdk, Gio, GLib

from .settings import _atomic_write, _xdg_root


_LOG = logging.getLogger(__name__)

_KGLOBALACCEL = "org.kde.kglobalaccel"
_KWIN = "org.kde.KWin"
_PORTAL = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_REMOTE_DESKTOP = "org.freedesktop.portal.RemoteDesktop"
_WINDOWS_PATH = "/io/github/jumpkut/KWinWindows"
_WINDOWS_INTERFACE = "io.github.jumpkut.KWinWindows"
_SCRIPT_NAME = "jumpkut-active-window"

# KGlobalAccel::SetShortcutFlag values.
_IS_DEFAULT = 1
_SET_PRESENT = 2
_NO_AUTOLOADING = 4

_QT_MODIFIERS = {"Shift": 0x02000000, "Control": 0x04000000, "Alt": 0x08000000, "Super": 0x10000000}
_QT_KEYS = {
    "Escape": 0x01000000, "Tab": 0x01000001, "ISO_Left_Tab": 0x01000002, "BackSpace": 0x01000003,
    "Return": 0x01000004, "KP_Enter": 0x01000005, "Insert": 0x01000006, "Delete": 0x01000007,
    "Pause": 0x01000008, "Print": 0x01000009, "Home": 0x01000010, "End": 0x01000011,
    "Left": 0x01000012, "Up": 0x01000013, "Right": 0x01000014, "Down": 0x01000015,
    "Page_Up": 0x01000016, "Page_Down": 0x01000017, "Menu": 0x01000055, "space": 0x20,
}
_GDK_MODIFIER_MASKS = {
    "Shift": Gdk.ModifierType.SHIFT_MASK,
    "Control": Gdk.ModifierType.CONTROL_MASK,
    "Alt": Gdk.ModifierType.MOD1_MASK,
    "Super": Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK,
}
_KEYSYMS = {"Control_L": 0xFFE3, "Shift_L": 0xFFE1, "v": 0x0076}
_TERMINAL_CLASSES = frozenset(
    {
        "alacritty", "blackbox", "com.mitchellh.ghostty", "com.raggesilver.blackbox", "foot",
        "footclient", "ghostty", "gnome-terminal", "gnome-terminal-server", "kgx", "kitty",
        "konsole", "lxterminal", "mate-terminal", "org.gnome.console", "org.gnome.ptyxis",
        "org.gnome.terminal", "org.kde.konsole", "org.kde.yakuake", "org.wezfurlong.wezterm",
        "ptyxis", "qterminal", "rxvt", "st", "st-256color", "terminator", "terminology", "tilix",
        "urxvt", "wezterm", "xfce4-terminal", "xterm", "yakuake",
    }
)

# Runs once per clipboard change, with the text on stdin. Base64 keeps every
# clipping on one line, including multiline text.
_WATCH_SCRIPT = r"""state=${CLIPBOARD_STATE:-data}
if [ "$state" = data ]; then
  case $(wl-paste --list-types 2>/dev/null) in
    *x-kde-passwordManagerHint*|*application/x-nspasteboard-concealed-type*|*application/x-keepassxc-secret*) state=sensitive ;;
  esac
fi
if [ "$state" = data ]; then printf 'data '; base64 -w0; else printf '%s' "$state"; cat >/dev/null; fi
printf '\n'
"""

_ACTIVE_WINDOW_SCRIPT = """
function report(w) {
  callDBus(%(service)s, %(path)s, %(interface)s, "Activated",
           w ? String(w.internalId) : "",
           w ? String(w.resourceClass || "") : "",
           w && w.windowId ? String(w.windowId) : "",
           w ? String(w.pid || 0) : "0");
}
workspace.windowActivated.connect(report);
report(workspace.activeWindow);
"""

_ACTIVATE_SCRIPT = """
const target = %(target)s;
for (const w of workspace.windowList()) {
  if (String(w.internalId) === target) { workspace.activeWindow = w; break; }
}
"""

_WINDOWS_XML = f"""<node><interface name="{_WINDOWS_INTERFACE}">
<method name="Activated"><arg type="s"/><arg type="s"/><arg type="s"/><arg type="s"/></method>
</interface></node>"""


def qt_key_code(key_name: str, modifiers) -> int:
    """Encode a GDK key name and modifier names as a Qt key combination."""
    match = re.fullmatch(r"F([1-9]|[12][0-9]|3[0-5])", key_name)
    if key_name in _QT_KEYS:
        key = _QT_KEYS[key_name]
    elif match:
        key = 0x01000030 + int(match.group(1)) - 1
    else:
        codepoint = Gdk.keyval_to_unicode(Gdk.keyval_from_name(key_name))
        if codepoint <= 0x20:
            raise RuntimeError(f"KDE global shortcuts cannot use the {key_name!r} key.")
        key = ord(chr(codepoint).upper()[0])
    unsupported = set(modifiers) - _QT_MODIFIERS.keys()
    if unsupported:
        raise RuntimeError(f"Unsupported shortcut modifier: {', '.join(sorted(unsupported))}")
    for name in modifiers:
        key |= _QT_MODIFIERS[name]
    return key


def is_terminal_class(resource_class: str | None) -> bool:
    return bool(resource_class and resource_class.lower() in _TERMINAL_CLASSES)


def parse_watch_line(line: str) -> tuple[str, str | None]:
    """Return ``(state, text)`` for one line written by ``_WATCH_SCRIPT``."""
    state, _, payload = line.partition(" ")
    if state != "data":
        return state, None
    try:
        return state, base64.b64decode(payload, validate=True).decode("utf-8", errors="replace")
    except ValueError:
        return "invalid", None


def _js_string(value: str) -> str:
    # JSON string syntax is valid JavaScript and escapes every quote.
    return json.dumps(value)


def _runtime_dir() -> Path:
    root = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(root) if root and Path(root).is_absolute() else Path(GLib.get_user_runtime_dir())
    path = base / "jumpkut"
    path.mkdir(mode=0o700, exist_ok=True)
    path.chmod(0o700)
    return path


def restore_token_path() -> Path:
    return _xdg_root("XDG_STATE_HOME", ".local/state") / "jumpkut/remote-desktop-token"


class WaylandClipboard:
    """Watch and set the Wayland clipboard through wl-clipboard."""

    def __init__(self, on_change: Callable[[str, str | None], None]):
        self.on_change = on_change
        self.last_state: tuple[str, str | None] | None = None
        self.pending_text: str | None = None
        self._process = None
        self._stream = None
        self._cancel = None
        self._restart_id = 0
        self._failures = 0
        self._closed = False
        self._start()

    def _start(self) -> None:
        self._restart_id = 0
        if self._closed:
            return
        self._cancel = Gio.Cancellable()
        self._process = Gio.Subprocess.new(
            ["wl-paste", "--type", "text", "--watch", "sh", "-c", _WATCH_SCRIPT],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
        )
        self._stream = Gio.DataInputStream.new(self._process.get_stdout_pipe())
        self._read()

    def _read(self) -> None:
        self._stream.read_line_async(GLib.PRIORITY_DEFAULT, self._cancel, self._line, None)

    def _line(self, stream, result, _data) -> None:
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if self._closed:
            return
        if line is None:
            self._restart()
            return
        self._failures = 0
        state, text = parse_watch_line(line)
        if state != "invalid":
            self.last_state = (state, text)
            if text is not None and text == self.pending_text:
                self.pending_text = None
            self.on_change(state, text)
        self._read()

    def _restart(self) -> None:
        """Restart a watcher that exited, for example after a compositor restart."""
        self._failures += 1
        if self._process:
            self._process.force_exit()
        delay = min(30, 2 ** min(self._failures, 5))
        _LOG.warning("wl-paste --watch stopped; restarting in %s s", delay)
        self._restart_id = GLib.timeout_add_seconds(delay, lambda: self._start() or GLib.SOURCE_REMOVE)

    def replay(self) -> None:
        if self.last_state:
            self.on_change(*self.last_state)

    def set_text(self, text: str) -> None:
        self.pending_text = text
        process = Gio.Subprocess.new(
            ["wl-copy", "--type", "text/plain;charset=utf-8"],
            Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
        )

        def done(proc, result, _data):
            try:
                proc.communicate_finish(result)
            except GLib.Error as exc:
                _LOG.error("wl-copy failed: %s", exc.message)
            # Do not wait forever for a watcher confirmation that may never come.
            GLib.timeout_add(1000, lambda: setattr(self, "pending_text", None) if self.pending_text == text else None)

        process.communicate_async(GLib.Bytes.new(text.encode("utf-8")), None, done, None)

    def close(self) -> None:
        self._closed = True
        if self._restart_id:
            GLib.source_remove(self._restart_id)
            self._restart_id = 0
        if self._cancel:
            self._cancel.cancel()
        if self._process:
            self._process.force_exit()


class _GlobalShortcut:
    """One KGlobalAccel action for the configured shortcut."""

    COMPONENT = "jumpkut"
    ACTION = "show-history"

    def __init__(self, bus, qt_key: int, label: str, on_pressed: Callable[[int], None]):
        self._bus = bus
        self._subscription = 0
        self._action = [self.COMPONENT, self.ACTION, "Jumpkut", "Show clipboard history"]
        keys = GLib.Variant("a(ai)", [([qt_key],)])
        self._call("doRegister", GLib.Variant("(as)", (self._action,)))
        available = self._call(
            "globalShortcutAvailable", GLib.Variant("((ai)s)", (([qt_key],), self.COMPONENT))
        )[0]
        if not available:
            # Entries are (action, action name, component, component name, ...).
            owners = self._call("globalShortcutsByKey", GLib.Variant("((ai)(i))", (([qt_key],), (0,))))[0]
            others = [info for info in owners if (info[2], info[0]) != (self.COMPONENT, self.ACTION)]
            if others or not owners:
                owner = f"{others[0][3]} ({others[0][1]})" if others else "another application"
                raise RuntimeError(f"Cannot bind {label}: {owner} already uses this global shortcut")
        self._call("setShortcutKeys", GLib.Variant.new_tuple(
            GLib.Variant("as", self._action), keys, GLib.Variant("u", _IS_DEFAULT | _NO_AUTOLOADING)))
        assigned = self._call("setShortcutKeys", GLib.Variant.new_tuple(
            GLib.Variant("as", self._action), keys, GLib.Variant("u", _SET_PRESENT | _NO_AUTOLOADING)))[0]
        if not any(qt_key in sequence[0] for sequence in assigned):
            raise RuntimeError(f"Cannot bind {label}: KDE did not accept the shortcut.")
        path = self._call("getComponent", GLib.Variant("(s)", (self.COMPONENT,)))[0]

        def pressed(_bus, _sender, _path, _interface, _signal, parameters, _data):
            _component, action, timestamp = parameters.unpack()
            if action == self.ACTION:
                on_pressed(int(timestamp))

        self._subscription = bus.signal_subscribe(
            _KGLOBALACCEL, "org.kde.kglobalaccel.Component", "globalShortcutPressed",
            path, None, Gio.DBusSignalFlags.NONE, pressed, None,
        )

    def _call(self, method, parameters):
        return self._bus.call_sync(
            _KGLOBALACCEL, "/kglobalaccel", "org.kde.KGlobalAccel", method, parameters,
            None, Gio.DBusCallFlags.NONE, 5000, None,
        ).unpack()

    def close(self) -> None:
        if self._subscription:
            self._bus.signal_unsubscribe(self._subscription)
            self._subscription = 0
        try:
            # Inactive actions keep their System Settings entry but stop firing.
            self._call("setInactive", GLib.Variant("(as)", (self._action,)))
        except GLib.Error:
            pass


class _ActiveWindows:
    """Track KWin's active window with a session-scoped KWin script."""

    def __init__(self, bus):
        self._bus = bus
        self.active: dict[str, str] = {}
        self.owners: dict[str, str] = {}  # Window id -> process id, recent windows only.
        self._registration = 0
        self._loaded = False
        owner = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetNameOwner",
            GLib.Variant("(s)", (_KWIN,)), GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 5000, None,
        ).unpack()[0]
        self._kwin_owner = owner
        info = Gio.DBusNodeInfo.new_for_xml(_WINDOWS_XML).interfaces[0]
        self._registration = bus.register_object(_WINDOWS_PATH, info, self._method, None, None)
        script = _runtime_dir() / "active-window.js"
        script.write_text(
            _ACTIVE_WINDOW_SCRIPT % {
                "service": _js_string(bus.get_unique_name()),
                "path": _js_string(_WINDOWS_PATH),
                "interface": _js_string(_WINDOWS_INTERFACE),
            },
            encoding="utf-8",
        )
        self._unload(_SCRIPT_NAME)
        script_id = self._scripting("loadScript", GLib.Variant("(ss)", (str(script), _SCRIPT_NAME)))[0]
        if script_id < 0:
            raise RuntimeError("KWin could not load the active-window script.")
        self._loaded = True
        self._run(script_id)

    def _scripting(self, method, parameters):
        return self._bus.call_sync(
            _KWIN, "/Scripting", "org.kde.kwin.Scripting", method, parameters,
            None, Gio.DBusCallFlags.NONE, 5000, None,
        ).unpack()

    def _run(self, script_id: int) -> None:
        # Run asynchronously: the script immediately calls back into this
        # process, which must be free to answer.
        self._bus.call(
            _KWIN, f"/Scripting/Script{script_id}", "org.kde.kwin.Script", "run", None,
            None, Gio.DBusCallFlags.NONE, 5000, None, None, None,
        )

    def _unload(self, name: str) -> None:
        try:
            self._scripting("unloadScript", GLib.Variant("(s)", (name,)))
        except GLib.Error:
            pass

    def _method(self, _bus, sender, _path, _interface, method, parameters, invocation):
        if sender != self._kwin_owner or method != "Activated":
            invocation.return_dbus_error("org.freedesktop.DBus.Error.AccessDenied", "Only KWin may report windows.")
            return
        window_id, resource_class, x11_id, pid = parameters.unpack()
        self.active = {"id": window_id, "class": resource_class, "x11": x11_id, "pid": pid} if window_id else {}
        if window_id:
            self.owners.pop(window_id, None)
            self.owners[window_id] = pid
            while len(self.owners) > 64:
                self.owners.pop(next(iter(self.owners)))
        invocation.return_value(None)

    def activate(self, window_id: str) -> None:
        name = f"jumpkut-activate-{os.getpid()}"
        script = _runtime_dir() / "activate.js"
        script.write_text(_ACTIVATE_SCRIPT % {"target": _js_string(window_id)}, encoding="utf-8")
        try:
            self._unload(name)
            script_id = self._scripting("loadScript", GLib.Variant("(ss)", (str(script), name)))[0]
            if script_id >= 0:
                self._bus.call_sync(
                    _KWIN, f"/Scripting/Script{script_id}", "org.kde.kwin.Script", "run", None,
                    None, Gio.DBusCallFlags.NONE, 5000, None,
                )
        except GLib.Error as exc:
            _LOG.warning("Could not activate the previous window: %s", exc.message)
        finally:
            self._unload(name)

    def close(self) -> None:
        if self._loaded:
            self._unload(_SCRIPT_NAME)
            self._loaded = False
        if self._registration:
            self._bus.unregister_object(self._registration)
            self._registration = 0


class _PortalKeyboard:
    """Type key taps through the RemoteDesktop portal.

    The first session shows KDE's permission dialog. Its restore token is kept
    so later sessions start silently.
    """

    def __init__(self, bus, on_error: Callable[[str], None]):
        self._bus = bus
        self._on_error = on_error
        self._session = None
        self._requests = 0
        self.ready = False
        self.starting = False

    def start(self) -> None:
        if self.ready or self.starting:
            return
        self.starting = True
        self._request("CreateSession", lambda token: GLib.Variant("(a{sv})", ({
            "handle_token": GLib.Variant("s", token),
            "session_handle_token": GLib.Variant("s", f"jumpkut{os.getpid()}"),
        },)), self._created)

    def _request(self, method, parameters, on_success) -> None:
        self._requests += 1
        token = f"jumpkut{os.getpid()}_{self._requests}"
        sender = self._bus.get_unique_name()[1:].replace(".", "_")
        path = f"{_PORTAL_PATH}/request/{sender}/{token}"
        subscription = 0

        def response(_bus, _sender, _path, _interface, _signal, values, _data):
            self._bus.signal_unsubscribe(subscription)
            code, results = values.unpack()
            if code == 0:
                on_success(results)
            else:
                self._fail("Auto-paste needs permission to type the paste shortcut. "
                           "The clipping was copied; paste it with Ctrl+V. "
                           "Turn Paste automatically off and on in Preferences to ask again."
                           if code == 1 else "The remote input portal could not start.")

        subscription = self._bus.signal_subscribe(
            _PORTAL, "org.freedesktop.portal.Request", "Response", path, None,
            Gio.DBusSignalFlags.NONE, response, None,
        )

        def called(bus, result, _data):
            try:
                bus.call_finish(result)
            except GLib.Error as exc:
                self._bus.signal_unsubscribe(subscription)
                self._fail(f"The remote input portal is unavailable: {exc.message}")

        self._bus.call(
            _PORTAL, _PORTAL_PATH, _REMOTE_DESKTOP, method, parameters(token),
            None, Gio.DBusCallFlags.NONE, -1, None, called, None,
        )

    def _created(self, results) -> None:
        self._session = results["session_handle"]
        options = {
            "handle_token": None,
            "types": GLib.Variant("u", 1),  # Keyboard only.
            "persist_mode": GLib.Variant("u", 2),  # Until the user revokes it.
        }
        token = self._load_token()
        if token:
            options["restore_token"] = GLib.Variant("s", token)

        def parameters(handle):
            options["handle_token"] = GLib.Variant("s", handle)
            return GLib.Variant("(oa{sv})", (self._session, options))

        self._request("SelectDevices", parameters, self._selected)

    def _selected(self, _results) -> None:
        self._request("Start", lambda token: GLib.Variant("(osa{sv})", (
            self._session, "", {"handle_token": GLib.Variant("s", token)})), self._started)

    def _started(self, results) -> None:
        self.starting = False
        if not int(results.get("devices", 0)) & 1:
            self._fail("The remote input portal did not grant keyboard access.")
            return
        self.ready = True
        token = results.get("restore_token")
        if token:
            try:
                _atomic_write(restore_token_path(), token, private_directory=True)
            except OSError as exc:
                _LOG.warning("Could not save the remote input permission: %s", exc)

    @staticmethod
    def _load_token() -> str | None:
        try:
            return restore_token_path().read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _fail(self, message: str) -> None:
        self.starting = False
        self.ready = False
        self._close_session()
        self._on_error(message)

    def tap(self, keysyms: list[int]) -> bool:
        if not self.ready:
            return False
        try:
            for keysym in keysyms:
                self._notify(keysym, 1)
            for keysym in reversed(keysyms):
                self._notify(keysym, 0)
        except GLib.Error as exc:
            for keysym in reversed(keysyms):
                try:
                    self._notify(keysym, 0)
                except GLib.Error:
                    pass
            self._fail(f"The remote input portal stopped: {exc.message}")
            return False
        return True

    def _notify(self, keysym: int, state: int) -> None:
        self._bus.call_sync(
            _PORTAL, _PORTAL_PATH, _REMOTE_DESKTOP, "NotifyKeyboardKeysym",
            GLib.Variant("(oa{sv}iu)", (self._session, {}, keysym, state)),
            None, Gio.DBusCallFlags.NONE, 2000, None,
        )

    def _close_session(self) -> None:
        if self._session:
            try:
                self._bus.call_sync(
                    _PORTAL, self._session, "org.freedesktop.portal.Session", "Close", None,
                    None, Gio.DBusCallFlags.NONE, 2000, None,
                )
            except GLib.Error:
                pass
            self._session = None

    def close(self) -> None:
        self.ready = False
        self._close_session()


class KdeWaylandBackend:
    """The X11Backend interface, implemented with KDE Plasma session services."""

    # KWin moves focus back to the previous window shortly after the popup hides.
    focus_may_lag = True
    # Modifier state is only current while one of our XWayland windows is focused.
    reads_modifiers_from_focus = True

    def __init__(self, key_name: str, modifiers: set[str], on_hotkey: Callable[[], None],
                 on_error: Callable[[str], None] | None = None):
        self.key_name = key_name
        self.modifiers = frozenset(modifiers)
        self.on_hotkey = on_hotkey
        self.on_error = on_error or (lambda message: _LOG.error(message))
        self.last_event_time = Gdk.CURRENT_TIME
        self.errors: list[str] = []
        self.last_error: str | None = None
        self._closed = False
        self._shortcut = None
        self._windows = None
        self._keyboard = None
        self._mask = 0
        for name in self.modifiers:
            self._mask |= int(_GDK_MODIFIER_MASKS[name]) if name in _GDK_MODIFIER_MASKS else 0
        label = "+".join(
            [name for name in ("Control", "Alt", "Shift", "Super") if name in self.modifiers] + [key_name.upper()]
        )
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._shortcut = _GlobalShortcut(self._bus, qt_key_code(key_name, self.modifiers), label, self._pressed)
            self._windows = _ActiveWindows(self._bus)
            self._keyboard = _PortalKeyboard(self._bus, self._keyboard_error)
        except GLib.Error as exc:
            self.close()
            raise RuntimeError(f"KDE session services are unavailable: {exc.message}") from exc
        except Exception:
            self.close()
            raise

    @property
    def has_paste(self) -> bool:
        return bool(self._keyboard and (self._keyboard.ready or self._keyboard.starting))

    def prepare_paste(self, enabled: bool) -> None:
        """Start (or stop) the input portal; the first start asks permission."""
        if not self._keyboard:
            return
        if enabled:
            self._keyboard.start()
        else:
            self._keyboard.close()

    def _keyboard_error(self, message: str) -> None:
        self.last_error = message
        self.errors.append(message)
        self.on_error(message)

    def _pressed(self, _timestamp: int) -> None:
        if self._closed:
            return
        # KGlobalAccel timestamps are not X server times. CURRENT_TIME lets
        # GTK use the newest server time when it presents the popup.
        self.last_event_time = Gdk.CURRENT_TIME
        try:
            self.on_hotkey()
        except Exception as exc:
            message = f"The shortcut handler failed: {exc}"
            self.last_error = message
            self.errors.append(message)
            _LOG.exception("Shortcut handler exception")

    def focused_window(self) -> str | None:
        if self._closed or not self._windows:
            return None
        return self._windows.active.get("id") or None

    def is_own_window(self, window_id) -> bool:
        owners = self._windows.owners if self._windows else {}
        return bool(window_id) and owners.get(window_id) == str(os.getpid())

    def modifiers_down(self) -> bool:
        """Read the configured modifiers; callers check that we have focus."""
        if self._closed:
            return False
        display = Gdk.Display.get_default()
        if display is None:
            return True
        state = int(Gdk.Keymap.get_for_display(display).get_modifier_state())
        return bool(state & self._mask)

    def restore_focus(self, window_id) -> bool:
        if self._closed or not window_id or not self._windows:
            return False
        if self.focused_window() == window_id:
            return True
        self._windows.activate(window_id)
        return False

    def paste(self, window_id) -> bool:
        """Type the paste shortcut into ``window_id`` when it is KWin's active window."""
        if self._closed or not window_id or not self._keyboard or not self._keyboard.ready:
            return False
        active = self._windows.active
        if active.get("id") != window_id or active.get("pid") == str(os.getpid()):
            return False
        keys = [_KEYSYMS["Control_L"]]
        if is_terminal_class(active.get("class")):
            keys.append(_KEYSYMS["Shift_L"])
        keys.append(_KEYSYMS["v"])
        return self._keyboard.tap(keys)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for part in (self._shortcut, self._windows, self._keyboard):
            if part is not None:
                try:
                    part.close()
                except GLib.Error:
                    pass
