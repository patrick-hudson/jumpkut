# Working on Jumpkut

- Use `/usr/bin/python3` for checks; the desktop dependencies are installed for the system Python.
- Preserve clipboard history and preferences during installation and updates.
- Keep Alt+C as the quick popup and Show Full History as the separate browsing window.

## Release versioning

- `jumpkut/__init__.py` is the sole source of the application version. Package metadata reads it dynamically.
- For each completed user-visible change, add concise notes under `[Unreleased]` in `CHANGELOG.md`, then run `./scripts/release.py patch` for fixes, `minor` for new features, or `major` for breaking changes.
- Bump once per completed request, after its implementation and checks. Internal edits during the same request share that release.
- Keep the version visible in About, Preferences, the tray tooltip, and `jumpkut --version` by importing the canonical value.
- Application release versions and the clipboard backup format's schema version are independent.
- Reinstall with `./install.py` when updating this user's installed app. Reload a running instance only when its history is persisted; preserve temporary history.
