"""Native integration tests. Run only on a disposable X11 display/session bus.

JUMPKUT_DESKTOP_TEST=1 DISPLAY=:97 dbus-run-session -- python3 -m unittest discover -s tests -p test_desktop.py -v
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace


@unittest.skipUnless(os.environ.get("JUMPKUT_DESKTOP_TEST") == "1", "requires a disposable X11 desktop")
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("GdkX11", "3.0")
        from gi.repository import Gdk, GdkX11, GLib, Gtk
        from Xlib import X, XK, display
        from Xlib.ext import xtest
        from jumpkut.app import Jumpkut
        cls.Gdk, cls.Gtk, cls.GLib = Gdk, Gtk, GLib
        cls.X, cls.XK = X, XK
        cls.temporary = tempfile.TemporaryDirectory(prefix="jumpkut-desktop-")
        cls.root = Path(cls.temporary.name)
        os.environ["XDG_CONFIG_HOME"] = str(cls.root / "config")
        os.environ["XDG_DATA_HOME"] = str(cls.root / "data")
        cls.app = Jumpkut()
        cls.app.register(None)
        cls.display = display.Display()
        cls.editor = Gtk.Window(title="Jumpkut test editor")
        cls.editor.set_default_size(400, 180)
        cls.entry = Gtk.Entry()
        cls.editor.add(cls.entry)
        cls.editor.show_all()
        cls.pump(0.25)
        cls.editor_id = cls.editor.get_window().get_xid()
        if not cls.app.backend:
            raise RuntimeError("X11 backend did not initialize")

    @classmethod
    def tearDownClass(cls):
        cls.app.cancel_popup()
        if cls.app.history_window:
            cls.app.history_window.destroy()
        cls.app.do_shutdown()
        cls.editor.destroy()
        cls.display.close()
        cls.temporary.cleanup()

    @classmethod
    def pump(cls, seconds=0.12):
        deadline = time.monotonic() + seconds
        context = cls.GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.005)

    def setUp(self):
        self.owners = []
        self.app.cancel_popup()
        self.app._remove_source("_paste_source")
        if self.app.history_window:
            self.app.history_window.destroy()
        self.app.history.clear()
        self.entry.set_text("")
        if self.app.preferences:
            self.app.preferences.destroy()
        self.focus_editor()

    def tearDown(self):
        for owner in self.owners:
            owner.terminate()
            owner.wait(timeout=3)
            owner.stderr.close()
        for name in ("Alt_L", "Control_L", "Shift_L", "Super_L"):
            self.key(name, False)
        self.app.cancel_popup()
        self.app._remove_source("_paste_source")
        if self.app.history_window:
            self.app.history_window.destroy()
        self.app.save_preferences(replace(self.app.config.settings, hotkey="<Alt>c", sticky=False,
                                          history_limit=30, tray_limit=10,
                                          custom_quick_limit=False, custom_tray_limit=False,
                                          resume_last_selection=True), False)
        self.pump()

    def key(self, name, down):
        code = self.display.keysym_to_keycode(self.XK.string_to_keysym(name))
        self.display.xtest_fake_input(self.X.KeyPress if down else self.X.KeyRelease, code)
        self.display.sync()

    def tap(self, name):
        self.key(name, True)
        self.key(name, False)
        self.pump()

    def focus_editor(self):
        self.editor.present()
        self.entry.grab_focus()
        self.app.backend.restore_focus(self.editor_id)
        self.pump()

    def copy(self, text):
        program = "import gi,sys; gi.require_version('Gtk','3.0'); from gi.repository import Gtk,Gdk,GLib; Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(sys.argv[1],-1); GLib.MainLoop().run()"
        process = subprocess.Popen([sys.executable, "-c", program, text], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.owners.append(process)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.pump(0.05)
            if self.app.history.items and self.app.history.items[0].text == text:
                return
            if process.poll() is not None:
                self.fail(process.stderr.read().decode())
        self.fail(f"Clipboard copy was not captured: {text!r}")

    def open_hotkey(self, key="c"):
        self.key("Alt_L", True)
        self.tap(key)
        self.assertTrue(self.app.popup.get_visible(), "global shortcut must open history")
        self.assertIsNotNone(self.app.popup.keyboard_seat, "popup must capture Alt+Escape before the window manager")

    def screenshot(self, name, window):
        output = os.environ.get("JUMPKUT_SCREENSHOTS")
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            width, height = window.get_allocated_width(), window.get_allocated_height()
            pixbuf = self.Gdk.pixbuf_get_from_window(window.get_window(), 0, 0, width, height)
            pixbuf.savev(str(Path(output) / name), "png", [], [])

    def test_external_clipboard_capture_persistence_and_deduplication(self):
        self.copy("  café 🦊\nsecond line\t")
        self.copy("second clip")
        self.copy("  café 🦊\nsecond line\t")
        self.assertEqual([clip.text for clip in self.app.history.items], ["  café 🦊\nsecond line\t", "second clip"])
        saved = json.loads((self.root / "data/jumpkut/history.json").read_text())
        self.assertEqual(saved["clips"][0]["text"], "  café 🦊\nsecond line\t")

    def test_about_and_preferences_show_running_version(self):
        from jumpkut import __version__
        self.addCleanup(lambda: self.app.about.destroy() if self.app.about else None)
        self.app.tray.emit("activate")
        self.pump()
        menu = self.app._menu
        option = next(item for item in menu.get_children()
                      if isinstance(item, self.Gtk.MenuItem) and item.get_label() == "About Jumpkut")
        menu.popdown()
        option.activate()
        self.pump()
        about = self.app.about
        self.assertTrue(about.get_visible())
        self.assertEqual(about.get_version(), __version__)
        self.assertIs(about.get_application(), self.app)
        self.app.show_about()
        self.assertIs(self.app.about, about)
        self.assertIn(__version__, self.app.tray.get_tooltip_text())
        about.response(self.Gtk.ResponseType.CLOSE)
        self.pump()
        self.assertIsNone(self.app.about)
        self.app.show_popup()
        self.app.show_about()
        self.pump()
        self.assertFalse(self.app.popup.get_visible())
        self.app.about.response(self.Gtk.ResponseType.CLOSE)
        self.app.show_preferences()
        labels = [widget.get_text() for widget in self.app.preferences.get_content_area().get_children()
                  if isinstance(widget, self.Gtk.Label)]
        self.assertIn(f"Jumpkut {__version__}", labels)

    def test_alt_c_arrows_release_and_real_paste(self):
        older = "How many times have you needed to copy & paste multiple text items from a client's email or text document to place into a layout? Jumping back and forth between the design file and the client file can get extremely tedious. Enter Jumpkut."
        self.copy(older)
        self.copy("newer clipping")
        self.focus_editor()
        self.open_hotkey()
        self.tap("Down")
        self.assertEqual(self.app.popup.selected.text, older)
        self.tap("Up")
        self.assertEqual(self.app.popup.selected.text, "newer clipping")
        self.tap("c")
        self.assertEqual(self.app.popup.selected.text, older)
        self.screenshot("history.png", self.app.popup)
        self.key("Alt_L", False)
        self.pump(0.5)
        self.assertFalse(self.app.popup.get_visible())
        self.assertEqual(self.app.clipboard.wait_for_text(), older)
        self.assertEqual(self.entry.get_text(), older, "selected clipping must paste into original editor")
        self.assertEqual([clip.text for clip in self.app.history.items], ["newer clipping", older])

    def test_hotkey_resumes_last_selection_until_a_new_copy(self):
        self.copy("oldest copy")
        self.copy("older copy")
        self.copy("most recent copy")
        self.focus_editor()
        self.open_hotkey()
        self.assertEqual(self.app.popup.selected.text, "most recent copy")
        self.assertEqual(self.app.popup.counter.get_text(), "1")

        self.tap("c")
        self.assertEqual(self.app.popup.selected.text, "older copy")
        self.tap("Down")
        self.assertEqual(self.app.popup.selected.text, "oldest copy")
        self.tap("Up")
        self.assertEqual(self.app.popup.selected.text, "older copy")
        self.key("Alt_L", False)
        self.pump(0.5)
        self.assertEqual(self.entry.get_text(), "older copy")
        self.assertEqual([clip.text for clip in self.app.history.items],
                         ["most recent copy", "older copy", "oldest copy"],
                         "pasting an older clipping must preserve copy order")

        self.focus_editor()
        self.open_hotkey()
        self.assertEqual(self.app.popup.selected.text, "older copy")
        self.assertEqual(self.app.popup.counter.get_text(), "2")
        self.tap("Down")
        self.tap("Escape")
        self.key("Alt_L", False)
        self.pump()

        self.focus_editor()
        self.open_hotkey()
        self.assertEqual(self.app.popup.selected.text, "older copy")
        self.assertEqual(self.app.popup.counter.get_text(), "2")
        self.tap("Escape")
        self.key("Alt_L", False)
        self.pump()

        self.copy("oldest copy")
        self.focus_editor()
        self.open_hotkey()
        self.assertEqual(self.app.popup.selected.text, "oldest copy")
        self.assertEqual(self.app.popup.counter.get_text(), "1")
        self.tap("Escape")
        self.key("Alt_L", False)
        self.pump()

        self.copy("a newly copied clipping")
        self.focus_editor()
        self.open_hotkey()
        self.assertEqual(self.app.popup.selected.text, "a newly copied clipping")
        self.assertEqual(self.app.popup.counter.get_text(), "1")

    def test_resume_tracks_the_clipping_when_entries_are_deleted(self):
        for text in ("oldest", "remembered", "newest"):
            self.copy(text)
        self.focus_editor()
        self.open_hotkey()
        self.tap("Down")
        self.key("Alt_L", False)
        self.pump(0.35)
        self.app.remove_clip(self.app.history.items[0].id)
        self.focus_editor()
        self.open_hotkey()
        self.assertEqual(self.app.popup.selected.text, "remembered")
        self.assertEqual(self.app.popup.counter.get_text(), "1")
        self.tap("Escape")
        self.key("Alt_L", False)
        self.app.remove_clip(self.app.history.items[0].id)
        self.focus_editor()
        self.open_hotkey()
        self.assertEqual(self.app.popup.selected.text, "oldest")
        self.assertEqual(self.app.popup.counter.get_text(), "1")

    def test_resume_preference_and_quick_limit_fall_back_to_newest(self):
        for text in ("oldest", "remembered", "newest"):
            self.copy(text)
        self.focus_editor()
        self.open_hotkey()
        self.tap("Down")
        self.key("Alt_L", False)
        self.pump(0.35)
        self.app.show_preferences()
        self.pump()
        preferences = self.app.preferences
        self.assertTrue(preferences.resume.get_active())
        preferences.resume.set_active(False)
        preferences.response(self.Gtk.ResponseType.OK)
        self.assertFalse(self.app.config.settings.resume_last_selection)
        self.focus_editor()
        self.open_hotkey()
        self.assertEqual(self.app.popup.selected.text, "newest")
        self.tap("Escape")
        self.key("Alt_L", False)
        self.app.save_preferences(replace(self.app.config.settings, resume_last_selection=True,
                                          custom_quick_limit=True, history_limit=1), False)
        self.focus_editor()
        self.open_hotkey()
        self.assertEqual(self.app.popup.selected.text, "newest")
        self.assertEqual(len(self.app.history.items), 3)

    def test_escape_cancels_without_changing_clipboard(self):
        self.copy("older")
        self.copy("current")
        self.focus_editor()
        self.open_hotkey()
        self.tap("Down")
        self.tap("Escape")
        self.key("Alt_L", False)
        self.pump()
        self.assertFalse(self.app.popup.get_visible())
        self.assertEqual(self.app.clipboard.wait_for_text(), "current")
        self.assertEqual(self.entry.get_text(), "")

    def test_sticky_mode_selects_with_enter(self):
        self.copy("older sticky")
        self.copy("newer sticky")
        self.app.save_preferences(replace(self.app.config.settings, sticky=True), False)
        self.focus_editor()
        self.open_hotkey()
        self.key("Alt_L", False)
        self.pump()
        self.assertTrue(self.app.popup.get_visible())
        self.tap("Down")
        self.tap("Return")
        self.pump(0.35)
        self.assertEqual(self.entry.get_text(), "older sticky")

    def test_preferences_record_rebind_limit_and_startup(self):
        self.copy("first")
        self.copy("second")
        self.app.show_preferences()
        self.pump()
        preferences = self.app.preferences
        self.app.backend.restore_focus(preferences.get_window().get_xid())
        self.pump()
        preferences.hotkey.clicked()
        self.key("Alt_L", True)
        self.tap("x")
        self.key("Alt_L", False)
        self.pump()
        self.assertEqual(preferences.shortcut, "<Alt>x")
        preferences.custom_quick.set_active(True)
        preferences.limit.set_value(1)
        preferences.startup.set_active(True)
        self.screenshot("preferences.png", preferences)
        preferences.response(self.Gtk.ResponseType.OK)
        self.pump()
        self.assertEqual(self.app.config.settings.hotkey, "<Alt>x")
        self.assertEqual(len(self.app.history.items), 2, "quick length must not remove saved clippings")
        self.assertTrue((self.root / "config/autostart/jumpkut.desktop").exists())
        self.focus_editor()
        self.key("Alt_L", True)
        self.tap("c")
        self.assertFalse(self.app.popup.get_visible(), "old shortcut must be released")
        self.tap("x")
        self.assertTrue(self.app.popup.get_visible(), "new shortcut must be bound immediately")
        self.assertEqual([clip.text for clip in self.app.popup.clips], ["second"])
        self.tap("Escape")
        self.key("Alt_L", False)

    def test_single_instance_cli_history_and_quick_popup(self):
        self.copy("clipping for the history page")
        history_window = None
        for command in ("--history", "--history", "--show"):
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1] / "run.py"), command], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 5
            while process.poll() is None and time.monotonic() < deadline:
                self.pump(0.05)
            if process.poll() is None:
                process.kill()
                process.wait()
                self.fail(f"{command} did not forward to the existing instance")
            self.assertEqual(process.returncode, 0, process.stderr.read().decode())
            process.stdout.close()
            process.stderr.close()
            if command == "--history":
                self.assertTrue(self.app.history_window.get_visible())
                self.assertFalse(self.app.popup.get_visible())
                if history_window is not None:
                    self.assertIs(self.app.history_window, history_window, "reopening full history must reuse its window")
                history_window = self.app.history_window
            else:
                self.assertTrue(self.app.popup.get_visible(), "--show must keep opening the quick picker")
                self.assertIs(self.app.history_window, history_window)

    def test_hotkey_with_caps_lock_and_bind_collision(self):
        from jumpkut.x11 import X11Backend
        with self.assertRaisesRegex(RuntimeError, "another application"):
            X11Backend("c", {"Alt"}, lambda: None)
        self.copy("locked")
        self.focus_editor()
        self.tap("Caps_Lock")
        try:
            self.open_hotkey()
            self.tap("Escape")
            self.key("Alt_L", False)
        finally:
            self.tap("Caps_Lock")

    def test_tray_click_shows_short_history_and_normal_options(self):
        self.app.save_preferences(replace(self.app.config.settings, history_limit=1, custom_quick_limit=True), False)
        for index in range(12):
            self.copy(f"clipping {index}")
        self.focus_editor()
        self.app.tray.emit("activate")
        self.pump()
        menu = self.app._menu
        labels = [item.get_label() for item in menu.get_children() if isinstance(item, self.Gtk.MenuItem)]
        self.assertIn("1.  clipping 11", labels)
        self.assertIn("10.  clipping 2", labels)
        self.assertNotIn("11.  clipping 1", labels)
        for option in ("Show Full History", "Preferences…", "Pause Recording", "Back Up History…", "Clear History…", "Quit"):
            self.assertIn(option, labels)
        self.screenshot("tray-menu.png", menu)
        menu.popdown()
        menu.get_children()[2].activate()
        self.pump(0.35)
        self.assertEqual(self.app.clipboard.wait_for_text(), "clipping 10")
        self.assertEqual(self.entry.get_text(), "clipping 10")
        self.app.save_preferences(replace(self.app.config.settings, custom_quick_limit=False), False)
        self.focus_editor()
        self.open_hotkey()
        self.assertEqual(self.app.popup.selected.text, "clipping 10")
        self.assertEqual(self.app.popup.counter.get_text(), "2")

    def test_tray_full_history_browses_all_clippings_and_copies_exact_text(self):
        self.app.save_preferences(replace(self.app.config.settings, history_limit=3, custom_quick_limit=True), False)
        long_text = "Project notebook · café 🦊\n" + "A full page keeps every paragraph intact.\n" * 20 + "  Last line\t "
        for index in range(12):
            self.copy(long_text if index == 4 else f"Saved clipping {index:02d}\nNotes from the clipboard")
        original_order = [clip.id for clip in self.app.history.items]
        self.focus_editor()
        self.app.tray.emit("activate")
        self.pump()
        menu = self.app._menu
        option = next(item for item in menu.get_children()
                      if isinstance(item, self.Gtk.MenuItem) and item.get_label() == "Show Full History")
        menu.popdown()
        option.activate()
        self.pump()

        window = self.app.history_window
        self.assertTrue(window.get_visible())
        self.assertTrue(window.get_decorated())
        self.assertTrue(window.get_resizable())
        self.assertFalse(self.app.popup.get_visible(), "full history must open a separate browsing window")
        self.assertEqual([row[0] for row in window.model], original_order,
                         "full history must include clippings beyond the quick history limit")

        self.app.backend.restore_focus(window.get_window().get_xid())
        window.tree.grab_focus()
        window.tree.set_cursor(self.Gtk.TreePath.new_from_indices([0]))
        self.tap("Down")
        self.assertEqual(window.selected.id, original_order[1], "arrow keys must browse the history list")
        target = next(clip for clip in self.app.history.items if clip.text == long_text)
        target_row = next(row for row in window.model if row[0] == target.id)
        window.tree.set_cursor(target_row.path)
        self.pump()
        buffer = window.preview.get_buffer()
        self.assertEqual(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True), long_text)
        self.screenshot("full-history.png", window)

        window.copy_button.clicked()
        self.pump(0.35)
        self.assertEqual(self.app.clipboard.wait_for_text(), long_text)
        self.assertEqual(self.entry.get_text(), "", "copying from full history must not inject text into another app")
        self.assertTrue(window.get_visible(), "copying must keep the browsing window open")
        self.assertEqual([clip.id for clip in self.app.history.items], original_order)

        self.app.show_popup()
        self.pump()
        self.assertEqual(self.app.popup.counter.get_text(), "1", "a clipping outside the quick limit falls back to newest")
        self.app.cancel_popup(restore=False)
        remembered_row = next(row for row in window.model if row[0] == original_order[1])
        window.tree.set_cursor(remembered_row.path)
        window.copy_button.clicked()
        self.pump()
        self.app.show_popup()
        self.pump()
        self.assertEqual(self.app.popup.selected.id, original_order[1])
        self.assertEqual(self.app.popup.counter.get_text(), "2")
        self.app.cancel_popup(restore=False)

        window.search.set_text("Saved")
        self.pump(0.3)
        self.app.backend.restore_focus(window.get_window().get_xid())
        window.search.grab_focus()
        self.open_hotkey()
        self.tap("Down")
        self.key("Alt_L", False)
        self.pump(0.4)
        self.assertFalse(self.app.popup.get_visible())
        self.assertTrue(window.get_visible())
        self.assertEqual(window.search.get_text(), "Saved", "the quick picker must not paste into Jumpkut's own search")
        self.assertEqual(self.entry.get_text(), "")

    def test_full_history_search_live_selection_delete_and_empty_state(self):
        target_text = "Long clipping with a searchable ending\n" + "Repeated body text " * 60 + "\nTailNeedle CAFÉ 🦊"
        self.copy("oldest clipboard entry")
        self.copy(target_text)
        self.copy("most recent clipboard entry")
        target = next(clip for clip in self.app.history.items if clip.text == target_text)
        self.app.show_full_history()
        self.pump()
        window = self.app.history_window
        window.search.set_text("tailneedle café")
        self.pump(0.3)
        self.assertEqual([row[0] for row in window.model], [target.id],
                         "search must match text beyond its shortened list preview, ignoring case")
        self.assertEqual(window.selected.id, target.id)

        window.search.set_text("")
        self.pump(0.3)
        self.assertEqual(window.selected.id, target.id)
        self.copy("a clipping copied while browsing")
        self.assertEqual(len(window.model), 4)
        self.assertEqual(window.selected.id, target.id, "new clipboard entries must preserve the item being read")
        window.search.set_text("TAILNEEDLE")
        self.pump(0.3)
        window.delete_button.clicked()
        self.pump()
        self.assertIsNone(self.app.history.get(target.id))
        self.assertEqual(len(window.model), 0)
        self.assertIsNone(window.selected)
        self.assertFalse(window.copy_button.get_sensitive())
        self.assertFalse(window.delete_button.get_sensitive())
        buffer = window.preview.get_buffer()
        empty_preview = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        self.assertNotIn("TailNeedle", empty_preview, "deleting the last match must clear its old preview")
        self.assertIn("No clippings match", empty_preview)

        window.search.set_text("")
        self.pump(0.3)
        self.assertEqual(len(window.model), 3, "clearing a search must restore the remaining clippings")
        for remaining in range(2, -1, -1):
            window.delete_button.clicked()
            self.pump()
            self.assertEqual(len(window.model), remaining)
        self.assertEqual(self.app.history.items, [])
        self.assertIsNone(window.selected)
        self.assertFalse(window.copy_button.get_sensitive())
        self.assertFalse(window.delete_button.get_sensitive())
        self.assertIn("0", window.count.get_text())
        self.assertIn("history is empty", buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True))

    def test_backup_dialog_owns_focus_without_pasting_into_filename(self):
        self.copy("older clipboard text that must not become a filename")
        self.copy("newest clipboard text")
        self.app.show_full_history()
        self.pump()
        window = self.app.history_window
        errors = []
        handled = []
        deadline = time.monotonic() + 5

        def entries(widget):
            if isinstance(widget, self.Gtk.Entry):
                yield widget
            if isinstance(widget, self.Gtk.Container):
                for child in widget.get_children():
                    yield from entries(child)

        def exercise_dialog():
            dialog = next((candidate for candidate in self.app.get_windows()
                           if isinstance(candidate, self.Gtk.FileChooserDialog) and candidate.get_mapped()), None)
            if dialog is None:
                if time.monotonic() < deadline:
                    return self.GLib.SOURCE_CONTINUE
                errors.append(AssertionError("The backup chooser did not appear among Jumpkut's owned windows"))
                # Cancel even an incorrectly unowned dialog so a regression cannot hang dialog.run().
                for candidate in self.Gtk.Window.list_toplevels():
                    if isinstance(candidate, self.Gtk.FileChooserDialog):
                        candidate.response(self.Gtk.ResponseType.CANCEL)
                return self.GLib.SOURCE_REMOVE
            try:
                handled.append(dialog)
                self.assertIs(dialog.get_application(), self.app)
                filename = "clipboard-backup-regression.json"
                dialog.set_current_name(filename)
                self.pump()
                entry = next((candidate for candidate in entries(dialog)
                              if candidate.get_mapped() and candidate.get_text() == filename), None)
                self.assertIsNotNone(entry, "the real save dialog must expose its filename entry")
                entry.grab_focus()
                self.app.backend.restore_focus(dialog.get_window().get_xid())
                self.pump()
                self.key("Alt_L", True)
                self.tap("c")
                if self.app.popup.get_visible():
                    self.tap("Down")
                    expected_text = self.app.popup.selected.text
                    self.key("Alt_L", False)
                    self.pump(0.4)
                    self.assertFalse(self.app.popup.get_visible())
                else:
                    # GTK may block the picker while its native modal chooser is active.
                    self.key("Alt_L", False)
                    selected_clip = self.app.history.items[1]
                    expected_text = selected_clip.text
                    self.app.select_clip(selected_clip, dialog.get_window().get_xid())
                    self.pump(0.4)
                self.assertEqual(self.app.clipboard.wait_for_text(), expected_text)
                self.assertEqual(dialog.get_current_name(), filename,
                                 "Alt+C must not paste a clipping into Jumpkut's backup filename")
                self.assertEqual(entry.get_text(), filename)
            except Exception as exc:
                errors.append(exc)
            finally:
                self.key("Alt_L", False)
                self.app.cancel_popup(restore=False)
                dialog.response(self.Gtk.ResponseType.CANCEL)
            return self.GLib.SOURCE_REMOVE

        self.GLib.timeout_add(50, exercise_dialog)
        self.app.export_history(window)
        if errors:
            raise errors[0]
        self.assertEqual(len(handled), 1)
        self.assertTrue(window.get_visible())

    def test_quick_length_changes_recent_views_without_trimming_full_history(self):
        for index in range(12):
            self.copy(f"retained clipping {index}")
        original_order = [clip.id for clip in self.app.history.items]
        self.app.show_full_history()
        self.pump()
        window = self.app.history_window
        for limit in (1, 3, 10, 2, 12):
            with self.subTest(limit=limit):
                self.app.save_preferences(replace(self.app.config.settings, history_limit=limit, custom_quick_limit=True), False)
                self.assertEqual([clip.id for clip in self.app.history.items], original_order)
                self.assertEqual([row[0] for row in window.model], original_order)
                self.app.show_popup()
                self.pump()
                self.assertEqual(len(self.app.popup.clips), limit)
                self.assertEqual(self.app.popup.selected.text, "retained clipping 11")
                self.assertEqual(self.app.popup.counter.get_text(), "1")
                self.tap("End")
                self.assertEqual(self.app.popup.selected.text, f"retained clipping {12 - limit}")
                self.tap("Down")
                self.assertEqual(self.app.popup.selected.text, "retained clipping 11")
                self.app.cancel_popup(restore=False)

                self.app.tray.emit("activate")
                self.pump()
                labels = [item.get_label() for item in self.app._menu.get_children()
                          if isinstance(item, self.Gtk.MenuItem)]
                self.assertIn("10.  retained clipping 2", labels)
                self.assertEqual(len([label for label in labels if label and label[0].isdigit()]), 10,
                                 "changing quick length must not change the tray")
                self.assertIn("Show Full History", labels)
                self.app._menu.popdown()

        window.search.set_text("retained clipping 0")
        self.pump(0.3)
        self.assertEqual(len(window.model), 1, "old clippings stay searchable")

    def test_custom_history_checkboxes_apply_independent_defaults_and_remember_values(self):
        for index in range(35):
            self.copy(f"custom lengths clipping {index}")

        def assert_view_lengths(quick, tray):
            self.app.show_popup()
            self.pump()
            self.assertEqual(len(self.app.popup.clips), quick)
            self.assertEqual(self.app.popup.selected.text, "custom lengths clipping 34")
            self.app.cancel_popup(restore=False)
            self.app.tray.emit("activate")
            self.pump()
            labels = [item.get_label() for item in self.app._menu.get_children()
                      if isinstance(item, self.Gtk.MenuItem)]
            self.assertEqual(len([label for label in labels if label and label[0].isdigit()]), tray)
            self.app._menu.popdown()
            self.assertEqual(len(self.app.history), 35)

        assert_view_lengths(30, 10)
        self.app.show_preferences()
        self.pump()
        preferences = self.app.preferences
        self.assertFalse(preferences.custom_quick.get_active())
        self.assertFalse(preferences.custom_tray.get_active())
        self.assertFalse(preferences.limit.get_sensitive())
        self.assertFalse(preferences.tray_limit.get_sensitive())
        self.assertEqual(preferences.limit.get_value_as_int(), 30)
        self.assertEqual(preferences.tray_limit.get_value_as_int(), 10)
        self.screenshot("preferences-defaults.png", preferences)

        preferences.custom_quick.set_active(True)
        preferences.custom_tray.set_active(True)
        self.assertTrue(preferences.limit.get_sensitive())
        self.assertTrue(preferences.tray_limit.get_sensitive())
        preferences.limit.set_value(4)
        preferences.tray_limit.set_value(2)
        preferences.response(self.Gtk.ResponseType.OK)
        self.pump()
        assert_view_lengths(4, 2)

        self.app.show_preferences()
        self.pump()
        self.app.preferences.custom_quick.set_active(False)
        self.assertFalse(self.app.preferences.limit.get_sensitive())
        self.app.preferences.response(self.Gtk.ResponseType.OK)
        self.pump()
        assert_view_lengths(30, 2)

        self.app.show_preferences()
        self.pump()
        self.assertEqual(self.app.preferences.limit.get_value_as_int(), 4)
        self.app.preferences.custom_tray.set_active(False)
        self.assertFalse(self.app.preferences.tray_limit.get_sensitive())
        self.app.preferences.response(self.Gtk.ResponseType.OK)
        self.pump()
        assert_view_lengths(30, 10)

        self.app.show_preferences()
        self.pump()
        preferences = self.app.preferences
        self.assertEqual(preferences.limit.get_value_as_int(), 4)
        self.assertEqual(preferences.tray_limit.get_value_as_int(), 2)
        preferences.custom_quick.set_active(True)
        preferences.custom_tray.set_active(True)
        preferences.response(self.Gtk.ResponseType.OK)
        self.pump()
        assert_view_lengths(4, 2)
        self.app.show_full_history()
        self.pump()
        self.assertEqual(len(self.app.history_window.model), 35)

    def test_quick_length_and_startup_save_without_rewriting_archive(self):
        from unittest.mock import patch
        self.copy("archived text")
        original_data = self.app.history.path.read_bytes()
        with patch.object(self.app.history, "_save", side_effect=OSError("test disk failure")):
            self.app.save_preferences(replace(self.app.config.settings, history_limit=5, custom_quick_limit=True), True)
        self.assertEqual(self.app.config.settings.history_limit, 5)
        self.assertTrue((self.root / "config/autostart/jumpkut.desktop").exists())
        self.assertEqual(self.app.history.path.read_bytes(), original_data)

    def test_paste_chord_can_be_the_configured_hotkey(self):
        self.copy("chosen text")
        self.app.save_preferences(replace(self.app.config.settings, hotkey="<Control>v"), False)
        self.focus_editor()
        self.key("Control_L", True)
        self.tap("v")
        self.assertTrue(self.app.popup.get_visible())
        self.key("Control_L", False)
        self.pump(0.4)
        self.assertEqual(self.entry.get_text(), "chosen text")
        self.assertFalse(self.app.popup.get_visible(), "simulated paste must not reopen history")

    def test_invalid_saved_hotkey_recovers_at_startup(self):
        configuration = self.root / "invalid-config/jumpkut/config.json"
        configuration.parent.mkdir(parents=True)
        original = '{"hotkey":"garbage"}'
        configuration.write_text(original)
        self.app.backend.close()
        self.app.backend = None
        bus_config = self.root / "child-bus.conf"
        bus_config.write_text('<busconfig><type>session</type><listen>unix:tmpdir=/tmp</listen><policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/><allow own="*"/></policy></busconfig>')
        program = "import gi,json; gi.require_version('Gtk','3.0'); gi.require_version('Gdk','3.0'); gi.require_version('GdkX11','3.0'); from gi.repository import GdkX11; from jumpkut.app import Jumpkut; app=Jumpkut(); app.register(None); print(json.dumps({'popup':app.popup is not None,'tray':app.tray is not None,'backend':app.backend is not None,'hotkey':app.config.settings.hotkey})); app.do_shutdown()"
        environment = dict(os.environ, XDG_CONFIG_HOME=str(self.root / "invalid-config"), XDG_DATA_HOME=str(self.root / "invalid-data"))
        try:
            result = subprocess.run(["dbus-run-session", f"--config-file={bus_config}", "--", sys.executable, "-c", program], env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(result.stdout)
            self.assertEqual(state, {"popup": True, "tray": True, "backend": True, "hotkey": "<Alt>c"})
            self.assertEqual(configuration.read_text(), original)
        finally:
            self.app._bind_hotkey(self.app.config.settings.hotkey)


if __name__ == "__main__":
    unittest.main()
