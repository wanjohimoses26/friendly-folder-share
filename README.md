# Friendly Folder Share

Friendly Folder Share is a single-file Python application that lets an owner share one selected folder over the same Wi‑Fi or through a temporary secure internet link. People sign in with the owner-provided password, choose a nickname, and can manage only the shared folder.

## Run it

Download [`sharer.py`](sharer.py), then run `python3 sharer.py`. The app can install the per-user `sharer` launcher; after opening a new terminal, run `sharer`.

The owner controls connected devices from the desktop Folder Share window. Connected web users do not receive the owner panel.

## How updates work

Stable updates are published as GitHub Releases. The app must use release assets, not the mutable `main` branch. Updates are checked over HTTPS, verified against the published SHA-256 file, compiled before installation, backed up, and installed atomically. The `sharer` command remains the same.

People using an older copy need to download the latest `sharer.py` once. After that, the built-in updater can manage later releases.

## Security

Share only a folder you are comfortable exposing. Use a unique strong password. Quick Tunnel links are temporary and can change each time sharing starts. Never commit passwords, session tokens, private keys, or personal files.

Anyone who controls this repository can publish code that users may run through the updater. Protect the GitHub account with 2FA, protect the `main` branch, review workflows, and use signed tags where possible. This application sends no telemetry.

## Troubleshooting

If `sharer` is unknown, open a new terminal after the launcher is installed. The desktop notification shows the exact launcher path. You can always run `python3 sharer.py` directly.

## License

MIT. See [LICENSE](LICENSE).
