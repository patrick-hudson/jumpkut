#!/usr/bin/python3
"""Capture shareable UI examples on a disposable desktop with synthetic data.

Run through capture-share.sh; it supplies an isolated X11 display, bus, and XDG
directories so this script never accesses a user's clipboard or preferences.
"""

import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo


if os.environ.get("JUMPKUT_SHARE_CAPTURE") != "1":
    raise SystemExit("Use scripts/capture-share.sh to capture on an isolated desktop.")

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jumpkut.app import Jumpkut
from jumpkut.settings import set_autostart


OUTPUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/share/screenshots")
OUTPUT.mkdir(parents=True, exist_ok=True)
EXAMPLES = [
    "Hi team — the draft is ready.\n\nPlease review the latest notes before our 3 pm check-in.\n\nThanks!",
    "Project notes\n\n• Review the café website draft\n• Update the launch checklist\n• Share the final screenshots\n\nSmall details make a smoother day.",
    "git status --short",
    "hello@example.com",
    "Launch checklist: review, test, share.",
    "Meeting notes: keep the first release focused.",
    "https://example.com/design-notes",
    "def greet(name):\n    return f\"Hello, {name}!\"",
    "Recipe: 2 cups flour, 1 cup water, a pinch of salt.",
    "42 Example Lane\nSample City, EX 12345",
    "Weekend list: books, coffee, a long walk.",
    "Remember to back up the clipboard history.",
]


def pump(seconds=0.3):
    deadline = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def capture(name, widget):
    pump()
    width = widget.get_allocated_width()
    height = widget.get_allocated_height()
    pixbuf = Gdk.pixbuf_get_from_window(widget.get_window(), 0, 0, width, height)
    if pixbuf is None:
        raise RuntimeError(f"Could not capture {name}")
    destination = OUTPUT / name
    pixbuf.savev(str(destination), "png", [], [])
    print(f"{destination.resolve()} — {width} × {height}", flush=True)


app = Jumpkut()
app.register(None)
try:
    pump()
    # A portable fixture gives the genuine history browser useful timestamps.
    latest = datetime(2026, 10, 5, 14, 30, tzinfo=ZoneInfo("America/Chicago")).timestamp()
    fixture = Path(os.environ["XDG_DATA_HOME"]) / "demo-clippings.json"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(json.dumps({
        "version": 1,
        "clips": [
            {"id": f"demo-{index:02d}", "text": text, "created_at": latest - index * 420}
            for index, text in enumerate(EXAMPLES)
        ],
    }, ensure_ascii=False), encoding="utf-8")
    app.history.import_from(fixture)
    app._history_changed()

    # This is the held-modifier quick popup, with a stable capture window.
    app.popup.open(app.history.items, release_selects=True, timestamp=0)
    capture("quick-popup.png", app.popup)
    app.cancel_popup(restore=False)

    app.show_full_history()
    app.history_window.tree.set_cursor(Gtk.TreePath.new_from_indices([1]))
    capture("full-history.png", app.history_window)
    app.history_window.destroy()
    pump()

    set_autostart(True)
    app.show_preferences()
    capture("preferences.png", app.preferences)
    app.preferences.destroy()
    pump()

    app.tray.emit("activate")
    capture("tray-menu.png", app._menu)
    app._menu.popdown()
    pump()

    # GTK's icon-name lookup can find the repository's real scissors SVG even
    # though the isolated data directory intentionally has no installed icons.
    Gtk.IconTheme.get_default().append_search_path(
        str(Path(__file__).resolve().parents[1] / "jumpkut/assets")
    )
    app.show_about()
    capture("about.png", app.about)
    app.about.destroy()
finally:
    app.cancel_popup(restore=False)
    for window in list(app.get_windows()):
        window.destroy()
    app.do_shutdown()
