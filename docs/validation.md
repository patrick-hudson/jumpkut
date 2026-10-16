# Validation

Jumpkut 0.6.1 was verified on Linux Mint 22.3 with system Python 3.12 and GTK 3.24. GitHub Actions runs the headless and native suites on Ubuntu 24.04 and builds/tests Linux installers on Ubuntu 22.04.

## Automated checks

| Suite | Checks | Covers |
|---|---:|---|
| Headless | 72 | Archive persistence and deduplication, background save ordering and shutdown, exact backup bytes, file permissions, settings validation and migration, startup entries, X11 paste safety, installation, releases, package archives, and portable launchers. |
| Native desktop | 23 | Clipboard capture, global shortcuts during blocked writes and large history refreshes, selection resuming, navigation, paste into another app, tray interaction, Preferences, Full History, and detached background launch. |

Run the headless suite with `/usr/bin/python3 -m unittest discover -s tests -v`. Native tests are skipped in that command unless the disposable-desktop flag is set; run them with `./scripts/test-desktop.sh`.

The native runner starts Xvfb and Metacity on a disposable display, creates a private D-Bus session without desktop service activation, and uses temporary preferences, history, and logs. It does not access the login session's clipboard.

## Responsiveness measurements

A synthetic Xvfb/Metacity workload on the development machine queued Alt+C while capturing a new copy into an archive of 10,000 short clippings (about 3.8 MiB). These are medians from 11 warmed samples per case, comparing 0.6.0 with 0.6.1 using the same workload. The timer ends when the popup maps; it does not measure compositor presentation.

| Full History state | 0.6.0 Alt+C during copy | 0.6.1 Alt+C during copy |
|---|---:|---:|
| Opened, then hidden | 985 ms | 48 ms |
| Visible | 866 ms | 65 ms |

Hidden-window refresh alone fell from about 465 ms to under 1 ms. These measurements characterize contention on this machine, not a timing threshold enforced in CI. Native regression tests instead verify that the shortcut works while disk writes are blocked and before a large history refresh finishes.

## Behavior verified

- Archive creation, loading, backup, and restore retain more than the original 200-clipping limit. Whitespace, Unicode, clipping IDs, and copy order survive persistence.
- Background saves use the existing atomic JSON writer, retain every clipping in the latest snapshot, and finish active and queued writes before normal exit. Tests cover slow disks, failure recovery, complete backups during a pending save, deletion/clear ordering, and unchanged serialization bytes.
- Alt+C opens while a save is deliberately blocked. A 10,000-clipping Full History refresh yields to input, retains all rows, and skips rebuilding while hidden.
- Quick history defaults to 30 and the tray to 10. Independent checkboxes enable custom lengths; disabling them restores the defaults and remembers the custom values.
- A 35-clipping native scenario checks default and custom view counts, independent toggling, re-enabling remembered values, and complete Full History retention.
- Alt+C resumes the last successful selection from the quick picker, tray, or Full History. Cancellation leaves that selection intact. A new external copy resets to #1, including text already in the archive. Arrow and repeated-key navigation wrap within the quick list.
- Resume follows the clipping's ID when deletion changes its position. Deleted clippings, selections outside the quick limit, and disabling Resume last selection fall back to #1 without trimming Full History.
- Selection pastes into the original external editor and avoids Jumpkut's own search and backup filename fields. Sticky mode, terminal paste chords, held modifiers, Caps Lock, and conflicting shortcuts are covered.
- Full History supports exact previews and copying, full-text search, stable selection during new copies, deletion, empty states, and single-instance command routing.
- Preferences and installation preserve saved data. Legacy settings migrate in memory without rewriting the original file until Save.
- The installed command detaches from the terminal. Its desktop entry opens Full History in the same running instance; command-line Show and Quit remain functional.
- Installation handles unusual path characters and refreshes app code without replacing saved history or backups.
- Semantic version bumps, changelog rollover, invalid input, atomic write rollback, and Python bytecode cache invalidation are covered.
- Debian and RPM checks build real archives, inspect package dependencies and assets, extract the application, and run its command without installing onto the host.
- Portable launch checks cover persistent background commands, startup filenames with special characters, extraction-mode startup without FUSE, mount-specific environment replacement, and loader-cache cleanup.

## Installer checks

The Linux packages workflow builds on Ubuntu 22.04, runs the source checks, then installs the `.deb` on its disposable runner and checks its detached launch, application-menu command, single-instance behavior, Show, and Quit. It runs those same commands against the built AppImage in extraction mode, with a private X11 display, D-Bus session, and temporary XDG data. A separate Ubuntu 24.04 job verifies clean startup with newer host GVfs modules installed and repeats the portable desktop commands. RPM checks verify the built archive, dependencies, manifest, and extracted launcher; a Fedora GUI installation is not part of this CI run.

Tag builds validate the tag against the canonical application version before publishing. SHA256SUMS covers all three installers. Release notes select only that version's changelog section, and older tagged reruns preserve automatic date/version ordering for the latest release.

## Visual checks and limits

The quick bezel, tray menu, Preferences, Full History, and About dialog were visually inspected. Current screenshots in `docs/images/` use synthetic demo data.

No linter or static type checker is configured. GTK 3 emits deprecation warnings for its legacy tray API; Cinnamon supports that protocol. Automatic terminal paste selects a chord from common terminal window classes.

This release supports text history on X11. Native Wayland needs a different clipboard and global-shortcut backend.
