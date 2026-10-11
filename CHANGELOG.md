# Changelog

## [Unreleased]

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
