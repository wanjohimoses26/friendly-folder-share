# Friendly Folder Share

Share one folder from your computer with friends: they open a link, type a password, and can download, upload, rename and delete files in that folder only. One file (`sharer.py`), nothing to install except Python.

## Install and run

1. Install **Python 3.9 or newer** from [python.org](https://www.python.org/downloads/) (Windows: tick "Add Python to PATH"; Linux: `sudo apt install python3 python3-tk`).
2. Download [`sharer.py`](https://github.com/wanjohimoses26/friendly-folder-share/releases/latest/download/sharer.py).
3. Run it: **Windows** `python sharer.py` · **macOS/Linux** `python3 sharer.py`.

_Screenshot placeholders: `docs/main-window.png`, `docs/web-page.png`._

## Sharing

1. Choose a folder, then type a password (12+ characters) or press **Generate**.
2. Press **Start secure sharing**. Keep the window open; closing it stops sharing.
3. Press **Copy message for a friend** and send it. The internet link changes every time you start sharing.

## FAQ

- **Is it safe?** Only the chosen folder is reachable, behind a password, over HTTPS. Share only folders you are happy to expose.
- **Does it cost anything or send data to the author?** No. There is no telemetry.
- **Same Wi-Fi only?** Untick the internet link and tick the same-Wi-Fi option. Same-Wi-Fi access is not encrypted.
- **Why does the link change?** It uses a free Cloudflare Quick Tunnel: fine for casual sharing, no uptime guarantee.

## How updates work

Updates come from GitHub Releases only. The app checks at start and every 6 hours, downloads `sharer.py` and its `.sha256`, verifies the checksum, that it is valid Python and the right version, backs up the old file (last 3 kept in `~/.friendly_folder_share/backups`), and installs it atomically. If a new version fails to start, the previous one is restored automatically. Updates are never installed while you are sharing. Copies older than 2.5.1 have no updater: download the latest `sharer.py` once, and updates are automatic after that.

## Troubleshooting

- **`sharer` command not found:** open a new terminal, or run `python3 sharer.py` directly.
- **Internet link will not start:** antivirus or a firewall may block the `cloudflared` component in `~/.friendly_folder_share`; allow it, or use same-Wi-Fi sharing.
- **Log file:** `~/.friendly_folder_share/folder_share.log`.

## For the owner: releasing

Write notes under `## [Unreleased]` in `CHANGELOG.md`, then run `./release.sh 2.6.0` (Windows: `release.bat 2.6.0`). It bumps `APP_VERSION`, runs the tests, commits, tags and pushes; GitHub Actions tests on Python 3.9 and 3.12 and publishes the release. Put `min_version: X.Y.Z` or `critical: true` on its own line in the notes to force a stronger update prompt.

**Anyone who controls this repository controls what runs on every user's computer.** Turn on 2FA, protect `main` (no force-push or deletion), sign tags if you can, review workflow changes, and never commit passwords or tokens.

## License

MIT, see [LICENSE](LICENSE).
