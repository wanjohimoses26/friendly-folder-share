#!/usr/bin/env python3
"""Secure, user-friendly folder sharing over a local network or the internet.

Run with: python3 sharerv2.py

The program opens a GUI where you choose a folder, then starts a password-
protected web server. Anyone with the displayed URL and password can manage
files inside that folder only. Internet mode uses a Cloudflare Quick Tunnel
when the `cloudflared` program is installed. The public URL is HTTPS.
"""

from __future__ import annotations

import base64
import hashlib
import html
import http.server
import ipaddress
import json
import os
import platform
import re
import secrets
import shutil
import socket
import socketserver
import subprocess
import sys
import tarfile
import threading
import time


def bootstrap_tkinter():
    """Install the OS-level GUI dependency before importing tkinter."""
    try:
        import tkinter  # noqa: F401
        return
    except (ImportError, ModuleNotFoundError):
        pass
    print("[Folder Share setup] Python's Tkinter GUI library is missing.", flush=True)
    print("[Folder Share setup] I will try to install it now. Nothing is being shared.", flush=True)
    system = platform.system().lower()
    if system == "linux" and shutil.which("apt-get") and shutil.which("sudo"):
        command = ["sudo", "apt-get", "install", "-y", "python3-tk"]
    elif system == "linux" and shutil.which("dnf") and shutil.which("sudo"):
        command = ["sudo", "dnf", "install", "-y", "python3-tkinter"]
    else:
        print("[Folder Share setup] Automatic installation is not available on this computer.", flush=True)
        print("Install Python with Tk/Tcl included, then run this file again.", flush=True)
        return
    print("[Folder Share setup] Running: " + " ".join(command), flush=True)
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[Folder Share setup] Installation did not finish: {exc}", flush=True)
        print("Please install Python Tkinter from your operating system's app installer, then try again.", flush=True)
        return
    print("[Folder Share setup] Tkinter installed. Restarting the app…", flush=True)
    os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    bootstrap_tkinter()

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ModuleNotFoundError:
    tk = None
    filedialog = messagebox = ttk = None
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

APP_TITLE = "Friendly Folder Share"
APP_VERSION = "2.5.0"
GITHUB_OWNER = "wanjohimoses26"
GITHUB_REPO = "friendly-folder-share"
LAUNCH_COMMAND = "sharer"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB per upload; limited by available disk space
SESSION_SECONDS = 8 * 60 * 60
MIN_PASSWORD_LENGTH = 12
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 8
UPLOAD_PART_MAX_AGE = 24 * 60 * 60
CLOUDFLARED_DIR = Path.home() / ".friendly_folder_share"
SETUP_MARKER = CLOUDFLARED_DIR / ".setup_complete"
LOG_PATH = CLOUDFLARED_DIR / "folder_share.log"
VERSION_PATH = CLOUDFLARED_DIR / "app-version.txt"
NOTICE_DISMISSED_PATH = CLOUDFLARED_DIR / "update-notice-dismissed.txt"
COMMAND_NOTICE_DISMISSED_PATH = CLOUDFLARED_DIR / "command-notice-dismissed.txt"


def install_sharer_command() -> str:
    """Install the per-user `sharer` launcher without requiring admin rights."""
    if os.name == "nt":
        destinations = [Path.home() / "bin" / f"{LAUNCH_COMMAND}.cmd"]
        content = f'@echo off\n"{sys.executable}" "{Path(__file__).resolve()}" %*\n'
    else:
        destinations = [Path.home() / ".local" / "bin" / LAUNCH_COMMAND, Path.home() / "bin" / LAUNCH_COMMAND]
        content = f'#!/bin/sh\nexec "{sys.executable}" "{Path(__file__).resolve()}" "$@"\n'
    for destination in destinations:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.read_text(encoding="utf-8", errors="ignore") != content:
            destination.write_text(content, encoding="utf-8")
            if os.name != "nt": destination.chmod(0o700)
    launcher_dirs = sorted({str(destination.parent) for destination in destinations})
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                try:
                    current_path, value_type = winreg.QueryValueEx(key, "Path")
                except FileNotFoundError:
                    current_path, value_type = "", winreg.REG_EXPAND_SZ
                entries = [item for item in current_path.split(os.pathsep) if item]
                known = {item.casefold() for item in entries}
                for directory in launcher_dirs:
                    if directory.casefold() not in known:
                        entries.append(directory)
                        known.add(directory.casefold())
                winreg.SetValueEx(key, "Path", 0, value_type, os.pathsep.join(entries))
        except (ImportError, OSError):
            pass
    else:
        posix_line = 'export PATH="$HOME/.local/bin:$HOME/bin:$PATH"'
        fish_line = 'set -gx PATH "$HOME/.local/bin" "$HOME/bin" $PATH'
        for startup_file, line in (
            (Path.home() / ".profile", posix_line),
            (Path.home() / ".bashrc", posix_line),
            (Path.home() / ".zshrc", posix_line),
            (Path.home() / ".config" / "fish" / "config.fish", fish_line),
        ):
            try:
                startup_file.parent.mkdir(parents=True, exist_ok=True)
                existing = startup_file.read_text(encoding="utf-8", errors="ignore") if startup_file.exists() else ""
                if line not in existing:
                    startup_file.write_text(existing.rstrip() + "\n\n# Folder Share launcher\n" + line + "\n", encoding="utf-8")
            except OSError:
                pass
    return str(destinations[0])


def record_and_get_previous_version() -> str | None:
    previous = VERSION_PATH.read_text(encoding="utf-8").strip() if VERSION_PATH.exists() else None
    VERSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    VERSION_PATH.write_text(APP_VERSION + "\n", encoding="utf-8")
    return previous


def should_show_update_notice(previous_version: str | None) -> bool:
    if not previous_version or previous_version == APP_VERSION:
        return False
    return not (NOTICE_DISMISSED_PATH.exists() and NOTICE_DISMISSED_PATH.read_text(encoding="utf-8").strip() == APP_VERSION)


def should_show_command_notice(command_path: str) -> bool:
    token = f"{APP_VERSION}|{Path(__file__).resolve()}"
    return bool(command_path) and not (COMMAND_NOTICE_DISMISSED_PATH.exists() and COMMAND_NOTICE_DISMISSED_PATH.read_text(encoding="utf-8").strip() == token)


def detach_from_terminal() -> None:
    """Relaunch a terminal-launched GUI in the background with file logging."""
    if os.environ.get("FFS_DETACHED") == "1":
        CLOUDFLARED_DIR.mkdir(parents=True, exist_ok=True)
        log = LOG_PATH.open("a", encoding="utf-8", buffering=1)
        sys.stdin = open(os.devnull, "r")
        sys.stdout = log
        sys.stderr = log
        return
    try:
        attached = bool(sys.stdout and sys.stdout.isatty())
    except (OSError, AttributeError):
        attached = False
    if not attached:
        return
    CLOUDFLARED_DIR.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    environment = os.environ.copy()
    environment["FFS_DETACHED"] = "1"
    log = LOG_PATH.open("a", encoding="utf-8", buffering=1)
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "close_fds": True, "env": environment}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)
    finally:
        log.close()
    print(f"Folder Share is running independently in the background. Logs: {LOG_PATH}")
    raise SystemExit(0)


