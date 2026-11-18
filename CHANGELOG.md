# Changelog

## [Unreleased]

## [0.8.2] - 2026-11-17

### Fixed

- Escape now closes Full History, Preferences, and About, and saving Preferences closes the window; before, the window stayed open. Escape and Return work as soon as a window opens, without clicking it first.
- Preferences can be used from the keyboard: Tab reaches the buttons and checkboxes, Space toggles a checkbox, and Space or Return presses a button.
- Paste automatically uses the active keyboard layout when Plasma has several, and no longer pastes a newer copy that arrived while it waited. A failed Wayland copy is reported even when Paste automatically is off.
- Choosing a clipping from the tray menu copies that clipping even if a new copy arrives at the same moment.
- `jumpkut --quit` and the other commands reach a running Jumpkut from a shell without a display.
- Shortcuts saved by the Python version with keys such as `plus` keep working, `Ctrl++` can be recorded, and non-Latin keys (for example Cyrillic) bind on X11.
- X11: a password-manager copy can no longer be recorded when the clipboard changes owner while Jumpkut reads it.
- A malformed colour in the KDE colour scheme no longer stops Jumpkut from starting.
- Without `XDG_RUNTIME_DIR`, Jumpkut keeps its KWin scripts in a private directory instead of a shared `/tmp` path.
- The installer and Run at startup handle install paths containing `%`, and the installer updates a startup entry that is a symbolic link.

## [0.8.1] - 2026-11-17

### Fixed

- KDE Plasma Wayland: quitting Jumpkut could crash KWin and close every app in the session. Jumpkut no longer takes keyboard focus from the active window when it starts.
- KDE Plasma Wayland: Jumpkut no longer appears in the taskbar or the window switcher; it shows only in the system tray.
- The clipboard watcher (`wl-paste`) now exits with Jumpkut instead of staying behind.

## [0.8.0] - 2026-11-14

### Changed

- Jumpkut is rewritten in Rust with the Slint toolkit and ships as one native binary. It reads and writes the same history, preferences, backups, and startup entry, so nothing needs converting; it refuses to start while the Python version is running.
- The Alt+C popup appears instantly, without KWin's window-open animation and without first-press delay, with KDE blur behind it and your KDE accent colour. Full History, Preferences, and About follow the Plasma colour scheme.
- On KDE Plasma Wayland, Jumpkut runs natively instead of through XWayland. Paste automatically types the shortcut through KWin's own input protocol: no permission prompt and no remote-control notice. A `/dev/uinput` keyboard, then the RemoteDesktop portal, are used only where that protocol is unavailable.
- Paste automatically still types Ctrl+Shift+V into terminals (Konsole, Ghostty, kitty, and others) on Wayland and X11 and waits for held modifiers to be released; it now also uses the right key on non-QWERTY layouts.
- X11 keyboard-layout changes re-register the shortcut.
- Dependencies: Python 3, GTK 3, PyGObject, python-xlib, and librsvg are no longer needed. Packages now depend on the system OpenGL/EGL, Wayland, X11, xkbcommon, and fontconfig libraries, and recommend wl-clipboard 2.3 or newer. The `.deb` and `.rpm` are now x86_64 (`amd64`) packages. Installing from source needs Rust (cargo); `./install.py` builds the binary, replaces the old Python app directory, and points an existing startup entry at the new binary.

## [0.7.1] - 2026-11-14

### Changed

- New app and tray icon: a fanned deck of clippings in blue with an orange highlight, replacing the purple scissors. The README banner and badges use the new colours.

## [0.7.0] - 2026-11-13

### Added

- Support KDE Plasma 6 Wayland sessions. Alt+C is registered with KDE's global shortcuts and appears in System Settings. Clipboard copies from every app are recorded through wl-clipboard 2.3 or newer; copies that carry the KDE password-manager hint (`x-kde-passwordManagerHint`) are skipped. Paste automatically uses the RemoteDesktop portal after a one-time permission prompt; Jumpkut opens one input session per run and keeps it across shortcut changes, so KDE's remote-control notice appears once per sign-in. It waits until held modifiers are released, and types Ctrl+Shift+V for terminals including Konsole and Ghostty. The tray icon and popup run through XWayland; python-xlib is now needed only on X11.

## [0.6.1] - 2026-10-16

### Fixed

- Keep Alt+C responsive while saving clipboard history with an ordered background writer that finishes pending saves on normal exit. Archive and backup bytes, preferences, and complete history are preserved.
- Populate Full History in small batches and skip hidden-window rebuilds so large archives do not block clipboard shortcuts. Search, selection, and all archived clippings remain available.

## [0.6.0] - 2026-10-10

### Added

- Resume last selection in the Alt+C picker until a new external copy, enabled by default and configurable in Preferences. The picker follows the clipping when history positions change; canceled browsing does not change the remembered selection.

## [0.5.1] - 2026-10-06

### Fixed

- The AppImage uses its own GIO module directory and local file backend, avoiding incompatible host GVfs modules on newer Linux distributions.

## [0.5.0] - 2026-10-06

### Added

- Installable Debian/Ubuntu/Mint `.deb` and Fedora `.rpm` packages with a desktop menu entry and scissors icon.
- A portable x86_64 AppImage bundling Python, GTK, and clipboard dependencies, built against Ubuntu 22.04.
- GitHub Actions builds and tests installers, saves build artifacts, and publishes packages and SHA256 checksums for matching version tags.

### Fixed

- AppImage background launch and startup entries use the persistent executable so temporary mounts can close safely.

## [0.4.0] - 2026-10-05

### Changed

- The Alt+C popup defaults to 30 recent clippings and the tray menu to 10, with independent optional custom limits in Preferences.
- Custom length checkboxes restore each menu's default when unchecked and remember the previous custom value for reuse.
- Updates preserve previously customized quick history lengths while moving the old default of 200 to the new defaults. Full History and backups continue to include the complete archive.

## [0.3.0] - 2026-10-05

### Changed

- Quick history length limits recent clippings in the Alt+C popup and tray menu; Full History retains all unique clippings until deleted or cleared.
- Lowering quick history length preserves older clippings, and backups and restores include the complete archive without trimming.
- Existing history length preferences are reused as the quick history length, preserving saved clippings during updates.

## [0.2.0] - 2026-10-05

### Added

- Release versioning with one version value shared by the application, command, and package metadata.
- About Jumpkut in the tray menu and a visible version in Preferences and the tray tooltip.
- A release command that bumps the version and records pending changes in this changelog.

### Fixed

- A canceled quick popup cannot capture the keyboard while About is opening.
- A rapid version bump cannot reuse a cached Python module with the old version.

## [0.1.0] - 2026-10-05

### Added

- Linux/X11 text clipboard recording, bounded persistent history, and configurable shortcuts, defaulting to Alt+C.
- A Jumpcut-style quick popup with arrow-key cycling and automatic paste.
- A scissors tray icon, recent-history menu, Preferences, startup settings, and backup/restore.
- A searchable Full History window with complete previews, copying, and deletion.
- Detached background launch, a single `jumpkut` command, and a desktop start-menu entry.

### Fixed

- Fresh quick popups always start at the newest clipping, #1.
- Restoring a saved clipping preserves copy order.
- Clipboard selection avoids pasting into Jumpkut's own windows and dialogs.
