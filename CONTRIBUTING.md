# Contributing to Jumpkut

Bug reports, small fixes, and thoughtful feature proposals are welcome. Jumpkut is a native GTK 3 clipboard manager for Linux on X11; the quick picker, tray menu, and Full History have separate roles.

## Get started

Use Python 3.10 or newer. On Ubuntu, Debian, or Linux Mint, install the dependencies for the app and its desktop tests:

```sh
sudo apt install git python3-gi gir1.2-gtk-3.0 python3-xlib xvfb metacity dbus-x11
git clone https://github.com/patrick-hudson/jumpkut.git
cd jumpkut
```

Use `/usr/bin/python3` for checks so GTK and Xlib come from the system packages. To try the app from source in an X11 session, run:

```sh
./run.py --daemon
```

This keeps diagnostic output in the terminal. Quit any existing Jumpkut instance first if you want to test the source version; commands otherwise go to the running instance. Back up your history before testing changes against your normal desktop session.

## Check your changes

Run the headless suite:

```sh
/usr/bin/python3 -m unittest discover -s tests -v
```

The native tests are skipped by that command. Run them with the supplied isolation script:

```sh
./scripts/test-desktop.sh
```

The script starts a disposable Xvfb display, a Metacity window manager, and a private D-Bus session. Tests use temporary preferences and history, so they do not read or replace your desktop clipboard. Do not enable `JUMPKUT_DESKTOP_TEST` directly in your login session. If Xvfb is in another location, pass its path to the script.

Add a focused regression test for behavior changes. Use synthetic clipboard text in tests and screenshots. Follow the surrounding Python style and keep the change small enough to review without unrelated formatting or refactoring.

## Open an issue or pull request

For bugs, include the Jumpkut version, distribution, desktop environment, session type, and steps to reproduce with sample text. Explain what you expected and what happened. Native Wayland support is not currently implemented.

For a pull request, describe the problem, the resulting behavior, and the checks you ran. Preserve stored history and preferences during updates. Keep **Alt+C** as the quick popup and **Show Full History** as the separate browser. Quick and tray limits must never trim the full archive.

## Release changes

For a user-visible change, add concise release notes under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md). The application version lives only in `jumpkut/__init__.py`; package metadata reads it dynamically.

When a change is complete and its checks pass, make one version bump for the request:

```sh
./scripts/release.py patch
```

Use `patch` for fixes, `minor` for new features, or `major` for breaking changes. The helper moves pending notes into a dated release entry and updates the canonical version. The clipboard backup schema has its own version and does not change with an application release.

To update a local installation, run `./install.py`. It preserves history and preferences. Reload a running instance only after its history is persisted; preserve any temporary history first.
