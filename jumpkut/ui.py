"""Small native GTK windows; clipboard and keyboard ownership live in the app."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from gi.repository import Gdk, GLib, Gtk

from . import __version__


CSS = b"""
window#jumpkut-popup { background: rgba(119, 111, 127, 0.88); color: #fff; border-radius: 26px; }
#jumpkut-popup label { color: #fff; }
#jumpkut-popup .muted { color: rgba(255, 255, 255, 0.78); font-size: 10px; }
#jumpkut-popup .counter { background: rgba(34, 29, 43, 0.50); border-radius: 10px; padding: 6px 12px; font-size: 16px; font-weight: bold; }
#jumpkut-popup .clip-preview { background: rgba(31, 24, 42, 0.62); border-radius: 10px; }
#jumpkut-popup textview, #jumpkut-popup textview text { background: transparent; color: #fff; font-family: sans-serif; font-weight: bold; font-size: 15px; }
"""


class Popup(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Jumpkut — Clipboard history")
        self.app = app
        self.clips = []
        self.index = 0
        self.keyboard_seat = None
        self.set_name("jumpkut-popup")
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(420, 420)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.connect("key-press-event", self._key)
        self.connect("focus-out-event", self._focus_out)
        self.connect("map-event", self._grab_keyboard)
        self.connect("hide", lambda *_: self._release_keyboard())
        self.connect("button-press-event", self._click)
        self.connect("delete-event", lambda *_: self.app.cancel_popup() or True)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=18)
        self.add(box)
        scissors = Gtk.Image.new_from_file(str(Path(__file__).resolve().parent / "assets" / "bezel-scissors.svg"))
        box.pack_start(scissors, False, False, 0)
        self.counter = Gtk.Label()
        self.counter.set_halign(Gtk.Align.CENTER)
        self.counter.get_style_context().add_class("counter")
        box.pack_start(self.counter, False, False, 0)

        self.preview = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.preview.set_can_focus(False)
        self.preview.set_justification(Gtk.Justification.CENTER)
        self.preview.set_left_margin(12)
        self.preview.set_right_margin(12)
        self.preview.set_top_margin(12)
        self.preview.set_bottom_margin(12)
        scroll = Gtk.ScrolledWindow()
        scroll.get_style_context().add_class("clip-preview")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(174)
        scroll.add(self.preview)
        box.pack_start(scroll, True, True, 0)
        self.hint = Gtk.Label()
        self.hint.get_style_context().add_class("muted")
        box.pack_start(self.hint, False, False, 0)

    def open(self, clips, release_selects, timestamp, selected_id):
        self.clips = list(clips)
        self.index = next((index for index, clip in enumerate(self.clips) if clip.id == selected_id), 0)
        self.hint.set_text("↑ ↓ cycle   ·   Release modifiers to select   ·   Esc cancel" if release_selects else "↑ ↓ cycle   ·   Enter select   ·   Delete remove   ·   Esc cancel")
        self.render()
        self.show_all()
        self.present_with_time(timestamp or Gtk.get_current_event_time())
        self.grab_focus()

    def _grab_keyboard(self, *_):
        if not self.get_visible():
            return False
        # Keep Alt+Escape and repeated shortcut keys out of the WM's bindings.
        # Wait for the X11 map event: show_all() alone is not yet viewable.
        self.keyboard_seat = self.get_display().get_default_seat()
        status = self.keyboard_seat.grab(self.get_window(), Gdk.SeatCapabilities.KEYBOARD, False, None, None, None)
        if status != Gdk.GrabStatus.SUCCESS:
            self.keyboard_seat = None
        return False

    def _release_keyboard(self):
        if self.keyboard_seat:
            self.keyboard_seat.ungrab()
            self.keyboard_seat = None

    @property
    def selected(self):
        return self.clips[self.index] if self.clips else None

    def move(self, delta):
        if self.clips:
            self.index = (self.index + delta) % len(self.clips)
            self.render()

    def render(self):
        clip = self.selected
        if not clip:
            self.counter.set_text("0")
            self.preview.get_buffer().set_text("Your clipboard history starts here.\n\nCopy some text in any application, then use your history shortcut.")
            return
        self.counter.set_text(str(self.index + 1))
        self.counter.set_tooltip_text(f"Clipping {self.index + 1} of {len(self.clips)}")
        # Rendering a preview never truncates the text that will be copied.
        self.preview.get_buffer().set_text(clip.text[:8000])
        self.preview.get_buffer().place_cursor(self.preview.get_buffer().get_start_iter())

    def _click(self, _window, event):
        if event.type == Gdk.EventType.DOUBLE_BUTTON_PRESS:
            self.app.accept_popup()
            return True
        return False

    def _key(self, _window, event):
        key = event.keyval
        if key == Gdk.KEY_Escape:
            self.app.cancel_popup()
        elif key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.app.accept_popup()
        elif key in (Gdk.KEY_Down, Gdk.KEY_Right, Gdk.KEY_Tab):
            self.move(-1 if event.state & Gdk.ModifierType.SHIFT_MASK else 1)
        elif key in (Gdk.KEY_Up, Gdk.KEY_Left, Gdk.KEY_ISO_Left_Tab):
            self.move(-1)
        elif key == Gdk.KEY_Home:
            self.index = 0
            self.render()
        elif key == Gdk.KEY_End and self.clips:
            self.index = len(self.clips) - 1
            self.render()
        elif key == Gdk.KEY_Delete and self.selected:
            self.app.remove_clip(self.selected.id)
            self.clips.pop(self.index)
            self.index = min(self.index, max(0, len(self.clips) - 1))
            self.render()
        elif Gdk.keyval_to_lower(key) == self.app.hotkey_key:
            self.move(-1 if event.state & Gdk.ModifierType.SHIFT_MASK else 1)
        else:
            return False
        return True

    def _focus_out(self, *_):
        # A repeated global shortcut temporarily grabs the keyboard and emits
        # focus-out, even though this is still the WM's active window.
        GLib.timeout_add(50, self._cancel_if_other_window_active)
        return False

    def _cancel_if_other_window_active(self):
        if self.get_visible() and (not self.app.backend or self.app.backend.focused_window() != self.get_window().get_xid()):
            self.app.cancel_popup(restore=False)
        return GLib.SOURCE_REMOVE


class Preferences(Gtk.Dialog):
    def __init__(self, app):
        super().__init__(title="Jumpkut Preferences", flags=0)
        self.app = app
        self.set_application(app)
        self.set_default_size(440, -1)
        self.set_resizable(False)
        self.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        box = self.get_content_area()
        box.set_border_width(20)
        box.set_spacing(16)
        title = Gtk.Label(label="Clipboard history, your way", xalign=0)
        title.set_markup("<b>Clipboard history, your way</b>")
        box.pack_start(title, False, False, 0)
        grid = Gtk.Grid(column_spacing=18, row_spacing=14)
        box.pack_start(grid, False, False, 0)
        grid.attach(Gtk.Label(label="Keyboard shortcut", xalign=0), 0, 0, 1, 1)
        self.shortcut = app.config.settings.hotkey
        self.recording_shortcut = False
        self.shortcut_seat = None
        self.hotkey = Gtk.Button(label=Gtk.accelerator_get_label(*Gtk.accelerator_parse(self.shortcut)))
        self.hotkey.set_tooltip_text("Click, then press your preferred key combination")
        self.hotkey.connect("clicked", self._record_shortcut)
        self.connect("key-press-event", self._shortcut_key)
        grid.attach(self.hotkey, 1, 0, 1, 1)
        def length_row(label, row, value, enabled):
            custom = Gtk.CheckButton(label=label)
            custom.set_active(enabled)
            spin = Gtk.SpinButton.new_with_range(1, 10000, 1)
            spin.set_value(value)
            spin.set_sensitive(enabled)
            custom.connect("toggled", lambda check: spin.set_sensitive(check.get_active()))
            grid.attach(custom, 0, row, 1, 1)
            grid.attach(spin, 1, row, 1, 1)
            return custom, spin

        settings = app.config.settings
        self.custom_quick, self.limit = length_row("Custom quick history length", 1, settings.history_limit, settings.custom_quick_limit)
        self.custom_tray, self.tray_limit = length_row("Custom tray history length", 2, settings.tray_limit, settings.custom_tray_limit)
        scope = Gtk.Label(label="Defaults: quick picker 30, tray menu 10.\nFull History keeps all clippings until you delete them.", xalign=0)
        scope.set_line_wrap(True)
        scope.get_style_context().add_class("dim-label")
        box.pack_start(scope, False, False, 0)
        self.startup = Gtk.CheckButton(label="Run at startup")
        self.startup.set_active(app.autostart_enabled())
        self.sticky = Gtk.CheckButton(label="Keep history open until Enter")
        self.sticky.set_active(app.config.settings.sticky)
        self.resume = Gtk.CheckButton(label="Resume last selection")
        self.resume.set_active(app.config.settings.resume_last_selection)
        self.resume.set_tooltip_text("Reopen the last selected clipping until you copy something new")
        self.paste = Gtk.CheckButton(label="Paste automatically into the previous app")
        self.paste.set_active(app.config.settings.auto_paste)
        for widget in (self.startup, self.resume, self.sticky, self.paste):
            box.pack_start(widget, False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 0)
        backup_box = Gtk.Box(spacing=10)
        for label, callback in (("Back Up History…", app.export_history), ("Restore Backup…", app.import_history)):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, fn=callback: fn(self))
            backup_box.pack_start(button, True, True, 0)
        box.pack_start(backup_box, False, False, 0)
        note = Gtk.Label(label="History and backups contain plain text. Password-manager\nclippings marked as sensitive are skipped.", xalign=0)
        note.get_style_context().add_class("dim-label")
        box.pack_start(note, False, False, 0)
        version = Gtk.Label(label=f"Jumpkut {__version__}", xalign=0)
        version.get_style_context().add_class("dim-label")
        box.pack_start(version, False, False, 0)
        self.connect("response", self._response)
        self.connect("destroy", lambda *_: setattr(app, "preferences", None))
        self.connect("destroy", lambda *_: self._release_keyboard())

    def _response(self, _dialog, response):
        if response == Gtk.ResponseType.OK:
            if self.recording_shortcut:
                self.app.message("Press a key combination, or Escape to keep the previous shortcut.", self)
                return
            new_settings = replace(self.app.config.settings,
                                   hotkey=self.shortcut, history_limit=self.limit.get_value_as_int(),
                                   tray_limit=self.tray_limit.get_value_as_int(),
                                   custom_quick_limit=self.custom_quick.get_active(),
                                   custom_tray_limit=self.custom_tray.get_active(),
                                   sticky=self.sticky.get_active(), auto_paste=self.paste.get_active(),
                                   resume_last_selection=self.resume.get_active())
            try:
                self.app.save_preferences(new_settings, self.startup.get_active())
            except (ValueError, RuntimeError, OSError) as exc:
                self.app.message(str(exc), self)
                return
        self.destroy()

    def _record_shortcut(self, _button):
        self.shortcut_seat = self.get_display().get_default_seat()
        status = self.shortcut_seat.grab(self.get_window(), Gdk.SeatCapabilities.KEYBOARD, False, None, None, None)
        if status != Gdk.GrabStatus.SUCCESS:
            self.shortcut_seat = None
            self.app.message("Could not capture the keyboard. Try clicking the shortcut again.", self)
            return
        self.recording_shortcut = True
        self.hotkey.set_label("Press shortcut…")
        self.hotkey.grab_focus()

    def set_shortcut(self, shortcut):
        self._release_keyboard()
        self.shortcut = shortcut
        self.recording_shortcut = False
        self.hotkey.set_label(Gtk.accelerator_get_label(*Gtk.accelerator_parse(shortcut)))

    def _release_keyboard(self):
        if self.shortcut_seat:
            self.shortcut_seat.ungrab()
            self.shortcut_seat = None

    def _shortcut_key(self, _window, event):
        if not self.recording_shortcut:
            return False
        if event.keyval == Gdk.KEY_Escape:
            self.set_shortcut(self.shortcut)
        elif event.keyval not in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R, Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R):
            mask = event.state & Gtk.accelerator_get_default_mod_mask()
            if mask:
                self.set_shortcut(Gtk.accelerator_name(Gdk.keyval_to_lower(event.keyval), mask))
        return True


def choose_backup(parent, save, application):
    dialog = Gtk.FileChooserDialog(title="Back Up History" if save else "Restore Backup", transient_for=parent, action=Gtk.FileChooserAction.SAVE if save else Gtk.FileChooserAction.OPEN)
    dialog.set_application(application)
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Back Up" if save else "Restore", Gtk.ResponseType.ACCEPT)
    file_filter = Gtk.FileFilter()
    file_filter.set_name("Jumpkut history (*.json)")
    file_filter.add_pattern("*.json")
    dialog.add_filter(file_filter)
    if save:
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(f"jumpkut-history-{datetime.now():%Y-%m-%d}.json")
    filename = dialog.get_filename() if dialog.run() == Gtk.ResponseType.ACCEPT else None
    dialog.destroy()
    return filename
