"""Tray daemon, clipboard monitoring, and popup lifecycle."""

import signal
import sys
import time
from dataclasses import replace
from pathlib import Path

from gi.repository import Gdk, Gio, GLib, Gtk

from . import __version__
from .__main__ import parser
from .history import History
from .history_window import FullHistory
from .settings import Config, Settings, autostart_enabled, data_path, set_autostart
from .ui import Popup, Preferences, choose_backup
from .x11 import X11Backend


def parse_hotkey(shortcut):
    key, mask = Gtk.accelerator_parse(shortcut)
    names = {Gdk.ModifierType.CONTROL_MASK: "Control", Gdk.ModifierType.MOD1_MASK: "Alt", Gdk.ModifierType.SHIFT_MASK: "Shift", Gdk.ModifierType.SUPER_MASK: "Super"}
    supported = sum(int(flag) for flag in names)
    if not key or not mask or int(mask) & ~supported:
        raise ValueError("Use a shortcut with Alt, Control, Shift, or Super, for example <Alt>c.")
    key = Gdk.keyval_to_lower(key)
    return key, Gdk.keyval_name(key), {name for flag, name in names.items() if mask & flag}


class Jumpkut(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.jumpkut.Clipboard", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.backend = None
        self.popup = None
        self.preferences = None
        self.history_window = None
        self.about = None
        self.tray = None
        self.paused = False
        self._generation = 0
        self._ignore_text = None
        self._last_selected_clip_id = None
        self._previous_window = None
        self._release_source = 0
        self._paste_source = 0

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.Window.set_default_icon_from_file(str(Path(__file__).resolve().parent / "assets" / "jumpkut.svg"))
        self.hold()
        self.config = Config()
        self.history = History(data_path() if self.config.settings.persist else None)
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.clipboard.connect("owner-change", self._owner_changed)
        self._bind_hotkey(self.config.settings.hotkey)
        self.popup = Popup(self)
        self._make_tray()
        for problem in (self.config.load_error, self.history.load_error):
            if problem:
                self.notify(problem)
        for signum in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self._quit_signal)
        GLib.idle_add(self._capture_current)

    def do_command_line(self, command_line):
        options = parser().parse_args(command_line.get_arguments()[1:])
        if options.quit:
            self.quit()
        elif options.preferences:
            self.show_preferences()
        elif options.about:
            self.show_about()
        elif options.history:
            self.show_full_history()
        elif not options.daemon:
            self.show_popup(held=False)
        return 0

    def do_activate(self):
        self.show_popup(held=False)

    def do_shutdown(self):
        for attribute in ("_release_source", "_paste_source"):
            self._remove_source(attribute)
        if self.backend:
            self.backend.close()
        if self.tray:
            self.tray.set_visible(False)
        Gtk.Application.do_shutdown(self)

    def _quit_signal(self):
        self.quit()
        return GLib.SOURCE_REMOVE

    def _bind_hotkey(self, shortcut):
        try:
            self.hotkey_key, name, modifiers = parse_hotkey(shortcut)
        except ValueError:
            shortcut = Settings().hotkey
            self.config.settings = replace(self.config.settings, hotkey=shortcut)
            self.hotkey_key, name, modifiers = parse_hotkey(shortcut)
            self.notify("Invalid saved keyboard shortcut. Using Alt+C; the original preferences file is preserved until you save Preferences.")
        try:
            self.backend = X11Backend(name, modifiers, self._hotkey)
        except RuntimeError as exc:
            self.backend = None
            self.notify(f"Keyboard shortcut unavailable: {exc}. Open history from the tray or change the shortcut in Preferences.")

    def _hotkey(self):
        if self.preferences and self.preferences.recording_shortcut:
            self.preferences.set_shortcut(self.config.settings.hotkey)
        elif self.popup.get_visible():
            self.popup.move(1)
        elif not (self.preferences and self.preferences.get_visible()):
            self.show_popup(held=True)

    def _capture_current(self):
        self._owner_changed(self.clipboard, None)
        return GLib.SOURCE_REMOVE

    def _owner_changed(self, clipboard, _event):
        self._generation += 1
        generation = self._generation
        if self.paused:
            return

        def targets_received(_clipboard, targets, _count, _data):
            if generation != self._generation or self.paused:
                return
            sensitive = {"x-kde-passwordManagerHint", "application/x-nspasteboard-concealed-type", "application/x-keepassxc-secret"}
            if targets and any(atom.name() in sensitive for atom in targets):
                self._ignore_text = None
                return
            clipboard.request_text(text_received, None)

        def text_received(_clipboard, text, _data):
            if generation != self._generation or self.paused:
                return
            ignored = self._ignore_text
            self._ignore_text = None
            if text is None or text == ignored:
                return
            if text.strip():
                self._last_selected_clip_id = None
            try:
                self.history.add(text)
            except OSError as exc:
                self.notify(f"History is available in memory, but could not be saved: {exc}")
            self._history_changed()

        clipboard.request_targets(targets_received, None)

    def show_popup(self, held=False):
        self._remove_source("_paste_source")
        if self.popup.get_visible():
            self.popup.present()
            return
        self._previous_window = self.backend.focused_window() if self.backend else None
        release_selects = bool(held and not self.config.settings.sticky and self.backend)
        timestamp = self.backend.last_event_time if held and self.backend else Gtk.get_current_event_time()
        selected_id = self._last_selected_clip_id if self.config.settings.resume_last_selection else None
        self.popup.open(self.recent_clips(), release_selects, timestamp, selected_id)
        if release_selects:
            self._release_source = GLib.timeout_add(25, self._check_release)

    def show_full_history(self):
        self._remove_source("_paste_source")
        if self.popup.get_visible():
            self.cancel_popup(restore=False)
        if self.history_window is None:
            self.history_window = FullHistory(self)
        self.history_window.open()

    def show_about(self):
        self._remove_source("_paste_source")
        if self.popup.get_visible():
            self.cancel_popup(restore=False)
        if self.about is None:
            self.about = Gtk.AboutDialog(program_name="Jumpkut", version=__version__, logo_icon_name="jumpkut", license_type=Gtk.License.MIT_X11, comments="Clipboard history for Linux. Copy, browse, and paste with a keyboard shortcut.")
            self.about.set_application(self)
            self.about.connect("response", lambda dialog, _response: dialog.destroy())
            self.about.connect("destroy", lambda *_: setattr(self, "about", None))
        windows = (self.history_window, self.preferences)
        parent = self.get_active_window()
        if parent is None or parent not in windows or not parent.get_visible():
            parent = next((window for window in windows if window and window.get_visible()), None)
        self.about.set_transient_for(parent)
        self.about.show_all()
        self.about.present()

    def _check_release(self):
        if not self.popup.get_visible():
            self._release_source = 0
            return GLib.SOURCE_REMOVE
        if not self.backend.modifiers_down():
            self._release_source = 0
            self.accept_popup()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _remove_source(self, attribute):
        source = getattr(self, attribute)
        if source:
            GLib.source_remove(source)
            setattr(self, attribute, 0)

    def cancel_popup(self, restore=True):
        self._remove_source("_release_source")
        self.popup.hide()
        if restore and self.backend and self._previous_window:
            self.backend.restore_focus(self._previous_window)

    def accept_popup(self):
        selected = self.popup.selected
        self.cancel_popup(restore=selected is None)
        if not selected:
            return
        self.select_clip(selected, self._previous_window)

    def select_clip(self, selected, target):
        self._last_selected_clip_id = selected.id
        self._ignore_text = selected.text
        self.clipboard.set_text(selected.text, -1)
        self._remove_source("_paste_source")
        if self.backend and target:
            self.backend.restore_focus(target)
        own_window = any(window.get_window() and window.get_window().get_xid() == target for window in self.get_windows())
        if self.config.settings.auto_paste and self.backend and self.backend.has_paste and target and not own_window:
            deadline = time.monotonic() + 1.5

            def paste_when_ready():
                if self.backend.focused_window() != target:
                    self._paste_source = 0
                    return GLib.SOURCE_REMOVE
                if self.backend.paste(target) or time.monotonic() >= deadline:
                    self._paste_source = 0
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE

            self._paste_source = GLib.timeout_add(100, paste_when_ready)

    def remove_clip(self, clip_id):
        try:
            self.history.remove(clip_id)
        except OSError as exc:
            self.notify(str(exc))
        self._history_changed()

    def _history_changed(self):
        if self._last_selected_clip_id and self.history.get(self._last_selected_clip_id) is None:
            self._last_selected_clip_id = None
        self._update_tooltip()
        if self.history_window:
            self.history_window.refresh()

    def _make_tray(self):
        self.tray = Gtk.StatusIcon.new_from_file(str(Path(__file__).resolve().parent / "assets" / "jumpkut.svg"))
        self.tray.set_title("Jumpkut")
        self.tray.connect("activate", lambda icon: self._tray_menu(icon, 0, Gtk.get_current_event_time()))
        self.tray.connect("popup-menu", self._tray_menu)
        self.tray.set_visible(True)
        self._update_tooltip()

    def _update_tooltip(self):
        if self.tray:
            status = "Paused" if self.paused else f"{len(self.history)} clips"
            shortcut = Gtk.accelerator_get_label(*Gtk.accelerator_parse(self.config.settings.hotkey))
            self.tray.set_tooltip_text(f"Jumpkut {__version__} · {status} · {shortcut}")

    def recent_clips(self):
        return self.history.recent(self.config.settings.quick_history_limit)

    def _tray_menu(self, _icon, button, timestamp):
        self._remove_source("_paste_source")
        if self.popup.get_visible():
            self.cancel_popup()
        target = self.backend.focused_window() if self.backend else None
        menu = Gtk.Menu()
        heading = Gtk.MenuItem(label=f"Clipboard History · {len(self.history)} clips")
        heading.set_sensitive(False)
        menu.append(heading)
        for index, clip in enumerate(self.history.recent(self.config.settings.tray_history_limit), 1):
            preview = " ".join(clip.text.split())
            item = Gtk.MenuItem(label=f"{index}.  {preview[:65]}{'…' if len(preview) > 65 else ''}")
            item.connect("activate", lambda _item, chosen=clip: self.select_clip(chosen, target))
            menu.append(item)
        if not self.history:
            empty = Gtk.MenuItem(label="Copy some text to start your history")
            empty.set_sensitive(False)
            menu.append(empty)
        menu.append(Gtk.SeparatorMenuItem())
        for label, callback in (("Show Full History", self.show_full_history), ("Preferences…", self.show_preferences), ("About Jumpkut", self.show_about)):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, fn=callback: fn())
            menu.append(item)
        pause = Gtk.CheckMenuItem(label="Pause Recording")
        pause.set_active(self.paused)
        pause.connect("toggled", lambda item: self._pause(item.get_active()))
        menu.append(pause)
        menu.append(Gtk.SeparatorMenuItem())
        for label, callback in (("Back Up History…", lambda: self.export_history(self.preferences)), ("Clear History…", self.clear_history), ("Quit", self.quit)):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, fn=callback: fn())
            menu.append(item)
        menu.show_all()
        menu.popup(None, None, Gtk.StatusIcon.position_menu, self.tray, button, timestamp)
        self._menu = menu

    def _pause(self, paused):
        self.paused = paused
        self._generation += 1
        if not paused:
            self._capture_current()
        self._update_tooltip()

    def show_preferences(self):
        if self.popup.get_visible():
            self.cancel_popup(restore=False)
        if self.preferences is None:
            self.preferences = Preferences(self)
        self.preferences.show_all()
        self.preferences.present()

    @staticmethod
    def autostart_enabled():
        return autostart_enabled()

    def save_preferences(self, settings, startup):
        key, name, modifiers = parse_hotkey(settings.hotkey)
        old = self.config.settings
        changed = settings.hotkey != old.hotkey or self.backend is None
        if changed:
            if self.backend:
                self.backend.close()
            self.backend = None
            try:
                self.backend = X11Backend(name, modifiers, self._hotkey)
            except RuntimeError:
                self._bind_hotkey(old.hotkey)
                raise
        try:
            self.config.save(settings)
        except (OSError, ValueError):
            if changed:
                self.backend.close()
                self._bind_hotkey(old.hotkey)
            raise
        self.hotkey_key = key
        errors = []
        try:
            set_autostart(startup)
        except OSError as exc:
            errors.append(f"Could not change startup: {exc}")
        self._update_tooltip()
        if errors:
            raise RuntimeError("Preferences were saved, but some changes could not be written.\n" + "\n".join(errors))

    def export_history(self, parent=None):
        filename = choose_backup(parent, save=True, application=self)
        if filename:
            try:
                self.history.export_to(Path(filename))
            except OSError as exc:
                self.message(f"Could not back up history: {exc}", parent)

    def import_history(self, parent=None):
        filename = choose_backup(parent, save=False, application=self)
        if filename:
            try:
                count = self.history.import_from(Path(filename))
            except (OSError, ValueError, UnicodeError) as exc:
                self._history_changed()
                self.message(f"Could not restore history: {exc}", parent)
                return
            self._history_changed()
            self.message(f"Restored {count} new clippings into full history. Existing clippings were kept.", parent, error=False)

    def clear_history(self):
        dialog = Gtk.MessageDialog(transient_for=self.preferences, message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.OK_CANCEL, text="Clear all clipboard history?")
        dialog.set_application(self)
        dialog.format_secondary_text("Saved backups are kept. The current system clipboard is left as it is.")
        confirmed = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        if confirmed:
            try:
                self.history.clear()
            except OSError as exc:
                self.message(str(exc), self.preferences)
            self._history_changed()

    def message(self, text, parent=None, error=True):
        dialog = Gtk.MessageDialog(transient_for=parent, modal=True, message_type=Gtk.MessageType.ERROR if error else Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.CLOSE, text=text)
        dialog.set_application(self)
        dialog.run()
        dialog.destroy()

    def notify(self, text):
        print(f"Jumpkut: {text}", file=sys.stderr)
        notification = Gio.Notification.new("Jumpkut")
        notification.set_body(text)
        self.send_notification("status", notification)
