# Changelog

All notable changes to Friendly Folder Share are documented here.

## [2.5.0] - 2026-11-18

### Added

- Nickname sign-in for connected web users.
- Owner-only connected-device panel in the desktop GUI.
- Device heartbeat, online/away status, and disconnect control.
- Recursive file and folder search in the web file manager.
- Initial GitHub Release workflow and update-channel documentation.

### Security

- Connected web users cannot access the owner device-control panel.

[Unreleased]: https://github.com/wanjohimoses26/friendly-folder-share/compare/v2.5.0...HEAD

## [2.5.1] - 2026-11-18

### Added

- GitHub Releases update checking with ETag caching.
- SHA-256, syntax, version, host, backup, and atomic-install checks for updates.
- Non-blocking desktop update banner.

## [2.5.2] - 2026-11-18

### Fixed

- Accept GitHub’s official release-assets redirect host during verified updates.
