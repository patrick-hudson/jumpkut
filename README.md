<p align="center">
  <img src="docs/images/banner.svg" alt="Jumpkut — your clipboard, a shortcut away" width="100%">
</p>

<p align="center">
  <a href="https://github.com/patrick-hudson/jumpkut/actions/workflows/tests.yml"><img src="https://github.com/patrick-hudson/jumpkut/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/patrick-hudson/jumpkut/actions/workflows/packages.yml"><img src="https://github.com/patrick-hudson/jumpkut/actions/workflows/packages.yml/badge.svg?branch=main" alt="Linux packages"></a>
  <a href="https://github.com/patrick-hudson/jumpkut/releases/latest"><img src="https://img.shields.io/github/v/release/patrick-hudson/jumpkut?color=3d8fd6" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-3d8fd6" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/desktop-X11%20%7C%20KDE%20Wayland-3d8fd6" alt="X11 or KDE Plasma Wayland">
  <img src="https://img.shields.io/badge/built%20with-Rust%20%2B%20Slint-3d8fd6" alt="Built with Rust and Slint">
</p>

<p align="center">
  <strong>A small, keyboard-first clipboard manager for Linux.</strong><br>
  Copy text. Press <strong>Alt+C</strong>. Find the clipping you need. Keep moving.
</p>

<p align="center">
  <a href="#installation">Install</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#screenshots">Screenshots</a> ·
  <a href="#preferences">Preferences</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

