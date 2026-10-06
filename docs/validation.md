# Validation

Jumpkut 0.4.0 was verified on Linux Mint 22.3 with system Python 3.12 and GTK 3.24. GitHub Actions also runs the headless and native suites on Ubuntu 24.04.

## Automated checks

| Suite | Checks | Covers |
|---|---:|---|
| Headless | 55 | Archive persistence and deduplication, complete backups, file permissions, settings validation and migration, startup entries, X11 paste safety, installation, and releases. |
| Native desktop | 19 | Clipboard capture, global shortcuts, navigation, paste into another app, tray interaction, Preferences, Full History, and detached background launch. |

Run the headless suite with `/usr/bin/python3 -m unittest discover -s tests -v`. Native tests are skipped in that command unless the disposable-desktop flag is set; run them with `./scripts/test-desktop.sh`.

The native runner starts Xvfb and Metacity on a disposable display, creates a private D-Bus session without desktop service activation, and uses temporary preferences, history, and logs. It does not access the login session's clipboard.

## Behavior verified

- Archive creation, loading, backup, and restore retain more than the original 200-clipping limit. Whitespace, Unicode, clipping IDs, and copy order survive persistence.
- Quick history defaults to 30 and the tray to 10. Independent checkboxes enable custom lengths; disabling them restores the defaults and remembers the custom values.
- A 35-clipping native scenario checks default and custom view counts, independent toggling, re-enabling remembered values, and complete Full History retention.
- Fresh Alt+C popups start at the newest clipping, #1, after selection, cancellation, and new copies. Arrow and repeated-key navigation wrap within the quick list.
- Selection pastes into the original external editor and avoids Jumpkut's own search and backup filename fields. Sticky mode, terminal paste chords, held modifiers, Caps Lock, and conflicting shortcuts are covered.
- Full History supports exact previews and copying, full-text search, stable selection during new copies, deletion, empty states, and single-instance command routing.
- Preferences and installation preserve saved data. Legacy settings migrate in memory without rewriting the original file until Save.
- The installed command detaches from the terminal. Its desktop entry opens Full History in the same running instance; command-line Show and Quit remain functional.
- Installation handles unusual path characters and refreshes app code without replacing saved history or backups.
- Semantic version bumps, changelog rollover, invalid input, atomic write rollback, and Python bytecode cache invalidation are covered.

## Visual checks and limits

The quick bezel, tray menu, Preferences, Full History, and About dialog were visually inspected. Current screenshots in `docs/images/` use synthetic demo data.

No linter or static type checker is configured. GTK 3 emits deprecation warnings for its legacy tray API; Cinnamon supports that protocol. Automatic terminal paste selects a chord from common terminal window classes.

This release supports text history on X11. Native Wayland needs a different clipboard and global-shortcut backend.