def cloudflared_asset_name() -> tuple[str, str]:
    """Return the official GitHub release asset and local filename."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm"}.get(machine)
    if not arch:
        raise RuntimeError(f"This computer's processor ({machine}) is not supported automatically.")
    if system == "windows":
        return f"cloudflared-windows-{arch}.exe", "cloudflared.exe"
    if system == "darwin":
        return f"cloudflared-darwin-{arch}.tgz", "cloudflared"
    if system == "linux":
        return f"cloudflared-linux-{arch}", "cloudflared"
    raise RuntimeError(f"This operating system ({system}) is not supported automatically.")


def ensure_cloudflared(status_callback=None) -> str:
    """Find cloudflared or download the official latest release automatically."""
    def report(text, fraction=None):
        if status_callback:
            status_callback(text, fraction)

    existing = shutil.which("cloudflared")
    if existing:
        return existing
    asset, filename = cloudflared_asset_name()
    CLOUDFLARED_DIR.mkdir(parents=True, exist_ok=True)
    destination = CLOUDFLARED_DIR / filename
    if destination.exists() and os.access(destination, os.X_OK):
        return str(destination)
    report("Checking the secure internet component…", 0.02)
    api_request = urllib.request.Request(
        "https://api.github.com/repos/cloudflare/cloudflared/releases/latest",
        headers={"User-Agent": "FriendlyFolderShare/1.0", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(api_request, timeout=30) as response:
        release = json.loads(response.read().decode("utf-8"))
    download_url = next((item["browser_download_url"] for item in release.get("assets", []) if item.get("name") == asset), None)
    if not download_url or not download_url.startswith("https://"):
        raise RuntimeError(f"The official download for {asset} was not found.")
    temporary = destination.with_suffix(destination.suffix + ".download")
    try:
        with urllib.request.urlopen(urllib.request.Request(download_url, headers={"User-Agent": "FriendlyFolderShare/1.0"}), timeout=120) as response, temporary.open("wb") as output:
            total = int(response.headers.get("Content-Length", "0") or 0)
            downloaded = 0
            started = time.monotonic()
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                elapsed = max(time.monotonic() - started, 0.01)
                speed = downloaded / elapsed
                remaining = ((total - downloaded) / speed) if total and speed else None
                if remaining is not None:
                    eta = f"about {int(remaining // 60)}m {int(remaining % 60):02d}s left"
                    fraction = min(downloaded / total, 0.99)
                    report(f"Downloading secure component… {format_size(downloaded)} of {format_size(total)} at {format_size(int(speed))}/s • {eta}", fraction)
                else:
                    report(f"Downloading secure component… {format_size(downloaded)} received", None)
        report("Finishing secure component setup…", 0.99)
        if asset.endswith(".tgz"):
            with tarfile.open(temporary, "r:gz") as archive:
                member = next((item for item in archive.getmembers() if item.name.endswith("/cloudflared") or item.name == "cloudflared"), None)
                if not member:
                    raise RuntimeError("The downloaded package did not contain the expected program.")
                member.name = "cloudflared"
                archive.extract(member, CLOUDFLARED_DIR)
                extracted = CLOUDFLARED_DIR / "cloudflared"
                if extracted != destination:
                    extracted.replace(destination)
        else:
            temporary.replace(destination)
        if os.name != "nt":
            destination.chmod(0o700)
        return str(destination)
    finally:
        temporary.unlink(missing_ok=True)


def make_qr_image(data: str, destination: Path, status_callback=None) -> Path:
    """Create a QR image, installing the small QR dependency if needed."""
    try:
        import qrcode
    except ImportError:
        if status_callback:
            status_callback("Installing QR-code support (one-time setup)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "qrcode[pil]"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180)
        import qrcode
    destination.parent.mkdir(parents=True, exist_ok=True)
    qrcode.make(data).save(destination)
    return destination


def local_ip() -> str:
    """Best-effort LAN IP discovery; falls back safely to localhost."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def safe_child(root: Path, relative: str) -> Path:
    """Resolve a URL path and ensure it remains inside root."""
    relative = unquote(relative).replace("\\", "/").lstrip("/")
    if ".folder_share_uploads" in Path(relative).parts:
        raise PermissionError("That path is reserved for active uploads")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise PermissionError("That path is outside the shared folder")
    return candidate


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return str(size)


