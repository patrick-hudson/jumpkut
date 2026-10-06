"""Background launch lifecycle on the disposable X11 display and session bus."""

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

from install import install


@unittest.skipUnless(os.environ.get("JUMPKUT_DESKTOP_TEST") == "1", "requires a disposable X11 desktop")
class BackgroundTests(unittest.TestCase):
    def setUp(self):
        import gi
        from Xlib import X, display, error

        from gi.repository import Gio, GLib

        self.Gio, self.GLib = Gio, GLib
        self.X, self.BadWindow = X, error.BadWindow
        self.temporary = tempfile.TemporaryDirectory(prefix="jumpkut-background-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = dict(os.environ, **{
            f"XDG_{kind}_HOME": str(self.root / kind.lower())
            for kind in ("CONFIG", "DATA", "STATE")
        })
        self.launcher = install(self.root / "installation with spaces")
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.display = display.Display()
        self.addCleanup(self.display.close)
        self.addCleanup(self._stop_children)

    def _dbus(self, method, name):
        return self.bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", method, self.GLib.Variant("(s)", (name,)),
            None, self.Gio.DBusCallFlags.NONE, 1000, None,
        ).unpack()[0]

    def _owner(self):
        name = "io.github.jumpkut.Clipboard"
        if not self._dbus("NameHasOwner", name):
            return None
        try:
            return self._dbus("GetNameOwner", name)
        except self.GLib.Error as error:
            if self.Gio.DBusError.get_remote_error(error) == "org.freedesktop.DBus.Error.NameHasNoOwner":
                return None
            raise

    def _wait_for(self, condition, message):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = condition()
            if result:
                return result
            time.sleep(0.05)
        self.fail(message)

    def _launch(self, option=None):
        result = subprocess.run(
            [str(self.launcher), *([option] if option else [])], cwd=self.root,
            env=self.environment, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    @staticmethod
    def _alive(pid):
        try:
            return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] != "Z"
        except FileNotFoundError:
            return False

    def _children(self):
        marker = f"XDG_STATE_HOME={self.environment['XDG_STATE_HOME']}".encode()
        children = set()
        for process in Path("/proc").iterdir():
            if not process.name.isdigit():
                continue
            try:
                if process.stat().st_uid != os.getuid():
                    continue
                arguments = (process / "cmdline").read_bytes().split(b"\0")
                if b"jumpkut" not in arguments or b"--daemon" not in arguments:
                    continue
                if marker in (process / "environ").read_bytes().split(b"\0"):
                    pid = int(process.name)
                    if self._alive(pid):
                        children.add(pid)
            except (OSError, ProcessLookupError):
                continue
        return children

    def _stop_children(self):
        # The unique state directory identifies only children from this test,
        # including a child that failed before acquiring the application name.
        for pid in self._children():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 2
        while self._children() and time.monotonic() < deadline:
            time.sleep(0.05)
        for pid in self._children():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _window_visible(self, pid, expected_title):
        clients = self.display.screen().root.get_full_property(
            self.display.intern_atom("_NET_CLIENT_LIST"), self.X.AnyPropertyType,
        )
        for window_id in clients.value if clients else ():
            window = self.display.create_resource_object("window", int(window_id))
            try:
                process = window.get_full_property(self.display.intern_atom("_NET_WM_PID"), self.X.AnyPropertyType)
                title = window.get_full_property(self.display.intern_atom("_NET_WM_NAME"), self.X.AnyPropertyType)
                if (process is not None and int(process.value[0]) == pid
                        and title is not None and bytes(title.value).decode("utf-8") == expected_title
                        and window.get_attributes().map_state == self.X.IsViewable):
                    return True
            except self.BadWindow:
                continue
        return False

    def test_plain_command_detaches_and_preserves_single_instance_commands(self):
        self.assertIsNone(self._owner(), "the previous desktop suite must release the application name")
        self._launch()
        owner = self._wait_for(self._owner, "background child did not acquire the application name")
        pid = self._dbus("GetConnectionUnixProcessID", owner)
        self.assertEqual(os.getsid(pid), pid)
        self.assertNotEqual(os.getsid(pid), os.getsid(0))
        stat = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        self.assertEqual(stat[4], "0", "background child must have no controlling terminal")
        self.assertEqual(os.readlink(f"/proc/{pid}/fd/0"), "/dev/null")
        log = self.root / "state/jumpkut/jumpkut.log"
        for descriptor in (1, 2):
            self.assertEqual(os.readlink(f"/proc/{pid}/fd/{descriptor}"), str(log))
        popup_title = "Jumpkut — Clipboard history"
        self.assertFalse(self._window_visible(pid, popup_title))

        self._launch()
        self._wait_for(lambda: self._children() == {pid}, "repeated launch left another daemon running")
        self.assertEqual(self._owner(), owner)
        self.assertFalse(self._window_visible(pid, popup_title))

        desktop = self.launcher.parent.parent / "share/applications/jumpkut.desktop"
        menu_application = self.Gio.DesktopAppInfo.new_from_filename(str(desktop))
        self.assertIsNotNone(menu_application)
        self.assertTrue(menu_application.launch([], None))
        self._wait_for(
            lambda: self._window_visible(pid, "Jumpkut — Full history"),
            "application-menu launch did not open the full history window",
        )
        self.assertEqual(self._owner(), owner)
        self.assertTrue(menu_application.launch([], None))
        self._wait_for(lambda: self._children() == {pid}, "repeated menu launch left another daemon running")
        self.assertEqual(self._owner(), owner)
        self._launch("--show")
        self._wait_for(lambda: self._window_visible(pid, popup_title), "--show did not open the background instance's popup")
        self.assertEqual(self._owner(), owner)
        self._launch("--quit")
        self._wait_for(lambda: self._owner() is None and not self._alive(pid), "--quit did not stop the background instance")


if __name__ == "__main__":
    unittest.main()