Jumpkut brings the quick, single-clipping picker of [Jumpcut](https://snark.github.io/jumpcut/) to Linux, with a tray icon, a searchable history browser, and portable backups. It is a single native binary written in Rust with the [Slint](https://slint.dev) toolkit: no Python, GTK or Qt at run time.

**Runs on X11 desktops and on KDE Plasma 6 Wayland** ([details](#kde-plasma-wayland)). Other Wayland desktops are not supported yet. Jumpkut records text, including Unicode and multiline content; images and files are not recorded.

## How it works

1. **Copy** text in any app. Jumpkut saves it in the background.
2. **Press Alt+C** and keep Alt held. The picker starts at the newest clipping, **#1**.
3. **Browse with ↑ / ↓** or press **C** again to cycle.
4. **Release Alt** to select and paste into your previous app. **Esc** cancels.

Prefer to choose without holding a key? Enable **Keep history open until Enter** in Preferences.

| A place for every clipping | What you get |
|---|---|
| **Quick picker** | A Jumpcut-style bezel with one preview at a time; the newest **30** clippings by default. |
| **System tray** | The newest **10** clippings, plus Full History, Preferences, Pause Recording, backups, and Quit. |
| **Full History** | Every saved clipping, with search, full previews, copying, and deletion. |
| **Your preferences** | A configurable shortcut, independent optional quick/tray limits, startup, and paste behavior. |
| **Backups** | Export the complete archive to JSON; restore and merge without losing existing clippings. |
| **Background launch** | One `jumpkut` command, a desktop app-menu entry, and optional startup at login. |

## Screenshots

These are real screenshots of the Rust app (Jumpkut **0.8**) on KDE Plasma 6 (Breeze Dark), using demo clippings.

<table>
  <tr>
    <td align="center" width="50%"><strong>Quick picker</strong><br><br><img src="docs/images/quick-picker.png" alt="Alt+C quick picker, showing the newest clipping numbered 1" width="340"></td>
    <td align="center" width="50%"><strong>System tray</strong><br><br><img src="docs/images/tray-menu.png" alt="Tray menu with recent clippings and the app options" width="370"></td>
  </tr>
</table>

<p align="center"><strong>Full History — search, read, copy, and organize the complete archive</strong></p>
<p align="center"><img src="docs/images/full-history.png" alt="Searchable Full History browser with a clipping list and complete text preview" width="940"></p>

<table>
  <tr>
    <td align="center" width="50%"><strong>Preferences & backups</strong><br><br><img src="docs/images/preferences.png" alt="Preferences with Alt+C, independent default limits of 30 and 10, startup, and backup controls" width="350"></td>
    <td align="center" width="50%"><strong>Version information</strong><br><br><img src="docs/images/about.png" alt="About Jumpkut showing the version and the Made with Slint credit" width="420"></td>
  </tr>
</table>

## Installation

Download an installer from **[the latest release](https://github.com/patrick-hudson/jumpkut/releases/latest)**. All formats run on an **X11 session** or **KDE Plasma Wayland**, and support the same clipboard history, preferences, and backups.

| Format | Use it on | What is included |
|---|---|---|
| **.deb** | Linux Mint, Ubuntu 22.04+, Debian 12+ (x86_64) | System-wide command, application-menu entry, and icon; apt installs the few system libraries it uses. |
| **.rpm** | Fedora (x86_64) | System-wide command, application-menu entry, and icon; dnf installs the few system libraries it uses. |
| **.AppImage** | x86_64 Linux with glibc 2.35+ | The single Jumpkut binary; graphics, Wayland/X11 and font libraries come from your system. |

### Debian, Ubuntu & Linux Mint

From the directory containing your downloaded `.deb`:

```sh
sudo apt install ./jumpkut_*_amd64.deb
jumpkut
```

### Fedora

From the directory containing your downloaded `.rpm`:

```sh
sudo dnf install ./jumpkut-*.x86_64.rpm
jumpkut
```

Both native packages add **Jumpkut** to your desktop's application menu. Open it there to browse Full History. Installing a later package updates the app and preserves your saved history and preferences.

### Portable AppImage

Save the AppImage somewhere permanent, such as `~/Applications`, then make it executable and run it:

```sh
chmod +x Jumpkut-*-x86_64.AppImage
./Jumpkut-*-x86_64.AppImage
```

It starts in the background. Append `--history` to open Full History or `--preferences` to configure it. Enable **Run at startup** after placing the file in its permanent location. If you move or rename the AppImage, disable and re-enable startup from its new location.

If your desktop cannot mount AppImages with FUSE, use the runtime's extraction mode:

```sh
APPIMAGE_EXTRACT_AND_RUN=1 ./Jumpkut-*-x86_64.AppImage
```

Release downloads include **SHA256SUMS**. Put it beside the downloaded packages and run `sha256sum --check --ignore-missing SHA256SUMS` to verify them. Source `.zip` and `.tar.gz` archives are also available on each release page.

### KDE Plasma Wayland

On Plasma 6 Wayland (for example Fedora KDE), Jumpkut is a native Wayland app and uses KDE session services for the parts Wayland keeps private:

- **Shortcut** — Alt+C is registered with KDE's global shortcut service. It also appears in **System Settings → Keyboard → Shortcuts → Jumpkut**; change it in Jumpkut's Preferences.
- **Clipboard** — `wl-paste --watch` records copies from every app, and `wl-copy` sets the chosen clipping. It needs **wl-clipboard 2.3 or newer** (`sudo dnf install wl-clipboard`). Copies that carry the KDE password-manager hint (`x-kde-passwordManagerHint`, set by KeePassXC and similar apps) are skipped; other password copies are recorded like any text.
- **Popup** — appears instantly, without KWin's window-open animation, with KDE's blur behind it and your accent colour.
- **Paste automatically** — types Ctrl+V (Ctrl+Shift+V in terminals such as Konsole and Ghostty) through KWin's own input protocol. There is **no permission prompt and no remote-control notice**. If KWin does not offer that protocol (older Plasma), Jumpkut uses a `/dev/uinput` keyboard when your account may write to it, and only then the RemoteDesktop portal, which asks once. See [packaging notes](docs/packaging.md#uinput-fallback). If you turn **Paste automatically** off, Jumpkut copies the clipping and you paste it yourself.

### Install from source

Jumpkut builds with Rust (stable, installed for your user with [rustup](https://rustup.rs)); no system `-devel` packages are needed. The exact toolchain version is pinned in `rust-toolchain.toml` and installed automatically.

```sh
curl https://sh.rustup.rs -sSf | sh        # once, if cargo is not installed
git clone https://github.com/patrick-hudson/jumpkut.git
cd jumpkut
./install.py
jumpkut
```

On KDE Plasma Wayland, also install wl-clipboard 2.3 or newer. Run the installer as your normal user; it never needs `sudo`.

Plain `jumpkut` starts in the **background** and returns immediately. The app stays in the tray after you close the terminal. You can also open **Jumpkut** from your desktop's application menu to show Full History.

If your shell cannot find the command, add the user bin directory to your shell's PATH:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Add that line to your shell's startup file to keep it across terminals. Enable **Run at startup** in Preferences to start Jumpkut when you sign in.

KDE Plasma and Linux Mint Cinnamon support the tray protocol Jumpkut uses (StatusNotifierItem). GNOME needs a tray extension such as AppIndicator.

<details>
<summary><strong>Installation paths and running from source</strong></summary>

The installer builds `target/release/jumpkut` with cargo and creates:

| Item | Location |
|---|---|
| Command (the binary) | `~/.local/bin/jumpkut` |
| App-menu entry | `~/.local/share/applications/jumpkut.desktop` |
| Icon | `~/.local/share/icons/hicolor/scalable/apps/jumpkut.svg` |

Use `./install.py --prefix /some/directory` for a custom installation base, or `--binary FILE` to install an already-built binary. You can run from a source checkout with `cargo run --release -- --daemon` without installing.

</details>

## Keyboard controls

| In the quick picker | Action |
|---|---|
| **Alt+C** | Resume the last selected clipping, or start at the newest after a new copy; repeated C cycles while Alt is held. |
| **↑ / ↓** | Browse recent clippings. |
| **Release Alt** | Select and automatically paste, with default preferences. |
| **Enter** | Select in sticky mode or when opened with `--show`. |
| **Home / End** | Jump to the newest / oldest clipping in the picker. |
| **Delete** | Delete the selected clipping from saved history. |
| **Esc** | Cancel. |

In **Full History**, use arrows and Page Up / Page Down to browse, **Enter** or double-click to copy, and **Ctrl+F** to search. Copying leaves the browser open and preserves history order; paste into your other app when ready.

Automatic paste uses Ctrl+V, or Ctrl+Shift+V for recognized terminal windows. You can disable it in Preferences and paste the selected clipboard text yourself.

## Preferences

Click the Jumpkut tray icon and choose **Preferences**.

| Setting | Default | Customize it |
|---|---|---|
| Keyboard shortcut | **Alt+C** | Click the shortcut button and press a new combination. |
| Quick picker length | **30** | Check **Custom quick history length**, then choose 1–10,000. |
| Tray menu length | **10** | Check **Custom tray history length**, then choose 1–10,000. |
| Full History | **All saved clippings** | Delete individual entries or use Clear History. |
| Run at startup | **Off** | Enable it to start at login. |
| Automatic paste | **On** | Disable it to copy selections without pasting. |
| Resume last selection | **On** | Reopen the last selected clipping until you copy something new. Disable it to always start at #1. |
| Keep picker open until Enter | **Off** | Enable it to choose after releasing the shortcut. |

The two custom-length checkboxes start unchecked. Unchecking one restores that view's default and remembers its custom value for later. **Changing either limit never removes older clippings from Full History.**

A shortcut already in use produces an error. Changes take effect when you save.

During the current session, the quick picker remembers the clipping you last selected from the picker, tray, or Full History. Selecting through Jumpkut keeps the original copy order and does not count as a new copy. Browsing and canceling leaves that selection unchanged. Copying text in another app resets the picker to #1, including copying text already in the archive. If the remembered clipping is deleted or outside the quick history limit, the picker starts at #1. Restarting Jumpkut also starts at #1.

## History & backups

Use **Back Up History…** in the tray, Preferences, or Full History to export the complete archive to a portable JSON file. **Restore Backup…** merges a backup with your existing archive, removes duplicate text, and retains older entries beyond the quick and tray limits.

History keeps all unique clippings until you delete them or clear it. Repeated copies move an existing clipping to the front. Exact whitespace and text are preserved; empty or whitespace-only copies are ignored.

History and backups contain **plain text**. Clippings explicitly marked sensitive by supported password managers are skipped; ordinary clipboard text is stored as copied.

| Saved data | Default location |
|---|---|
| Preferences | `~/.config/jumpkut/config.json` |
| History | `~/.local/share/jumpkut/history.json` |
| Startup entry | `~/.config/autostart/jumpkut.desktop` |
| Background log | `~/.local/state/jumpkut/jumpkut.log` |

Jumpkut respects `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_STATE_HOME`. Clearing history keeps exported backups and leaves the current system clipboard unchanged.

## Commands

```sh
jumpkut                # Start in the background
jumpkut --show         # Open the quick picker
jumpkut --history      # Open Full History
jumpkut --preferences  # Open Preferences
jumpkut --about        # Show version and credits
jumpkut --quit         # Quit the running instance
jumpkut --version      # Print the installed version
jumpkut --daemon       # Run attached to the terminal for debugging
```

`--background` explicitly selects the default background behavior. One instance runs per desktop session; commands are forwarded to it.

## Updating

For `.deb` and `.rpm` installations, quit the running app, install the newer downloaded package with the command above, and start Jumpkut again. For AppImage, quit the app, replace the old file, and run the new one. Re-enable startup if its filename changed. Saved history and preferences live in your XDG directories and are shared between installation formats.

If you previously used `./install.py`, its `~/.local/bin/jumpkut` command and `~/.local/share/applications/jumpkut.desktop` menu entry may take precedence over the native package. Quit the old app, rename those two files, and start `/usr/bin/jumpkut`. Then disable and re-enable **Run at startup** in the new app. This does not require removing any saved history.

From your source checkout:

```sh
git pull --ff-only
jumpkut --quit
./install.py
jumpkut
```

Reinstalling preserves saved history, preferences, and backups. Quit the running instance before updating so the new binary starts afterwards. Standard installs persist history to disk; if you disabled persistence in the configuration file, back up the in-memory history before quitting.

**Coming from the Python version (0.7.1 and earlier)?** The Rust app reads and writes the same history, preferences, and backups, so nothing needs converting. Quit the Python app first: the Rust app refuses to start while it runs, so the two never write the same history. `./install.py` replaces the old `~/.local/share/jumpkut/app/` directory and launcher, and points an existing **Run at startup** entry at the new binary; packaged installs update that entry the first time the new version starts.

Older preferences using the previous default of 200 move to the new 30/10 defaults. A saved quick length of 30 uses the unchecked default; other custom quick lengths stay enabled. Entries removed by an older release can be recovered only from a backup containing them.

## Development

```sh
cargo build --release            # target/release/jumpkut
cargo test --release             # data-format compatibility, backup merge, selection logic
cargo clippy --all-targets -- -D warnings
/usr/bin/python3 -m unittest discover -s tests -v   # installer, release, packaging
./scripts/smoke-test.sh          # start, forward and quit on a private Xvfb display
```

The smoke test uses a disposable X11 display, a private session bus, and temporary history and preferences. It does not use your live clipboard. GitHub Actions runs all of these.

The **[Linux packages workflow](https://github.com/patrick-hudson/jumpkut/actions/workflows/packages.yml)** builds all three packages on pushes and pull requests; you can also run it manually. CI tests the native package and AppImage after packaging, then saves the files as an Actions artifact. Pushing a matching `vX.Y.Z` tag publishes the tested installers and checksums as a GitHub release.

Read [CONTRIBUTING.md](CONTRIBUTING.md) for development and releases, [CHANGELOG.md](CHANGELOG.md) for release notes, and [validation notes](docs/validation.md) for test coverage.

## License & credits

[MIT](LICENSE). Inspired by [Jumpcut](https://snark.github.io/jumpcut/) for macOS. Jumpkut is an independent project with its own interface, clipboard implementation, and artwork.

The interface is built with [Slint](https://slint.dev), used under the Slint Royalty-free Desktop, Mobile, and Web Applications License; the About window shows the required “Made with Slint” attribution.
