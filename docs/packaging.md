# Linux packaging

Jumpkut ships three installers from the [Linux packages workflow](https://github.com/patrick-hudson/jumpkut/actions/workflows/packages.yml). See [CONTRIBUTING](../CONTRIBUTING.md#build-installers) for build commands and [README](../README.md#installation) for installation.

| Artifact | Architecture | Runtime |
|---|---|---|
| `jumpkut_VERSION_amd64.deb` | Debian `amd64` | glibc 2.35+ and the system libraries below |
| `jumpkut-VERSION-1.x86_64.rpm` | RPM `x86_64` | The Fedora libraries below |
| `Jumpkut-VERSION-x86_64.AppImage` | x86_64 | Host glibc 2.35+; the same libraries from the host |

## What the packages contain

All formats ship one self-contained binary, `jumpkut`, plus a desktop entry, the icon, the licence, the changelog and an optional udev rule (documentation only). Native packages install `/usr/bin/jumpkut`, `/usr/share/applications/jumpkut.desktop` and `/usr/share/icons/hicolor/scalable/apps/jumpkut.svg`. They contain no saved history, preferences, services, or installation/removal scripts. User data stays in the normal XDG locations.

The binary links only to glibc and libgcc_s. It loads these libraries at run time, so the packages declare them explicitly:

| Purpose | Debian/Ubuntu | Fedora |
|---|---|---|
| OpenGL/EGL (the translucent popup) | `libegl1`, `libgl1` | `libglvnd-egl`, `libglvnd-glx` |
| Wayland | `libwayland-client0`, `libwayland-egl1` | `libwayland-client`, `libwayland-egl` |
| Keyboard maps | `libxkbcommon0`, `libxkbcommon-x11-0` | `libxkbcommon`, `libxkbcommon-x11` |
| X11 | `libx11-6`, `libx11-xcb1`, `libxcursor1`, `libxi6`, `libxrender1` | `libX11`, `libX11-xcb`, `libXcursor`, `libXi`, `libXrender` |
| Fonts | `libfontconfig1` | `fontconfig` |
| KDE Wayland clipboard (recommended) | `wl-clipboard (>= 2.3.0)` | `wl-clipboard >= 2.3.0` |

The Debian `libc6` requirement is read from the binary's symbol versions at build time. Builders stage files in temporary directories and do not need root.

The desktop entry has `StartupWMClass=jumpkut` (the app id) and `X-KDE-Wayland-Interfaces=org_kde_kwin_fake_input`. KWin 6.8 offers its fake-input protocol to every unsandboxed native client; older KWin versions grant it only to the executable named in a desktop file's `Exec`, so the packaged entry uses `jumpkut` from `PATH`.

## AppImage

The AppImage contains the binary, `AppRun`, the desktop entry, the icon and licence notices; nothing else needs bundling. Release builds run on Ubuntu 22.04 for a glibc 2.35 baseline. `AppRun` sets `JUMPKUT_LAUNCHER` to the persistent AppImage path, which the background start and the **Run at startup** entry use instead of the temporary mount. Because that mount path changes on every run, an older KWin that checks desktop files cannot grant fake input to the AppImage; there Jumpkut falls back to uinput, then to the portal.

The build script pins and verifies [appimagetool](https://github.com/AppImage/appimagetool) `1.9.1` and the [type2 runtime](https://github.com/AppImage/type2-runtime) `20251108` with SHA256 values.

## uinput fallback

On KDE Plasma Wayland, Jumpkut pastes in this order and logs the method it uses:

1. KWin's `org_kde_kwin_fake_input` protocol (no prompt, no notice);
2. a `/dev/uinput` virtual keyboard, if the signed-in user may write to `/dev/uinput`;
3. the RemoteDesktop portal (asks once; KDE shows a remote-control notice while the session runs).

Many systems already give the active seat's user access to `/dev/uinput` (`getfacl /dev/uinput` lists your user). Otherwise, an administrator can install the rule shipped as `/usr/share/doc/jumpkut/70-jumpkut-uinput.rules`:

```sh
sudo install -m 644 /usr/share/doc/jumpkut/70-jumpkut-uinput.rules /etc/udev/rules.d/
echo uinput | sudo tee /etc/modules-load.d/jumpkut-uinput.conf
sudo modprobe uinput && sudo udevadm control --reload && sudo udevadm trigger --name-match=uinput
```

The rule (`KERNEL=="uinput", SUBSYSTEM=="misc", TAG+="uaccess", OPTIONS+="static_node=uinput"`) grants access only to the user at the active local seat. Neither the packages nor `install.py` install it automatically.

## Release automation

Pushes, pull requests, and manual runs build packages and save them as a `linux-packages` Actions artifact. A push of `vX.Y.Z` validates the tag against the version in `Cargo.toml`, runs the checks, builds all formats, and runs a start/forward/quit smoke test against the installed `.deb` and the AppImage. The AppImage is also tested on Ubuntu 24.04.

Only after the build succeeds does a separate job publish the installers and `SHA256SUMS` to the matching GitHub release. New releases start as drafts, become public after all assets upload, and use the version's changelog notes. Build jobs have read access; the publishing job alone has write access.
