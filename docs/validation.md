# Validation

The Rust app is checked by unit tests, tooling tests, a display smoke test, and end-to-end runs in isolated sessions. GitHub Actions runs the first three on every push.

## Automated checks

| Suite | Command | Covers |
|---|---|---|
| Unit tests | `cargo test --release` | `history.json`, `config.json` and backup files byte-for-byte identical to the Python app's (fixtures in `tests/fixtures` were written by the Python code), legacy-preference migration, backup merge, corrupt-file preservation, selection/cycle logic, terminal detection |
| Lint | `cargo clippy --all-targets -- -D warnings`, `cargo fmt --check` | |
| Tooling | `/usr/bin/python3 -m unittest discover -s tests -v` | `install.py` upgrade from a Python-era install (history kept, old app removed, startup entry repointed), `release.py` bumps, `.deb`/`.rpm` contents and dependencies |
| Smoke | `./scripts/smoke-test.sh [LAUNCHER]` | Start on a private Xvfb display and D-Bus session, single-instance forwarding, clean quit, history untouched |

## End-to-end runs (manual)

These used temporary XDG directories, a private D-Bus bus, and the helpers in `examples/` (`fake_keys`, `paste_target`):

- **KDE Plasma 6.8 Wayland**, nested `kwin_wayland --virtual`: clipboard recording (a `--sensitive` copy skipped), hold/cycle/release, arrows with wrap-around, Esc, resume last selection and its reset on a new copy, paste with KWin fake input, Ctrl+Shift+V for a terminal class, Full History search/copy/delete, Preferences saved and read back by the Python reader, command forwarding, refusal while another process owns the instance name.
- **Live Plasma 6.8 session** (separate test instance): popup type `Notification` (no open animation) with blur, Full History, Preferences, About, tray menu, paste via fake input and via uinput into a throwaway window with no portal dialog or notice.
- **X11** (Xvfb with real XTEST): recording with the sensitive check, hold/cycle/release, XTEST paste read back by the target window, Ctrl+Shift+V for an `xterm` class, re-grab after a keyboard-layout change.

## Responsiveness

Measured with `JUMPKUT_DEBUG=1` on Plasma 6.8 Wayland: shortcut to first frame of the popup 13–31 ms, to keyboard focus 14–34 ms; on X11 (Xvfb) a repeat show draws in about 2 ms. Idle memory is about 120 MiB RSS / 57 MiB PSS, most of it the GL driver, because the popup's OpenGL context is created ahead of the first press.
