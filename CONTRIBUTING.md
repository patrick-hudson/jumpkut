# Contributing to Jumpkut

Bug reports, small fixes, and thoughtful feature proposals are welcome. Jumpkut is a native clipboard manager for Linux on X11 and KDE Plasma Wayland, written in Rust with Slint; the quick picker, tray menu, and Full History ## Get started

Install Rust for your user with [rustup](https://rustup.rs); `rust-toolchain.toml` pins the exact toolchain and rustup installs it on first use. No system `-devel` packages are needed.

```sh
git clone https://github.com/patrick-hudson/jumpkut.git
cd jumpkut
cargo build --release
```

To try your build, quit any running Jumpkut (commands otherwise go to the running instance) and run `./target/release/jumpkut --daemon`, which keeps diagnostic output in the terminal. Back up your history before testing changes against your normal desktop session. To keep a test instance away from your real data, set `JUMPKUT_DATA_DIR`, `JUMPKUT_CONFIG_DIR`, `JUMPKUT_STATE_DIR` and `JUMPKUT_AUTOSTART_DIR` to temporary directories, and `JUMPKUT_BUS_NAME`, `JUMPKUT_KDE_COMPONENT` and `JUMPKUT_APP_ID` to test names. `JUMPKUT_DEMO=1` uses demo clippings and records nothing; `JUMPKUT_DEBUG=1` logs show latency and window tracking.

## Check your changes

```sh
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --release
/usr/bin/python3 -m unittest discover -s tests -v
./scripts/smoke-test.sh
```

The smoke test needs `Xvfb` and `dbus-run-session`; it uses a disposable display, a private session bus, and temporary history and preferences. Keep tests focused: data-format compatibility, backup merge, and selection logic are unit-tested; verify integration by running the app. Use synthetic clipboard text in tests and screenshots.

ting or refactoring.

## Open an issue or pull request

For bugs, include the Jumpkut version, distribution, desktop environment, session type, and steps to reproduce with sample text. Explain what you expected and what happened. Wayland is supported on KDE Plasma only (`src/kde.rs`).

For a pull request, describe the problem, the resulting behavior, and the checks you ran. Preserve stored history and preferences during updates. Keep **Alt+C** as the quick popup and **Show Full History** as the separate browser. Quick and tray limits must never trim the full archive.

## Release changes

### Build installers

Build the binary first, then the packages. Native package builders need `dpkg-deb` and `rpmbuild`; the AppImage builder needs `curl` and `desktop-file-validate` and downloads pinned, checksum-verified tools into a temporary directory. On Ubuntu or Mint:

```sh
sudo apt install dpkg-dev rpm cpio binutils desktop-file-utils curl
cargo build --release --locked
./scripts/build-deb.py --output-dir dist
./scripts/build-rpm.py --output-dir dist
./scripts/build-appimage.sh --output-dir dist
./scripts/smoke-test.sh "$PWD/dist/Jumpkut-VERSION-x86_64.AppImage"
```

Official releases build on **Ubuntu 22.04** for a glibc 2.35 baseline. Building on a newer distribution raises the minimum runtime requirement (the `.deb` records it automatically). See [docs/packaging.md](docs/packaging.md) for package contents and dependencies.

### Publish a release

For a user-visible change, add concise release notes under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md). The application version lives only in `Cargo.toml` (`[package] version`); the app and the packaging scripts read it from there.

When a change is complete and its checks pass, make one version bump for the request:

```sh
./scripts/release.py patch
```

Use `patch` for fixes, `minor` for new features, or `major` for breaking changes. The helper moves pending notes into a dated release entry and updates the version in `Cargo.toml` and `Cargo.lock`. The clipboard backup schema has its own version and does not change with an application release.

Commit the release version and changelog, land the change on `main`, then push an annotated tag matching the canonical version:

```sh
version=$(./target/release/jumpkut --version | cut -d' ' -f2)
git tag -a "v$version" -m "Jumpkut $version"
git push origin "v$version"
```

The **Linux packages** action validates the tag against the application version, runs tests, builds the `.deb`, `.rpm`, and `.AppImage`, and tests the installed Debian package and portable AppImage. It publishes a GitHub release with the installers and SHA256SUMS only after those checks pass. A mismatched tag fails without publishing. Untagged pushes, pull requests, and manual runs create downloadable Actions artifacts without cutting a release.

Build jobs have read-only repository access; only the tag-triggered publishing job receives write access. Re-running a tagged build updates that release's package assets.

To update a local installation, quit Jumpkut and run `./install.py`. It preserves history and preferences. Quit a running instance only after its history is persisted; preserve any temporary history first.
