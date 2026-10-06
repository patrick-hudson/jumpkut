"""A searchable, ordinary desktop window for the complete clipboard history."""

from datetime import datetime

from gi.repository import Gdk, Gtk, Pango


class FullHistory(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Jumpkut — Full history")
        self.app = app
        self._refreshing = False
        self._previewed = None
        self.set_default_size(940, 620)
        self.set_size_request(720, 440)
        self.connect("key-press-event", self._key)
        self.connect("destroy", lambda *_: setattr(app, "history_window", None))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        self.add(box)
        heading = Gtk.Label(xalign=0)
        heading.set_markup("<big><b>Full history</b></big>")
        box.pack_start(heading, False, False, 0)
        self.count = Gtk.Label(xalign=0)
        self.count.get_style_context().add_class("dim-label")
        box.pack_start(self.count, False, False, 0)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search all clipboard text…")
        self.search.connect("search-changed", lambda *_: self.refresh())
        box.pack_start(self.search, False, False, 0)

        panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        panes.set_position(480)
        box.pack_start(panes, True, True, 0)

        self.model = Gtk.ListStore(str, str, str, int)
        self.tree = Gtk.TreeView(model=self.model)
        self.tree.set_headers_visible(True)
        self.tree.set_enable_search(False)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        self.tree.get_selection().connect("changed", self._selection_changed)
        self.tree.connect("row-activated", lambda *_: self._copy())
        for title, index in (("#", 3), ("Clipping", 1), ("Copied", 2)):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            if title == "Clipping":
                renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
                column.set_expand(True)
                column.set_min_width(140)
            else:
                column.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
            self.tree.append_column(column)
        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_shadow_type(Gtk.ShadowType.IN)
        list_scroll.add(self.tree)
        panes.pack1(list_scroll, resize=True, shrink=False)

        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12)
        preview_box.pack_start(Gtk.Label(label="Full clipping", xalign=0), False, False, 0)
        self.preview = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.preview.set_left_margin(12)
        self.preview.set_right_margin(12)
        self.preview.set_top_margin(12)
        self.preview.set_bottom_margin(12)
        preview_scroll = Gtk.ScrolledWindow()
        preview_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        preview_scroll.set_shadow_type(Gtk.ShadowType.IN)
        preview_scroll.add(self.preview)
        preview_box.pack_start(preview_scroll, True, True, 0)
        panes.pack2(preview_box, resize=True, shrink=False)

        actions = Gtk.Box(spacing=8)
        self.copy_button = Gtk.Button(label="Copy")
        self.copy_button.get_style_context().add_class("suggested-action")
        self.copy_button.connect("clicked", lambda *_: self._copy())
        self.delete_button = Gtk.Button(label="Delete")
        self.delete_button.connect("clicked", lambda *_: self._delete())
        actions.pack_start(self.copy_button, False, False, 0)
        actions.pack_start(self.delete_button, False, False, 0)
        for label, callback in (("Back Up History…", app.export_history), ("Restore Backup…", app.import_history)):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _button, fn=callback: fn(self))
            actions.pack_end(button, False, False, 0)
        box.pack_start(actions, False, False, 0)
        self.status = Gtk.Label(label="↑ ↓ browse · Enter or double-click to copy · Ctrl+F search", xalign=0)
        self.status.get_style_context().add_class("dim-label")
        box.pack_start(self.status, False, False, 0)

    @property
    def selected(self):
        model, iterator = self.tree.get_selection().get_selected()
        return self.app.history.get(model[iterator][0]) if iterator is not None else None

    def open(self):
        self.refresh()
        self.show_all()
        self.present()
        self.tree.grab_focus()

    def refresh(self):
        selected = self.selected
        selected_id = selected.id if selected else None
        query = self.search.get_text().casefold()
        clips = self.app.history.items
        self._refreshing = True
        self.model.clear()
        selected_path = None
        for rank, clip in enumerate(clips, 1):
            if query and query not in clip.text.casefold():
                continue
            preview = " ".join(clip.text[:1000].split())
            if len(preview) > 240 or len(clip.text) > 1000:
                preview = preview[:240] + "…"
            try:
                copied = datetime.fromtimestamp(clip.created_at).strftime("%Y-%m-%d %H:%M")
            except (OverflowError, OSError, ValueError):
                copied = "Unknown date"
            iterator = self.model.append((clip.id, preview, copied, rank))
            if clip.id == selected_id:
                selected_path = self.model.get_path(iterator)
        self._refreshing = False

        if len(self.model):
            path = selected_path or Gtk.TreePath.new_first()
            self.tree.set_cursor(path)
            self.tree.scroll_to_cell(path, None, False, 0, 0)
        self.count.set_text(
            f"Showing {len(self.model)} of {len(clips)} clippings"
            if query else f"{len(clips)} clippings · newest first"
        )
        self._update_selection()

    def _selection_changed(self, *_):
        if not self._refreshing:
            self._update_selection()

    def _update_selection(self):
        clip = self.selected
        self.copy_button.set_sensitive(clip is not None)
        self.delete_button.set_sensitive(clip is not None)
        if clip:
            content = (clip.id, clip.text)
            text = clip.text
        elif self.app.history.items:
            content = None
            text = "No clippings match your search.\n\nTry another search or clear the search field."
        else:
            content = None
            text = "Your clipboard history is empty.\n\nCopy text in any application to start your history."
        # Preserve the preview's scroll position when an unrelated copy arrives.
        if content != self._previewed or content is None:
            buffer = self.preview.get_buffer()
            buffer.set_text(text)
            buffer.place_cursor(buffer.get_start_iter())
            self._previewed = content
        self.status.set_text("↑ ↓ browse · Enter or double-click to copy · Ctrl+F search")

    def _copy(self):
        clip = self.selected
        if clip:
            self.app.select_clip(clip, None)
            self.status.set_text("Copied to clipboard")

    def _delete(self):
        clip = self.selected
        if clip:
            self.app.remove_clip(clip.id)

    def _key(self, _window, event):
        if Gdk.keyval_to_lower(event.keyval) == Gdk.KEY_f and event.state & Gdk.ModifierType.CONTROL_MASK:
            self.search.grab_focus()
            return True
        if event.keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
                self.refresh()
            else:
                self.destroy()
            return True
        if event.keyval == Gdk.KEY_Delete and self.tree.has_focus():
            self._delete()
            return True
        return False
