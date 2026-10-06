# Linux packaging

Jumpkut ships three installers from the [Linux packages workflow](https://github.com/patrick-hudson/jumpkut/actions/workflows/packages.yml). See [CONTRIBUTING](../CONTRIBUTING.md#build-installers) for build commands and [README](../README.md#installation) for installation.

| Artifact | Architecture | Runtime |
|---|---|---|
| `jumpkut_VERSION_all.deb` | Debian `all` | System Python 3.10+, GTK 3, PyGObject, Xlib, SVG loader |
| `jumpkut-VERSION-1.noarch.rpm` | RPM `noarch` | Fedora Python 3.10+, GTK 3, PyGObject, Xlib, librsvg |
| `Jumpkut-VERSION-x86_64.AppImage` | x86_64 | Bundled Python and GTK; host glibc 2.35+ and X11 desktop |

## Native package layout

Both native packages install the application into `/usr/share/jumpkut/app`, the command into `/usr/bin/jumpkut`, a desktop entry into `/usr/share/applications`, and the scissors icon into the hicolor icon theme. They contain no saved history, preferences, services, or installation/removal scripts. User data stays in the normal XDG locations.

Builders stage files in temporary directories and do not need root. Package checks build and extract real archives, run the extracted command, and verify dependencies, versions, assets, ownership, permissions, and absence of user data.

## AppImage runtime

Official builds use Ubuntu 22.04 and its Python 3.10 runtime. Building on a newer system can increase the required glibc version. The AppImage contains the interpreter, standard library, PyGObject, Xlib, GTK/GDK libraries, introspection metadata, GLib schemas, and image/input-method loaders.

`AppRun` derives paths from its current mount and replaces inherited Python and GTK bundle paths. Loader caches are private to each run and removed when it exits. It keeps the user's XDG config, data, and state directories.

`JUMPKUT_LAUNCHER` identifies the persistent AppImage, or `AppRun` in a manually extracted directory. Detached launch invokes that launcher again so the child has its own runtime. Startup entries point to the persistent file instead of a temporary mount.

GIO uses an empty module directory inside the image and its built-in local file backend. This prevents newer host GVfs plugins from loading against the bundled GLib. Local files and mounted directories remain available for backups.

The build script pins and verifies these upstream tools before running them:

- [linuxdeploy](https://github.com/linuxdeploy/linuxdeploy), release `1-alpha-20251107-1`.
- [GTK plugin](https://github.com/linuxdeploy/linuxdeploy-plugin-gtk), commit `7a3fbc31a9e5075073ff8790f26effbac5f84453`.
- [appimagetool](https://github.com/AppImage/appimagetool), release `1.9.1`.
- [type2 runtime](https://github.com/AppImage/type2-runtime), release `20251108`.

SHA256 values are recorded in the build script. Third-party copyright notices, common license texts, and binary/source package versions are included under `usr/share/doc/jumpkut` in the image. The build does not read clipboard history or preferences.

## Release automation

Pushes, pull requests, and manual runs build packages and save them as a `linux-packages` Actions artifact. A push of `vX.Y.Z` validates the tag against `jumpkut/__init__.py`, runs the headless/native checks, builds all formats, and runs background/desktop command checks against the installed `.deb` and portable AppImage. The same AppImage also runs on Ubuntu 24.04 with newer GVfs plugins installed; module loader errors block publication.

Only after the build succeeds does a separate job publish the installers and `SHA256SUMS` to the matching GitHub release. New releases start as drafts, become public after all assets upload, and use the version's changelog notes. Tagged reruns replace package assets on that release. Build jobs have read access; the publishing job alone has write access.

Native Wayland is not supported. Native package architectures describe the Python payload; the AppImage currently targets x86_64 only.
