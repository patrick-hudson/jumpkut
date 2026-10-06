"""Command-line entry point; help/version work without a graphical session."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

from . import __version__
from .settings import log_path


def parser():
    result = argparse.ArgumentParser(description="Jumpkut — clipboard history for Linux/X11", epilog="Without options, Jumpkut starts in the background system tray.")
    result.add_argument("--version", action="version", version=f"Jumpkut {__version__}")
    commands = result.add_mutually_exclusive_group()
    commands.add_argument("--background", action="store_true", help="start in the tray and detach from the terminal")
    commands.add_argument("--daemon", action="store_true", help="start in the tray, attached to the terminal")
    commands.add_argument("--show", action="store_true", help="show the running history popup")
    commands.add_argument("--history", action="store_true", help="open the full history browser")
    commands.add_argument("--preferences", action="store_true", help="open preferences")
    commands.add_argument("--about", action="store_true", help="show the application version and credits")
    commands.add_argument("--quit", action="store_true", help="quit the running instance")
    return result


def start_background():
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "ab") as output:
            os.fchmod(output.fileno(), 0o600)
            subprocess.Popen(
                [sys.executable, "-m", "jumpkut", "--daemon"],
                cwd=Path(__file__).resolve().parent.parent,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
    except OSError as exc:
        print(f"Cannot start Jumpkut in the background: {exc}", file=sys.stderr)
        return 1
    print(f"Jumpkut background start requested. Log: {path}")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        argv = ["--background"]
    options = parser().parse_args(argv)
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        print("Jumpkut currently requires an X11 desktop session. Choose an X11 session at login.", file=sys.stderr)
        return 1
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("GdkX11", "3.0")
        from gi.repository import Gdk, GLib, Gtk
        from gi.repository import GdkX11  # Registers the X11 window methods.
        from .app import Jumpkut
    except (ImportError, ValueError) as exc:
        print(f"Missing desktop dependency: {exc}\nOn Mint/Ubuntu: sudo apt install python3-gi gir1.2-gtk-3.0 python3-xlib", file=sys.stderr)
        return 1
    GLib.set_prgname("jumpkut")
    GLib.set_application_name("Jumpkut")
    Gdk.set_program_class("Jumpkut")
    if not Gtk.init_check()[0]:
        print("Cannot open the X11 display. Run Jumpkut inside your desktop session.", file=sys.stderr)
        return 1
    if options.background:
        return start_background()
    return Jumpkut().run(["jumpkut", *argv])


if __name__ == "__main__":
    raise SystemExit(main())
