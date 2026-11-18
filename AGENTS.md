# Working on Jumpkut

- Jumpkut is a Rust + Slint app. Check changes with `cargo fmt --check`, `cargo clippy --all-targets -- -D warnings`, `cargo test --release`, `/usr/bin/python3 -m unittest discover -s tests -v` (installer, release and packaging tooling), and `./scripts/smoke-test.sh`. Rust is installed per user (`~/.cargo/bin`); `rust-toolchain.toml` pins the toolchain.
- Preserve clipboard history and preferences during installation and updates. `history.json`, `config.json`, backups and the portal restore token keep the format the Python app used; `tests/fixtures` hold files it wrote.
- Keep Alt+C as the quick popup and Show Full History as the separate browsing window.
- Test against isolated data: set `JUMPKUT_DATA_DIR`, `JUMPKUT_CONFIG_DIR`, `JUMPKUT_STATE_DIR`, `JUMPKUT_AUTOSTART_DIR`, `JUMPKUT_BUS_NAME`, `JUMPKUT_KDE_COMPONENT` and `JUMPKUT_APP_ID` for test instances, and use a shortcut other than the user's.

## Release versioning

- `Cargo.toml` (`[package] version`) is the sole source of the application version. The app reads it with `env!("CARGO_PKG_VERSION")`; the packaging scripts read `Cargo.toml`.
- For each completed user-visible change, add concise notes under `[Unreleased]` in `CHANGELOG.md`, then run `./scripts/release.py patch` for fixes, `minor` for new features, or `major` for breaking changes. It bumps `Cargo.toml` and `Cargo.lock`.
- Bump once per completed request, after its implementation and checks. Internal edits during the same request share that release.
- Keep the version visible in About, Preferences, the tray tooltip, and `jumpkut --version` from that single value.
- Application release versions and the clipboard backup format's schema version are independent.
- Reinstall with `./install.py` when updating this user's installed app (it builds with cargo). Quit a running instance first, only when its history is persisted; preserve temporary history.
