# Changelog

All notable changes to Friendly Folder Share are documented here. Notes for the
next release go under **Unreleased**; `release.sh` moves them under the new version.
A line `min_version: X.Y.Z` or `critical: true` inside a release's notes makes the
app show a stronger "please update" message (those lines are hidden from the notes people see).

## [Unreleased]

## [2.6.1] - 2026-11-19

### Fixed
- Moved the Connected devices button into the visible sharing controls so it is not hidden below the window.

## [2.6.0] - 2026-11-19

### Security
- File names can no longer inject script into the web page.
- Wrong-password limits now work behind the internet link (they used to share one bucket for everyone) and slow down with each failure.
- Passwords are stored as a salted PBKDF2 hash instead of plain SHA-256.
- The server only listens on this computer unless "same Wi-Fi access" is ticked, so the plain-HTTP login is not exposed on the network.
- The QR code contains only the link unless you tick "Include the password".
- Cross-site requests are rejected; extra browser security headers added; error messages no longer reveal the owner's folder paths.

### Fixed
- Downloads stream straight to disk instead of filling the phone's memory.
- Uploads: name clashes are handled before upload starts ("name (1).ext"); free disk space is checked first; bigger chunks with automatic retries; correct speed after resume.
- Rename / new folder can no longer overwrite anything or use reserved or invalid names.
- The listing no longer crashes on broken links or unreadable files; links leaving the shared folder are hidden.
- Non-English file names download with the right name.
- The internet link no longer stalls on long sessions, is restored automatically (up to 3 tries) if it drops, and fails clearly after 30 seconds instead of spinning forever.
- Update check: an update you had not installed yet was never shown again after the first check.
- HEAD requests work; unsupported methods get a proper 405.
- Window updates from background threads are now thread-safe.

### Changed
- Updater: release notes, "Remind me later" / "Skip this version", re-check every 6 hours, step-by-step progress, and automatic rollback if a new version fails to start.
- The `sharer` command is only added after you say yes, and can be removed again.
- Password Show/Hide, Generate and Copy; "Copy link" and "Copy message for a friend".
- Removed dead code (old upload route, unused transfer logic).

## [2.5.3] - 2026-11-18
- Version bump used to test the updater.

## [2.5.2] - 2026-11-18
### Fixed
- Accept GitHub's release-assets redirect host during verified updates.

## [2.5.1] - 2026-11-18
### Added
- GitHub Releases update checking with SHA-256, syntax, version, host, backup and atomic-install checks, and a desktop update banner.

## [2.5.0] - 2026-11-18
Never published as a release; copies of this version have no updater and must download the latest `sharer.py` once.
### Added
- Nickname sign-in, owner-only connected-device panel, recursive search, release workflow.