class ShareState:
    def __init__(self, folder: Path, password: str):
        self.folder = folder.resolve()
        self.password_hash = hashlib.sha256(password.encode()).hexdigest()
        self.connection_id = secrets.token_urlsafe(18)
        self.sessions: dict[str, dict] = {}
        self.login_failures: dict[str, list[float]] = {}
        self.upload_locks: dict[str, threading.Lock] = {}
        self.lock = threading.Lock()
        self._cleanup_upload_parts()

    def authenticate(self, supplied: str) -> bool:
        return secrets.compare_digest(
            hashlib.sha256(supplied.encode()).hexdigest(), self.password_hash
        )

    def login_allowed(self, address: str) -> bool:
        now = time.time()
        with self.lock:
            attempts = [stamp for stamp in self.login_failures.get(address, []) if now - stamp < LOGIN_WINDOW_SECONDS]
            self.login_failures[address] = attempts
            return len(attempts) < LOGIN_MAX_FAILURES

    def record_login_failure(self, address: str) -> None:
        with self.lock:
            self.login_failures.setdefault(address, []).append(time.time())

    def clear_login_failures(self, address: str) -> None:
        with self.lock:
            self.login_failures.pop(address, None)

    def _cleanup_upload_parts(self) -> None:
        upload_dir = self.folder / ".folder_share_uploads"
        if not upload_dir.is_dir():
            return
        cutoff = time.time() - UPLOAD_PART_MAX_AGE
        for part in upload_dir.glob("*.part"):
            try:
                if part.stat().st_mtime < cutoff:
                    part.unlink(missing_ok=True)
            except OSError:
                pass

    def new_session(self, address: str, user_agent: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.sessions[token] = {
                "expires": now + SESSION_SECONDS,
                "nickname": None,
                "address": address,
                "user_agent": user_agent[:180],
                "connected_at": now,
                "last_seen": now,
            }
        return token

    def valid_session(self, token: str | None) -> bool:
        if not token:
            return False
        with self.lock:
            session = self.sessions.get(token)
            if not session or session["expires"] < time.time():
                self.sessions.pop(token, None)
                return False
            return True

    def session(self, token: str | None) -> dict | None:
        if not self.valid_session(token):
            return None
        with self.lock:
            return dict(self.sessions[token])

    def touch(self, token: str | None) -> None:
        if not token:
            return
        with self.lock:
            session = self.sessions.get(token)
            if session:
                session["last_seen"] = time.time()

    def set_nickname(self, token: str | None, nickname: str) -> tuple[bool, str]:
        nickname = " ".join(nickname.strip().split())
        if not 2 <= len(nickname) <= 32:
            return False, "Choose a nickname between 2 and 32 characters."
        if any(ord(char) < 32 for char in nickname):
            return False, "The nickname contains an invalid character."
        if not token:
            return False, "Your session has expired. Please sign in again."
        with self.lock:
            session = self.sessions.get(token)
            if not session or session["expires"] < time.time():
                self.sessions.pop(token, None)
                return False, "Your session has expired. Please sign in again."
            used = {item["nickname"].casefold() for key, item in self.sessions.items() if key != token and item.get("nickname")}
            if nickname.casefold() in used:
                return False, "That nickname is already in use. Please choose another one."
            session["nickname"] = nickname
            session["last_seen"] = time.time()
        return True, ""

    def devices(self) -> list[tuple[str, dict]]:
        now = time.time()
        result = []
        with self.lock:
            expired = [token for token, item in self.sessions.items() if item["expires"] < now]
            for token in expired:
                self.sessions.pop(token, None)
            for token, item in self.sessions.items():
                if item.get("nickname"):
                    record = dict(item)
                    record["online"] = now - item["last_seen"] < 90
                    result.append((token, record))
        return sorted(result, key=lambda pair: pair[1]["last_seen"], reverse=True)

    def revoke_session(self, token: str) -> bool:
        with self.lock:
            return self.sessions.pop(token, None) is not None

    def forget(self, token: str | None) -> None:
        if token:
            with self.lock:
                self.sessions.pop(token, None)


class ReusableServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class FolderHandler(http.server.BaseHTTPRequestHandler):
    server_version = "FriendlyFolderShare/1.0"

    @property
    def state(self) -> ShareState:
        return self.server.share_state  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):
        # Keep the GUI pleasant; do not print every request to the terminal.
        pass

    def _cookie(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        for item in raw.split(";"):
            key, _, value = item.strip().partition("=")
            if key == "ffs_session":
                return value
        return None

    def _send_html(self, body: str, status=200, headers: dict[str, str] | None = None):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, value, status=200):
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str, extra_headers=None):
        self.send_response(303)
        self.send_header("Location", location)
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()

    def _require_login(self) -> bool:
        token = self._cookie()
        if self.state.valid_session(token):
            self.state.touch(token)
            session = self.state.session(token)
            if session and session.get("nickname") is None and not (self.command == "POST" and urlparse(self.path).path == "/nickname"):
                self._send_html(nickname_page(), 403)
                return False
            return True
        parsed = urlparse(self.path)
        if self.command == "POST" and parsed.path == "/login":
            return False
        self._send_html(login_page(), 401)
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            token = self._cookie()
            if self.state.valid_session(token):
                session = self.state.session(token) or {}
                if session.get("nickname") is None:
                    self._send_html(nickname_page())
                else:
                    self.state.touch(token)
                    self._send_html(file_page(self.state.folder, "", nickname=session.get("nickname", ""), local_url=self._local_url()))
            else:
                self._send_html(login_page(), 401)
            return
        if parsed.path == "/logout":
            token = self._cookie()
            self.state.forget(token)
            self._redirect("/", {"Set-Cookie": "ffs_session=; Max-Age=0; HttpOnly; SameSite=Strict"})
            return
        if not self._require_login():
            return
        if parsed.path == "/heartbeat":
            self._send_json({"ok": True})
        elif parsed.path == "/download":
            self._download(parse_qs(parsed.query).get("path", [""])[0])
        elif parsed.path == "/browse":
            rel = parse_qs(parsed.query).get("path", [""])[0]
            query = parse_qs(parsed.query).get("q", [""])[0]
            session = self.state.session(self._cookie()) or {}
            self._send_html(file_page(self.state.folder, rel, nickname=session.get("nickname", ""), search_query=query, local_url=self._local_url()))
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            address = self.client_address[0]
            if not self.state.login_allowed(address):
                self._send_html(login_page("Too many incorrect attempts. Please wait 15 minutes and try again."), 429, {"Retry-After": str(LOGIN_WINDOW_SECONDS)})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 20_000:
                self.send_error(413)
                return
            raw = self.rfile.read(length).decode("utf-8", "replace")
            password = parse_qs(raw).get("password", [""])[0]
            if self.state.authenticate(password):
                self.state.clear_login_failures(address)
                token = self.state.new_session(address, self.headers.get("User-Agent", ""))
                secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
                self._redirect("/", {"Set-Cookie": f"ffs_session={token}; HttpOnly; SameSite=Strict{secure}; Max-Age={SESSION_SECONDS}"})
            else:
                self.state.record_login_failure(address)
                self._send_html(login_page("Incorrect password. Please try again."), 401)
            return
        if parsed.path == "/nickname":
            token = self._cookie()
            if not self.state.valid_session(token):
                self._send_html(login_page("Your sign-in session expired. Please sign in again."), 401)
                return
            form = self._read_form()
            ok, error = self.state.set_nickname(token, form.get("nickname", [""])[0])
            if ok:
                self._redirect("/")
            else:
                self._send_html(nickname_page(error), 400)
            return
        if not self._require_login():
            return
        if parsed.path == "/cancel_upload":
            upload_id = parse_qs(parsed.query).get("upload_id", [""])[0]
            self._cancel_upload(upload_id)
            return
        try:
            if parsed.path == "/upload":
                self._send_json({"error": "This upload route is disabled. Use the resumable chunked Upload button."}, 410)
                return
            form = self._read_form()
            if parsed.path == "/mkdir":
                parent = form.get("path", [""])[0]
                name = form.get("name", [""])[0].strip()
                if not name or name in {".", ".."} or "/" in name or "\\" in name:
                    raise ValueError("Folder name is invalid")
                safe_child(self.state.folder, parent).joinpath(name).mkdir()
            elif parsed.path == "/delete":
                target = safe_child(self.state.folder, form.get("path", [""])[0])
                if target == self.state.folder:
                    raise ValueError("The shared folder itself cannot be deleted")
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            elif parsed.path == "/rename":
                target = safe_child(self.state.folder, form.get("path", [""])[0])
                name = form.get("name", [""])[0].strip()
                if not name or name in {".", ".."} or "/" in name or "\\" in name:
                    raise ValueError("New name is invalid")
                target.rename(target.parent / name)
            else:
                self.send_error(404)
                return
            self._redirect("/browse?path=" + quote(form.get("path", [""])[0]))
        except (OSError, ValueError, PermissionError) as exc:
            form = locals().get("form", {})
            session = self.state.session(self._cookie()) or {}
            self._send_html(file_page(self.state.folder, form.get("path", [""])[0], f"Could not complete that action: {html.escape(str(exc))}", nickname=session.get("nickname", ""), local_url=self._local_url()), 400)

    def _local_url(self) -> str:
        return f"http://{local_ip()}:{self.server.server_address[1]}/"  # type: ignore[attr-defined]

    def do_PUT(self):
        if not self._require_login():
            return
        if urlparse(self.path).path != "/upload_chunk":
            self.send_error(404)
            return
        try:
            self._upload_chunk(parse_qs(urlparse(self.path).query))
        except (OSError, ValueError, PermissionError) as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception:
            self._send_json({"error": "The server could not process this upload chunk."}, 500)

    def _upload_chunk(self, query):
        upload_id = query.get("upload_id", [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", upload_id):
            raise ValueError("Invalid upload ID")
        with self.state.lock:
            lock = self.state.upload_locks.setdefault(upload_id, threading.Lock())
        with lock:
            return self._upload_chunk_locked(query)

    def _upload_chunk_locked(self, query):
        upload_id = query.get("upload_id", [""])[0]
        relative = query.get("path", [""])[0]
        filename = Path(query.get("name", [""])[0]).name
        try:
            offset = int(query.get("offset", ["0"])[0])
            total = int(query.get("total", ["0"])[0])
        except ValueError:
            raise ValueError("Invalid upload progress")
        if not filename or filename in {".", ".."} or total <= 0 or total > MAX_UPLOAD_BYTES:
            raise ValueError("Invalid file or file size")
        if offset < 0 or offset > total:
            raise ValueError("Invalid upload offset")
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or offset + length > total:
            raise ValueError("Invalid upload chunk")
        temp_dir = self.state.folder / ".folder_share_uploads"
        temp_dir.mkdir(exist_ok=True)
        temp = temp_dir / (upload_id + ".part")
        current = temp.stat().st_size if temp.exists() else 0
        if current != offset:
            self._send_json({"error": "Upload position updated on the server", "offset": current, "resync": True}, 409)
            return
        with temp.open("ab") as output:
            remaining = length
            while remaining:
                data = self.rfile.read(min(1024 * 1024, remaining))
                if not data:
                    raise ValueError("Upload connection ended early")
                output.write(data)
                remaining -= len(data)
        new_offset = offset + length
        if new_offset == total:
            destination = safe_child(self.state.folder, relative) / filename
            if destination.exists():
                temp.unlink(missing_ok=True)
                raise ValueError("A file with that name already exists")
            temp.replace(destination)
            self._send_json({"offset": new_offset, "complete": True})
        else:
            self._send_json({"offset": new_offset, "complete": False})

    def _cancel_upload(self, upload_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", upload_id):
            self._send_json({"error": "Invalid upload ID"}, 400)
            return
        temp = self.state.folder / ".folder_share_uploads" / (upload_id + ".part")
        temp.unlink(missing_ok=True)
        self._send_json({"cancelled": True})

    def _read_form(self) -> dict[str, list[str]]:
        """Read normal HTML form fields from the POST body, not the URL."""
        length = int(self.headers.get("Content-Length", "0"))
        if length > 100_000:
            raise ValueError("Form is too large")
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return parse_qs(raw)

    def _download(self, relative: str):
        try:
            target = safe_child(self.state.folder, relative)
            if not target.is_file():
                raise FileNotFoundError
            size = target.stat().st_size
            start, end = 0, size - 1
            range_header = self.headers.get("Range", "")
            if range_header.startswith("bytes="):
                match = re.fullmatch(r"(\d*)-(\d*)", range_header[6:].strip())
                if not match:
                    self.send_error(416, "Invalid byte range")
                    return
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else size - 1
                elif match.group(2):
                    length = int(match.group(2)); start = max(0, size - length)
                if start >= size or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                end = min(end, size - 1)
            partial = start != 0 or end != size - 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            safe_name = "".join(char for char in target.name if ord(char) >= 32 and char not in {'"', "\\"}) or "download"
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
            self.end_headers()
            with target.open("rb") as file:
                file.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = file.read(min(1024 * 1024, remaining))
                    if not block: break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (OSError, PermissionError):
            self.send_error(404, "File not found")

    def _upload(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD_BYTES + 1_000_000:
            raise ValueError("Upload is empty or larger than the 512 MB limit")
        content_type = self.headers.get("Content-Type", "")
        boundary_marker = "boundary="
        if boundary_marker not in content_type:
            raise ValueError("Unsupported upload format")
        boundary = content_type.split(boundary_marker, 1)[1].encode()
        body = self.rfile.read(length)
        # Simple multipart parser sufficient for a single browser file input.
        fields: dict[str, bytes] = {}
        file_name = ""
        file_data = b""
        for part in body.split(b"--" + boundary):
            header_end = part.find(b"\r\n\r\n")
            if header_end < 0:
                continue
            headers = part[:header_end].decode("utf-8", "replace")
            data = part[header_end + 4:]
            if data.endswith(b"\r\n"):
                data = data[:-2]
            disposition = next((line for line in headers.split("\r\n") if line.lower().startswith("content-disposition:")), "")
            name_match = re.search(r'name="([^"]+)"', disposition)
            if not name_match:
                continue
            field_name = name_match.group(1)
            filename_match = re.search(r'filename="([^"]*)"', disposition)
            if filename_match:
                file_name = Path(filename_match.group(1)).name
                file_data = data
            else:
                fields[field_name] = data
        if not file_name:
            raise ValueError("No file was selected")
        relative = fields.get("path", b"").decode("utf-8", "replace")
        if len(file_data) > MAX_UPLOAD_BYTES:
            raise ValueError(f"Upload exceeds the {format_size(MAX_UPLOAD_BYTES)} limit")
        destination = safe_child(self.state.folder, relative) / file_name
        if destination.exists():
            raise ValueError("A file with that name already exists")
        destination.write_bytes(file_data)
        self._redirect("/browse?path=" + quote(relative))


def login_page(message="") -> str:
    return f"""<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Sign in · Folder Share</title><style>{CSS}</style>
<main class=card><h1>Folder Share</h1><p class=muted>Version {APP_VERSION}</p><p>Enter the password provided by the folder owner.</p>
{('<div class=error>' + message + '</div>') if message else ''}
<form method=post action=/login><label>Password<input type=password name=password autofocus required></label><button>Sign in</button></form></main>"""


def nickname_page(message="") -> str:
    return f"""<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Choose a nickname · Folder Share</title><style>{CSS}</style>
<main class=card><h1>Almost ready</h1><p>Choose a nickname so the owner can tell connected devices apart.</p>
{('<div class=error>' + html.escape(message) + '</div>') if message else ''}
<form method=post action=/nickname><label>Nickname<input name=nickname maxlength=32 minlength=2 autocomplete=nickname autofocus required placeholder='e.g. Wanjohi’s phone'></label><button>Continue</button></form>
<p class=muted>Your nickname is only shown to the owner of this Folder Share session.</p></main>"""


def search_files(root: Path, query: str, limit: int = 200) -> list[tuple[Path, str]]:
    query = " ".join(query.strip().split()).casefold()
    if not query:
        return []
    found = []
    try:
        for item in root.rglob("*"):
            if len(found) >= limit:
                break
            if ".folder_share_uploads" in item.parts or not item.is_file() and not item.is_dir():
                continue
            try:
                relative = str(item.relative_to(root)).replace(os.sep, "/")
            except ValueError:
                continue
            if query in item.name.casefold() or query in relative.casefold():
                found.append((item, relative))
    except OSError:
        pass
    return sorted(found, key=lambda pair: (not pair[0].is_dir(), pair[1].casefold()))


def file_page(root: Path, relative: str, message="", nickname="", search_query="", local_url="") -> str:
    try:
        current = safe_child(root, relative)
        if not current.is_dir():
            current = current.parent
            relative = str(current.relative_to(root)).replace(os.sep, "/")
    except (PermissionError, OSError):
        current, relative = root, ""
    entries = search_files(root, search_query) if search_query else sorted((item for item in current.iterdir() if item.name != ".folder_share_uploads"), key=lambda p: (not p.is_dir(), p.name.lower()))
    rows = []
    for entry in entries:
        item, found_rel = entry if search_query else (entry, "")
        rel = found_rel if search_query else str(item.relative_to(root)).replace(os.sep, "/")
        name = html.escape(item.name)
        if item.is_dir():
            rows.append(f"<tr><td>📁 <a href='/browse?path={quote(rel)}'>{name}</a></td><td>Folder</td><td><form class=inline method=post action=/rename><input type=hidden name=path value='{html.escape(rel)}'><input name=name placeholder='New name' required><button>Rename</button></form> <form class=inline method=post action=/delete onsubmit=\"return confirm('Delete this folder and everything in it?')\"><input type=hidden name=path value='{html.escape(rel)}'><button class=danger>Delete</button></form></td></tr>")
        else:
            rows.append(f"<tr><td>📄 {name}</td><td>{format_size(item.stat().st_size)}</td><td><button class=button onclick=\"startDownload('/download?path={quote(rel)}','{html.escape(item.name, quote=True)}',{item.stat().st_size})\">Download</button> <form class=inline method=post action=/rename><input type=hidden name=path value='{html.escape(rel)}'><input name=name placeholder='New name' required><button>Rename</button></form> <form class=inline method=post action=/delete onsubmit=\"return confirm('Delete this file?')\"><input type=hidden name=path value='{html.escape(rel)}'><button class=danger>Delete</button></form></td></tr>")
    parent = ""
    if relative:
        parent_rel = str(Path(relative).parent).replace(os.sep, "/")
        if parent_rel == ".": parent_rel = ""
        parent = f"<a href='/browse?path={quote(parent_rel)}'>← Parent folder</a>"
    crumb = html.escape(relative or "Shared folder")
    local_note = f"<div id=connection-note class=connection-note data-local-url='{html.escape(local_url)}'><strong>Checking connection…</strong><span id=connection-detail>Choosing the fastest safe route for this device.</span><a id=local-link href='{html.escape(local_url)}'>Use the direct same-Wi‑Fi address</a></div>" if local_url else ""
    page_script = UPLOAD_JS.replace("__LOCAL_BASE__", json.dumps(local_url))
    search_box = f"<form class=search-form method=get action=/browse><input type=hidden name=path value='{html.escape(relative)}'><input name=q value='{html.escape(search_query)}' placeholder='Search files and folders' aria-label='Search files and folders'><button>Search</button>{('<a href=/browse?path=' + quote(relative) + '>Clear</a>') if search_query else ''}</form>"
    identity = f"<span class=muted>Signed in as <strong>{html.escape(nickname)}</strong></span>" if nickname else ""
    return f"""<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Folder Share · {crumb}</title><style>{CSS}</style><main>
<header><div><h1>Folder Share</h1>{identity}</div><a href=/logout>Sign out</a></header>
{('<div class=notice>' + message + '</div>') if message else ''}{local_note}<p>{parent}</p><h2>📂 {crumb}</h2>

{search_box}
	<section class=actions><div class=upload-box><label for=upload-file>Choose one or more files</label><input id=upload-file type=file multiple onchange="selectFile(this, '{html.escape(relative)}')"><button id=start-upload onclick="startUpload()">Upload selected files</button><div id=upload-status class=muted>Choose files, then press Upload selected files.</div><progress id=upload-progress max=100 value=0 hidden></progress><div id=upload-buttons class=upload-buttons hidden><button id=pause-upload onclick="pauseUpload()">Pause</button><button id=resume-upload onclick="resumeUpload()" disabled>Resume</button><button id=cancel-upload class=danger onclick="cancelUpload()">Cancel</button></div></div><form method=post action=/mkdir><input type=hidden name=path value='{html.escape(relative)}'><input name=name placeholder='New folder name' required><button>New folder</button></form></section><details id=transfer-manager class=transfer-manager><summary id=transfer-summary>Transfers: idle</summary><div id=transfer-list class=muted>Nothing is transferring.</div></details><p class=muted>Edits here happen immediately in the selected folder on the owner’s computer.</p>
<table><tr><th>Name</th><th>Size</th><th>Actions</th></tr>{''.join(rows) or '<tr><td colspan=3 class=muted>This folder is empty.</td></tr>'}</table><footer>Folder Share v{APP_VERSION} · Browser and AI tools can use this page with the URL and password.</footer></main>{page_script}"""


UPLOAD_JS = """<script>
let upload = null;
let uploadQueue = [];
let transferJobs = [];
let downloadJobs = [];
// Smaller chunks make progress and pause/resume responsive on slow mobile links.
const CHUNK = 512 * 1024;
const LOCAL_BASE = __LOCAL_BASE__;
let transferBase = location.origin;
// When this page was opened through the LAN address, all transfers are local.
// A page opened through HTTPS cannot silently use HTTP LAN content in many browsers;
// in that case the public HTTPS URL remains the secure fallback.
if (LOCAL_BASE && location.protocol === 'http:' && location.hostname !== '127.0.0.1' && location.hostname !== 'localhost') transferBase = location.origin;
function updateConnectionNotice() {
  const note = document.getElementById('connection-note'); if (!note || !LOCAL_BASE) return;
  const detail = document.getElementById('connection-detail'), link = document.getElementById('local-link');
  try {
    const local = new URL(LOCAL_BASE), onLocal = location.hostname === local.hostname && location.port === local.port;
    if (onLocal) {
      note.className = 'connection-note local';
      note.querySelector('strong').textContent = '✓ Connected directly over local Wi‑Fi';
      detail.textContent = 'Transfers stay on this network for the best speed.';
      link.hidden = true;
    } else {
      note.className = 'connection-note internet';
      note.querySelector('strong').textContent = '✓ Connected through the secure internet link';
      detail.textContent = 'If this device is on the same Wi‑Fi, the direct link may be faster.';
      link.hidden = false;
    }
  } catch (_) { note.querySelector('strong').textContent = 'Connection ready'; }
}
updateConnectionNotice();
function selectFile(input, path) {
  if (!input.files.length) return;
  uploadQueue = Array.from(input.files).map(file => ({file, name:file.name, size:file.size, path, id: null, offset: 0, paused: false, cancelled: false, xhr: null, started: 0, lastUiUpdate: 0, state: 'queued'}));
  transferJobs = uploadQueue;
  upload = null;
  document.getElementById('start-upload').disabled = false;
  document.getElementById('upload-status').textContent = `${uploadQueue.length} file${uploadQueue.length === 1 ? '' : 's'} ready to upload`;
  renderTransfers();
}
function startUpload() {
  if (!uploadQueue.length) { document.getElementById('upload-status').textContent = 'Choose one or more files first, then press Upload selected files.'; document.getElementById('upload-file').focus(); return; }
  beginNextUpload();
}
function beginNextUpload() {
  upload = uploadQueue.find(item => item.state === 'queued' || item.state === 'paused');
  if (!upload) { document.getElementById('upload-status').textContent = 'All selected uploads completed.'; document.getElementById('upload-buttons').hidden = true; renderTransfers(); return; }
  upload.state = 'uploading';
  upload.id = (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now());
  upload.offset = 0; upload.paused = false; upload.cancelled = false; upload.started = Date.now();
  document.getElementById('start-upload').disabled = true;
  document.getElementById('upload-file').disabled = true;
  document.getElementById('upload-progress').hidden = false;
  document.getElementById('upload-progress').value = 0;
  document.getElementById('upload-buttons').hidden = false;
  document.getElementById('resume-upload').disabled = true;
  document.getElementById('upload-status').textContent = `0% · 0 B of ${formatBytes(upload.file.size)} · measuring speed…`;
  renderTransfers();
  uploadNext();
}
async function uploadNext() {
  if (!upload || upload.paused || upload.cancelled) return;
  const u = upload, end = Math.min(u.offset + CHUNK, u.file.size), chunk = u.file.slice(u.offset, end);
  if (!u.offset) document.getElementById('upload-status').textContent = `0% · 0 B of ${formatBytes(u.file.size)} · measuring speed…`;
  const qs = new URLSearchParams({path:u.path, name:u.file.name, upload_id:u.id, offset:String(u.offset), total:String(u.file.size)});
  try {
    const result = await sendChunk(u, qs, chunk);
    if (result.resync) { u.offset = result.offset; if (!u.paused) uploadNext(); else document.getElementById('upload-status').textContent = `Paused at ${Math.round(u.offset/u.file.size*100)}%`; return; }
    u.offset = result.offset;
    updateUploadStatus(u, u.offset, true);
    if (result.complete) { upload.state = 'completed'; document.getElementById('upload-status').textContent = `${upload.file.name} complete`; renderTransfers(); beginNextUpload(); }
    else { renderTransfers(); uploadNext(); }
  } catch (error) {
    if (error.name === 'AbortError') return;
    document.getElementById('upload-status').textContent = 'Upload error: ' + error.message;
    if (upload) { upload.state = 'paused'; renderTransfers(); }
    document.getElementById('pause-upload').disabled = true;
    document.getElementById('resume-upload').disabled = false;
  }
}
function sendChunk(u, qs, chunk) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); u.xhr = xhr;
    xhr.open('PUT', transferBase + '/upload_chunk?' + qs.toString(), true);
    xhr.upload.onprogress = event => {
      if (!event.lengthComputable) return;
      const transferred = u.offset + event.loaded;
      const now = Date.now();
      if (now - u.lastUiUpdate >= 300 || transferred >= u.file.size) { u.lastUiUpdate = now; updateUploadStatus(u, transferred, false); }
    };
    xhr.onload = () => {
      let result; try { result = JSON.parse(xhr.responseText); } catch (_) { reject(new Error(`The server returned an unexpected response (${xhr.status}). Try again or restart sharing.`)); return; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(result); else if (result.resync) resolve(result); else reject(new Error(result.error || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error('The connection was interrupted. You can press Resume to try again.'));
    xhr.onabort = () => reject(new DOMException('Upload paused or cancelled', 'AbortError'));
    xhr.send(chunk);
  });
}
function updateUploadStatus(u, transferred, finalUpdate) {
  const seconds = Math.max((Date.now()-u.started)/1000, .1), speed = transferred/seconds, remaining = speed ? Math.max(0, Math.round((u.file.size-transferred)/speed)) : 0, percent = Math.min(100, Math.round(transferred/u.file.size*100));
  document.getElementById('upload-progress').value = percent;
  if (transferred <= 0) document.getElementById('upload-status').textContent = `0% · 0 B of ${formatBytes(u.file.size)} · measuring speed…`;
  else document.getElementById('upload-status').textContent = `${percent}% · ${formatBytes(transferred)} of ${formatBytes(u.file.size)} · ${formatBytes(speed)}/s · about ${remaining}s left`;
  u.progress = percent; u.speed = speed; u.remaining = remaining; renderTransfers();
}
function renderTransfers() {
  const summary = document.getElementById('transfer-summary'), list = document.getElementById('transfer-list'); if (!summary || !list) return;
  const total = transferJobs.length, done = transferJobs.filter(j => j.state === 'completed').length, active = transferJobs.find(j => j.state === 'uploading');
  const combined = transferJobs.reduce((sum, j) => sum + (j.progress ? j.size*j.progress/100 : j.state === 'completed' ? j.size : 0), 0), bytes = transferJobs.reduce((sum,j)=>sum+j.size,0);
  summary.textContent = total ? `Transfers: ${done}/${total} complete · ${formatBytes(combined)} of ${formatBytes(bytes)}` : 'Transfers: idle';
  list.innerHTML = transferJobs.map(j => `<div class=transfer-row><strong>${escapeHtml(j.name)}</strong><span>${j.state}${j.progress != null ? ` · ${j.progress}%` : ''}${j.speed ? ` · ${formatBytes(j.speed)}/s` : ''}${j.kind === 'download' && j.state === 'downloading' ? ` <button onclick="pauseDownload('${j.id}')">Pause</button><button class=danger onclick="cancelDownload('${j.id}')">Cancel</button>` : ''}${j.kind === 'download' && j.state === 'paused' ? ` <button onclick="resumeDownload('${j.id}')">Resume</button><button class=danger onclick="cancelDownload('${j.id}')">Cancel</button>` : ''}</span></div>`).join('') || 'Nothing is transferring.';
}
function escapeHtml(value) { return String(value).replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c])); }
function startDownload(url, name, size) {
  const job = {id:'d'+Date.now()+Math.random().toString(36).slice(2), kind:'download', url, name, size, offset:0, chunks:[], state:'downloading', progress:0, speed:0, started:Date.now(), controller:null};
  downloadJobs.push(job); transferJobs.push(job); renderTransfers(); downloadNext(job);
}
async function downloadNext(job) {
  if (job.state !== 'downloading') return;
  try {
    const end = Math.min(job.offset + 1024*1024, job.size) - 1;
    job.controller = new AbortController();
    const response = await fetch(transferBase + job.url, {headers:{Range:`bytes=${job.offset}-${end}`}, credentials:'include', cache:'no-store', signal:job.controller.signal});
    if (!response.ok && response.status !== 206) throw new Error(`Download failed (${response.status})`);
    const data = new Uint8Array(await response.arrayBuffer());
    if (!data.length) throw new Error('The connection returned no data.');
    job.chunks.push(data); job.offset += data.length; job.progress = Math.min(100, Math.round(job.offset/job.size*100));
    const seconds=Math.max((Date.now()-job.started)/1000,.1); job.speed=job.offset/seconds; renderTransfers();
    if (job.offset < job.size) downloadNext(job);
    else { job.state='completed'; const blob=new Blob(job.chunks); const link=document.createElement('a'); link.href=URL.createObjectURL(blob); link.download=job.name; link.click(); setTimeout(()=>URL.revokeObjectURL(link.href),60000); renderTransfers(); }
  } catch (error) { if (job.state !== 'cancelled') { job.state='paused'; job.error=error.message; renderTransfers(); } }
}
function pauseDownload(id) { const job=downloadJobs.find(item=>item.id===id); if (!job || job.state !== 'downloading') return; job.state='paused'; if (job.controller) job.controller.abort(); renderTransfers(); }
function resumeDownload(id) { const job=downloadJobs.find(item=>item.id===id); if (!job) return; job.state='downloading'; job.started=Date.now()-(job.offset/Math.max(job.speed,1))*1000; renderTransfers(); downloadNext(job); }
function cancelDownload(id) { const job=downloadJobs.find(item=>item.id===id); if (!job) return; job.state='cancelled'; job.chunks=[]; renderTransfers(); }
function pauseUpload() { if (!upload) return; upload.paused = true; upload.state = 'paused'; if (upload.xhr) upload.xhr.abort(); document.getElementById('upload-status').textContent = `Paused at ${Math.round(upload.offset/upload.file.size*100)}%`; document.getElementById('pause-upload').disabled = true; document.getElementById('resume-upload').disabled = false; renderTransfers(); }
function resumeUpload() { if (!upload) return; upload.paused = false; upload.state = 'uploading'; upload.started = Date.now() - (upload.offset / Math.max(upload.offset,1))*1000; document.getElementById('pause-upload').disabled = false; document.getElementById('resume-upload').disabled = true; renderTransfers(); uploadNext(); }
async function cancelUpload() { if (!upload) return; upload.cancelled = true; upload.state = 'cancelled'; if (upload.xhr) upload.xhr.abort(); await fetch(transferBase + '/cancel_upload?upload_id=' + encodeURIComponent(upload.id), {method:'POST'}); document.getElementById('upload-status').textContent = 'Upload cancelled. Choose another file or press Upload again.'; document.getElementById('upload-progress').value = 0; document.getElementById('upload-buttons').hidden = true; document.getElementById('upload-file').disabled = false; document.getElementById('start-upload').disabled = false; upload = null; renderTransfers(); }
function formatBytes(n) { if (!n) return '0 B'; const units=['B','KB','MB','GB']; let i=0; while(n>=1024 && i<units.length-1){n/=1024;i++;} return n.toFixed(i?1:0)+' '+units[i]; }
setInterval(() => fetch('/heartbeat', {credentials:'same-origin', cache:'no-store'}).catch(() => {}), 30000);
</script>"""

CSS = """body{font:16px system-ui,-apple-system,Segoe UI,sans-serif;background:#f4f7fb;color:#172033;margin:0;padding:24px}main{max-width:1000px;margin:auto}.card{max-width:420px;margin:8vh auto;background:white;padding:32px;border-radius:16px;box-shadow:0 8px 30px #18345d18}header{display:flex;justify-content:space-between;align-items:start;gap:20px}h1{margin:0 0 6px;color:#183b69}h2{margin-top:24px}label{display:block;font-weight:600;margin:20px 0}input{font:inherit;padding:10px;border:1px solid #c8d2e0;border-radius:8px;box-sizing:border-box}label input{display:block;width:100%;margin-top:8px}button,.button{display:inline-block;background:#1769d1;color:#fff;border:0;border-radius:8px;padding:10px 14px;text-decoration:none;cursor:pointer;font:inherit}button:hover,.button:hover{background:#0d55ad}button:disabled{opacity:.5;cursor:not-allowed}.danger{background:#b42318;padding:7px 10px;font-size:14px}.inline{display:inline}.actions{display:flex;gap:12px;flex-wrap:wrap;background:#eaf2fc;padding:16px;border-radius:12px}.actions form{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.upload-box{flex:1 1 360px}.upload-box label{margin:0 0 8px}.upload-buttons{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}[hidden]{display:none!important}progress{display:block;width:100%;height:16px;margin-top:10px}.transfer-manager{background:white;border-radius:10px;margin-top:14px;padding:10px 14px;box-shadow:0 2px 8px #18345d12}.transfer-manager summary{cursor:pointer;font-weight:700;color:#183b69}.transfer-row{display:flex;justify-content:space-between;gap:12px;align-items:center;border-top:1px solid #e6ebf2;padding:9px 0}.transfer-row span{color:#65758b;font-size:14px;text-align:right}table{width:100%;border-collapse:collapse;background:white;margin-top:18px;border-radius:12px;overflow:hidden}th,td{text-align:left;padding:13px;border-bottom:1px solid #e6ebf2}th{background:#edf3fa}.muted{color:#65758b;font-size:14px}footer{color:#65758b;font-size:12px;margin-top:20px}.notice,.connection-note{background:#e7f6ec;color:#176b38;padding:12px;border-radius:8px}.connection-note{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0}.connection-note span{color:#365b47}.connection-note a{margin-left:auto;color:#135db5;font-weight:600}.connection-note.internet{background:#eaf2fc;color:#164c85}.connection-note.internet span{color:#365879}.error{background:#fff0f0;color:#a11;padding:10px}@media(max-width:650px){body{padding:12px}header{display:block}header>a{display:inline-block;margin-top:8px}.actions{display:block;padding:12px}.actions form{margin-top:14px}.actions form:first-child{margin-top:0}.connection-note{align-items:flex-start;flex-direction:column}.connection-note a{margin-left:0}.transfer-row{display:block}.transfer-row span{display:block;text-align:left;margin-top:4px}table,thead,tbody,th,td,tr{display:block}table{background:transparent}tr{background:#fff;border-radius:10px;margin:10px 0;padding:10px;box-shadow:0 2px 8px #18345d12}th{display:none}td{border:0;padding:6px 4px}td:last-child{display:flex;flex-wrap:wrap;gap:6px;align-items:center}.inline{display:flex;gap:6px;flex-wrap:wrap}.inline input{min-width:120px;width:42%}.card{padding:22px;margin:3vh auto}}"""


class App:
    def __init__(self, window: tk.Tk):
        self.window = window
        self.window.title(APP_TITLE)
        self.window.geometry("680x560")
        self.window.minsize(600, 500)
        self.server: ReusableServer | None = None
        self.thread: threading.Thread | None = None
        self.tunnel_process: subprocess.Popen | None = None
        self.share_state: ShareState | None = None
        self.device_window = None
        self.device_tree = None
        self.folder: Path | None = None
        self.password = ""
        self.password_var = tk.StringVar()
        self.public_var = tk.BooleanVar(value=True)
        self.url_var = tk.StringVar(value="Server is stopped")
        self.local_url_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Choose a folder to begin.")
        self.public_url = ""
        self.setup_frame = None
        self.command_notice_shown = False
        try:
            self.command_path = install_sharer_command()
        except OSError:
            self.command_path = str(Path(__file__).resolve())
        self.previous_version = record_and_get_previous_version()
        if not SETUP_MARKER.exists() or not (CLOUDFLARED_DIR / ("cloudflared.exe" if os.name == "nt" else "cloudflared")).exists():
            self._build_setup()
        else:
            self._build_main()
        self.window.protocol("WM_DELETE_WINDOW", self.close)

    def _build_setup(self):
        self.window.title("Welcome · " + APP_TITLE)
        self.window.geometry("680x520")
        self.window.minsize(500, 420)
        self.setup_frame = ttk.Frame(self.window, padding=32)
        self.setup_frame.pack(fill="both", expand=True)
        ttk.Label(self.setup_frame, text="Welcome to Folder Share", font=("TkDefaultFont", 23, "bold")).pack(anchor="w", pady=(12, 5))
        ttk.Label(self.setup_frame, text="Let’s do a one-time setup before you share anything.", wraplength=600).pack(anchor="w")
        info = ttk.LabelFrame(self.setup_frame, text="What will happen", padding=16)
        info.pack(fill="x", pady=(28, 18))
        ttk.Label(info, text="1. Download the secure internet connection component\n2. Check that it is ready\n3. Open the simple sharing screen\n\nNothing is shared during setup, and your files are not uploaded anywhere.", justify="left", wraplength=560).pack(anchor="w")
        self.setup_status = tk.StringVar(value="Ready to begin")
        self.setup_percent = tk.StringVar(value="0%")
        ttk.Label(self.setup_frame, textvariable=self.setup_status, wraplength=600).pack(anchor="w", pady=(10, 6))
        progress_row = ttk.Frame(self.setup_frame)
        progress_row.pack(fill="x")
        self.setup_progress = ttk.Progressbar(progress_row, mode="determinate", maximum=100)
        self.setup_progress.pack(side="left", fill="x", expand=True)
        ttk.Label(progress_row, textvariable=self.setup_percent, width=6, anchor="e").pack(side="left", padx=(10, 0))
        self.setup_button = ttk.Button(self.setup_frame, text="Begin one-time setup", command=self.begin_setup)
        self.setup_button.pack(anchor="w", pady=(28, 0))
        ttk.Label(self.setup_frame, text="You will see what is happening, the download speed, and an estimate of the time remaining.", foreground="#65758b", wraplength=600).pack(anchor="w", pady=(18, 0))
        if should_show_command_notice(self.command_path):
            self.window.after(350, self._show_command_notice)

    def begin_setup(self):
        self.setup_button.config(state="disabled")
        self.setup_status.set("Starting setup…")
        threading.Thread(target=self._setup_worker, daemon=True).start()

    def _setup_worker(self):
        try:
            ensure_cloudflared(lambda text, fraction: self.window.after(0, self._setup_progress_update, text, fraction))
            CLOUDFLARED_DIR.mkdir(parents=True, exist_ok=True)
            SETUP_MARKER.write_text("Folder Share setup complete\n", encoding="utf-8")
            self.window.after(0, self._setup_finished)
        except (OSError, urllib.error.URLError, RuntimeError, json.JSONDecodeError, tarfile.TarError) as exc:
            self.window.after(0, lambda: self._setup_error(str(exc)))

    def _setup_progress_update(self, text: str, fraction: float | None):
        self.setup_status.set(text)
        if fraction is not None:
            percent = max(0, min(100, int(fraction * 100)))
            self.setup_progress["value"] = percent
            self.setup_percent.set(f"{percent}%")

    def _setup_finished(self):
        self.setup_progress["value"] = 100
        self.setup_percent.set("100%")
        self.setup_status.set("Setup complete. Opening Folder Share…")
        self.window.after(600, self._show_main)

    def _setup_error(self, details: str):
        self.setup_button.config(state="normal")
        self.setup_status.set("Setup could not finish. Nothing is being shared.")
        messagebox.showerror("Setup needs attention", "I could not finish the one-time setup.\n\nCheck your internet connection and press Begin one-time setup to try again.\n\nDetails: " + details)

    def _show_main(self):
        if self.setup_frame:
            self.setup_frame.destroy()
            self.setup_frame = None
        self._build_main()

    def _build_main(self):
        outer = ttk.Frame(self.window, padding=28)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=APP_TITLE, font=("TkDefaultFont", 21, "bold")).pack(anchor="w")
        ttk.Label(outer, text=f"Share one folder safely over the internet · version {APP_VERSION}", wraplength=600).pack(anchor="w", pady=(4, 18))
        self.choose_button = ttk.Button(outer, text="Choose folder…", command=self.choose_folder)
        self.choose_button.pack(anchor="w")
        self.folder_label = ttk.Label(outer, text="No folder selected", wraplength=540)
        self.folder_label.pack(anchor="w", pady=(8, 16))
        password_box = ttk.LabelFrame(outer, text="Choose a password", padding=12)
        password_box.pack(fill="x", pady=(0, 12))
        ttk.Label(password_box, text=f"Use at least {MIN_PASSWORD_LENGTH} characters. Do not reuse an important password.").pack(anchor="w")
        ttk.Entry(password_box, textvariable=self.password_var, show="•", width=42).pack(anchor="w", pady=(7, 0))
        ttk.Checkbutton(password_box, text="Create an encrypted HTTPS internet link (recommended)", variable=self.public_var).pack(anchor="w", pady=(9, 0))
        self.start_button = ttk.Button(outer, text="Start secure sharing", command=self.start, state="disabled")
        self.start_button.pack(anchor="w")
        box = ttk.LabelFrame(outer, text="Share details", padding=14)
        box.pack(fill="x", pady=(24, 12))
        ttk.Label(box, textvariable=self.url_var, font=("TkDefaultFont", 12, "bold"), wraplength=580).pack(anchor="w")
        ttk.Label(box, textvariable=self.local_url_var, wraplength=580).pack(anchor="w", pady=(5, 0))
        self.password_label = ttk.Label(box, text="Your password will not be displayed here.", wraplength=580)
        self.password_label.pack(anchor="w", pady=(8, 0))
        self.qr_button = ttk.Button(box, text="Show QR code for a friend", command=self.show_qr, state="disabled")
        self.qr_button.pack(anchor="w", pady=(9, 0))
        self.progress = ttk.Progressbar(outer, mode="indeterminate", length=360)
        self.progress.pack(anchor="w", pady=(12, 0))
        ttk.Label(outer, textvariable=self.status_var, wraplength=580).pack(anchor="w")
        self.stop_button = ttk.Button(outer, text="Stop sharing", command=self.stop, state="disabled")
        self.stop_button.pack(anchor="e", pady=(18, 0))
        self.devices_button = ttk.Button(outer, text="Connected devices (0)", command=self.show_devices, state="disabled")
        self.devices_button.pack(anchor="e", pady=(8, 0))
        if should_show_update_notice(self.previous_version):
            self.window.after(350, self._show_version_notice)
        elif should_show_command_notice(self.command_path):
            self.window.after(350, self._show_command_notice)

    def _show_version_notice(self):
        notice = tk.Toplevel(self.window)
        notice.title("Folder Share updated")
        notice.transient(self.window)
        notice.grab_set()
        frame = ttk.Frame(notice, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Folder Share was updated", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        ttk.Label(frame, text=f"This installation was updated from version {self.previous_version} to {APP_VERSION}.\n\nPeople using an older copy will not receive this notice until they run the updated file. Please share the new file with them.", wraplength=480, justify="left").pack(anchor="w", pady=(12, 16))
        hide = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Don’t show this notification again", variable=hide).pack(anchor="w")
        def close_notice():
            if hide.get():
                NOTICE_DISMISSED_PATH.write_text(APP_VERSION + "\n", encoding="utf-8")
            notice.grab_release()
            notice.destroy()
            if should_show_command_notice(self.command_path):
                self.window.after(200, self._show_command_notice)
        ttk.Button(frame, text="OK", command=close_notice).pack(anchor="e", pady=(18, 0))
        notice.protocol("WM_DELETE_WINDOW", close_notice)

    def _show_command_notice(self):
        if self.command_notice_shown:
            return
        self.command_notice_shown = True
        notice = tk.Toplevel(self.window)
        notice.title("Folder Share command ready")
        notice.transient(self.window)
        notice.grab_set()
        frame = ttk.Frame(notice, padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Quick launch is ready", font=("TkDefaultFont", 15, "bold")).pack(anchor="w")
        ttk.Label(frame, text="You can now open Folder Share without typing the full file path. Open a new terminal, then type:", wraplength=480, justify="left").pack(anchor="w", pady=(12, 8))
        ttk.Label(frame, text=LAUNCH_COMMAND, font=("TkFixedFont", 14, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(frame, text=f"This works in a new Command Prompt or PowerShell window on Windows, and in a new Terminal window on macOS or Linux. Existing terminals do not always reload PATH settings.", wraplength=480, justify="left").pack(anchor="w")
        ttk.Label(frame, text=f"Launcher location:\n{self.command_path}", wraplength=480, justify="left", foreground="#65758b").pack(anchor="w", pady=(8, 0))
        hide = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Don’t show this notification again", variable=hide).pack(anchor="w", pady=(14, 0))
        def close_notice():
            if hide.get():
                token = f"{APP_VERSION}|{Path(__file__).resolve()}"
                COMMAND_NOTICE_DISMISSED_PATH.write_text(token + "\n", encoding="utf-8")
            notice.grab_release()
            notice.destroy()
        actions = ttk.Frame(frame)
        actions.pack(anchor="e", pady=(18, 0))
        ttk.Button(actions, text="Open Folder Share now", command=close_notice).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="OK", command=close_notice).pack(side="left")
        notice.protocol("WM_DELETE_WINDOW", close_notice)

    def choose_folder(self):
        chosen = filedialog.askdirectory(title="Choose the folder to share")
        if chosen:
            self.folder = Path(chosen).resolve()
            self.folder_label.config(text=str(self.folder))
            self.start_button.config(state="normal")
            self.status_var.set("Ready. Only this folder and its contents will be accessible.")

    def show_devices(self):
        if not self.share_state:
            return
        if self.device_window and self.device_window.winfo_exists():
            self.device_window.deiconify()
            self.device_window.lift()
            self.refresh_devices()
            return
        window = self.device_window = tk.Toplevel(self.window)
        window.title("Connected devices · Owner controls")
        window.geometry("720x360")
        window.transient(self.window)
        ttk.Label(window, text="Connected devices", font=("TkDefaultFont", 15, "bold")).pack(anchor="w", padx=16, pady=(14, 3))
        ttk.Label(window, text="This panel is only on the owner’s computer. Disconnect ends a device’s Folder Share session.", wraplength=680).pack(anchor="w", padx=16, pady=(0, 10))
        columns = ("nickname", "status", "connected", "last_seen", "address")
        self.device_tree = ttk.Treeview(window, columns=columns, show="headings", height=9)
        headings = {"nickname":"Nickname", "status":"Status", "connected":"Connected", "last_seen":"Last seen", "address":"Network address"}
        widths = {"nickname":170, "status":90, "connected":150, "last_seen":150, "address":120}
        for column in columns:
            self.device_tree.heading(column, text=headings[column])
            self.device_tree.column(column, width=widths[column], anchor="w")
        self.device_tree.pack(fill="both", expand=True, padx=16)
        actions = ttk.Frame(window)
        actions.pack(fill="x", padx=16, pady=12)
        ttk.Button(actions, text="Disconnect selected", command=self.disconnect_selected).pack(side="left")
        ttk.Button(actions, text="Refresh", command=self.refresh_devices).pack(side="left", padx=(8, 0))
        self.refresh_devices()

    def refresh_devices(self):
        if not self.share_state:
            return
        devices = self.share_state.devices()
        if hasattr(self, "devices_button"):
            self.devices_button.config(text=f"Connected devices ({len(devices)})")
        if self.device_tree and self.device_tree.winfo_exists():
            for item in self.device_tree.get_children():
                self.device_tree.delete(item)
            for token, device in devices:
                connected = time.strftime("%Y-%m-%d %H:%M", time.localtime(device["connected_at"]))
                last_seen = time.strftime("%H:%M:%S", time.localtime(device["last_seen"]))
                status = "Online" if device["online"] else "Away"
                self.device_tree.insert("", "end", iid=token, values=(device["nickname"], status, connected, last_seen, device["address"]))
            self.window.after(2000, self.refresh_devices)

    def disconnect_selected(self):
        if not self.share_state or not self.device_tree:
            return
        selected = self.device_tree.selection()
        if not selected:
            messagebox.showinfo("Choose a device", "Select a connected device first.")
            return
        self.share_state.revoke_session(selected[0])
        self.refresh_devices()

    def start(self):
        if not self.folder:
            return
        password = self.password_var.get()
        if len(password) < MIN_PASSWORD_LENGTH:
            messagebox.showerror("Password too short", f"Please choose a password with at least {MIN_PASSWORD_LENGTH} characters.")
            return
        if "\n" in password or "\r" in password:
            messagebox.showerror("Invalid password", "The password cannot contain line breaks.")
            return
        self.password = password
        self.choose_button.config(state="disabled")
        self.start_button.config(state="disabled")
        self.stop_button.config(state="disabled")
        self.progress.start(12)
        self.status_var.set("Preparing secure sharing… the window will remain usable.")
        if self.public_var.get():
            threading.Thread(target=self._prepare_public_start, daemon=True).start()
        else:
            self._start_server(None)

    def _prepare_public_start(self):
        try:
            path = ensure_cloudflared(lambda text, fraction: self.window.after(0, self.status_var.set, text))
            self.window.after(0, lambda: self._start_server(path))
        except (OSError, urllib.error.URLError, RuntimeError, json.JSONDecodeError, tarfile.TarError) as exc:
            self.window.after(0, lambda: self._setup_failed(str(exc)))

    def _setup_failed(self, details: str):
        self.progress.stop()
        self.choose_button.config(state="normal")
        self.start_button.config(state="normal" if self.folder else "disabled")
        self.status_var.set("Setup did not finish. Nothing is being shared.")
        messagebox.showerror("Automatic setup could not finish", "I could not finish the secure setup.\n\nCheck your internet connection and try again.\n\nDetails: " + details)

    def _start_server(self, cloudflared_path: str | None):
        if not self.folder:
            return
        state = ShareState(self.folder, self.password)
        self.share_state = state
        try:
            self.server = ReusableServer(("0.0.0.0", 0), FolderHandler)
            self.server.share_state = state  # type: ignore[attr-defined]
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
        except OSError as exc:
            messagebox.showerror("Could not start", f"The server could not start.\n\n{exc}")
            self.server = None
            return
        port = self.server.server_address[1]
        lan_url = f"http://{local_ip()}:{port}/"
        self.local_url_var.set("Same Wi‑Fi: " + lan_url)
        if self.public_var.get() and cloudflared_path:
            self.url_var.set("Starting encrypted internet link…")
            self.password_label.config(text="Password is set and will not be displayed here.")
            self.status_var.set("Starting a secure HTTPS tunnel. Keep this window open…")
            self._start_tunnel(port, cloudflared_path)
        else:
            self.url_var.set(lan_url)
            self.password_label.config(text="Password is set and will not be displayed here.")
            self.status_var.set("Local sharing is active. Enable the encrypted HTTPS link for internet access.")
        self.progress.stop()
        self.stop_button.config(state="normal")
        self.devices_button.config(state="normal")
        self.refresh_devices()

    def stop(self):
        if self.tunnel_process:
            try:
                self.tunnel_process.terminate()
                self.tunnel_process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.tunnel_process.kill()
                except OSError:
                    pass
            self.tunnel_process = None
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.share_state = None
        if hasattr(self, "devices_button"):
            self.devices_button.config(state="disabled", text="Connected devices (0)")
        if self.device_window and self.device_window.winfo_exists():
            self.device_window.destroy()
            self.device_window = None
            self.device_tree = None
        self.url_var.set("Server is stopped")
        self.local_url_var.set("")
        self.public_url = ""
        self.password_label.config(text="Password is hidden")
        self.qr_button.config(state="disabled")
        self.progress.stop()
        self.status_var.set("Sharing has stopped. Your folder is no longer available over the network.")
        self.choose_button.config(state="normal")
        self.start_button.config(state="normal" if self.folder else "disabled")
        self.stop_button.config(state="disabled")

    def close(self):
        self.stop()
        self.window.destroy()

    def show_qr(self):
        if not self.public_url:
            messagebox.showinfo("QR code not ready", "Start sharing and wait until the public HTTPS link appears.")
            return
        self.qr_button.config(state="disabled")
        self.status_var.set("Preparing the QR code… the window will remain usable.")
        threading.Thread(target=self._qr_worker, daemon=True).start()

    def _qr_worker(self):
        try:
            image_path = make_qr_image(f"Folder Share\nURL: {self.public_url}\nPassword: {self.password}", CLOUDFLARED_DIR / "folder-share-qr.png", lambda text: self.window.after(0, self.status_var.set, text))
            self.window.after(0, lambda: self._display_qr(image_path))
        except Exception as exc:
            self.window.after(0, lambda: self._qr_failed(str(exc)))

    def _qr_failed(self, details: str):
        self.qr_button.config(state="normal")
        self.status_var.set("The QR code could not be created.")
        messagebox.showerror("Could not create QR code", "The QR setup could not finish.\n\n" + details)

    def _display_qr(self, image_path: Path):
        try:
            image = tk.PhotoImage(file=str(image_path))
        except Exception as exc:
            self._qr_failed(str(exc))
            return
        self.qr_button.config(state="normal")
        self.status_var.set("QR code ready. Only show it to someone you trust.")
        qr_window = tk.Toplevel(self.window)
        qr_window.title("Scan to receive sharing details")
        qr_window.transient(self.window)
        ttk.Label(qr_window, text="Scan this only with someone you trust.\nIt contains the URL and password.").pack(padx=18, pady=(18, 8))
        label = ttk.Label(qr_window, image=image)
        label.image = image
        label.pack(padx=18, pady=8)
        ttk.Label(qr_window, text=self.public_url, wraplength=420).pack(padx=18, pady=(0, 18))

    def _start_tunnel(self, port: int, cloudflared_path: str | None):
        try:
            tunnel_kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True, "bufsize": 1}
            if os.name == "nt":
                tunnel_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self.tunnel_process = subprocess.Popen([cloudflared_path or "cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"], **tunnel_kwargs)
        except OSError as exc:
            self.stop()
            messagebox.showerror("Could not start secure link", str(exc))
            return
        threading.Thread(target=self._read_tunnel_output, daemon=True).start()

    def _read_tunnel_output(self):
        process = self.tunnel_process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            # cloudflared prints a Cloudflare terms URL before the actual
            # tunnel URL. Only accept the generated Quick Tunnel hostname.
            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com(?:/[^\s]*)?", line)
            if match:
                url = match.group(0).rstrip(".,)") + "/"
                self.window.after(0, lambda value=url: self._tunnel_ready(value))
                return
        self.window.after(0, self._tunnel_failed)

    def _tunnel_ready(self, url: str):
        if self.server:
            self.public_url = url
            self.url_var.set(url)
            self.qr_button.config(state="normal")
            self.status_var.set("Encrypted internet sharing is active. Give the URL and your chosen password to someone you trust.")

    def _tunnel_failed(self):
        if self.server:
            self.status_var.set("The secure internet link could not be created. Sharing was stopped.")
            self.stop()
            messagebox.showerror("Tunnel failed", "cloudflared could not create an internet link. Check your internet connection and try again.")


if __name__ == "__main__":
    detach_from_terminal()
    if tk is None:
        print("The graphical interface needs Python Tkinter, but it is not installed.")
        print("Ubuntu/Debian: sudo apt install python3-tk")
        print("Fedora: sudo dnf install python3-tkinter")
        print("Windows and macOS: reinstall Python from python.org and include Tk/Tcl.")
        sys.exit(1)
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.15)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
