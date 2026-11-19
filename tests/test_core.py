import hashlib
import http.client
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sharer", ROOT / "sharer.py")
sharer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sharer)
sharer.PBKDF2_ITERATIONS = 1000  # keep the tests fast


class CoreTests(unittest.TestCase):
    def test_safe_child_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError):
                sharer.safe_child(Path(directory), "../outside")

    def test_version_comparison_is_numeric(self):
        self.assertGreater(sharer.version_tuple("v2.10.0"), sharer.version_tuple("2.9.0"))
        self.assertEqual(sharer.version_tuple("not-a-version"), ())

    def test_validate_name(self):
        for bad in ["", "..", "a/b", "a\\b", ".folder_share_uploads", "a:b", "CON", "bad.", "x" * 300]:
            with self.assertRaises(ValueError, msg=bad):
                sharer.validate_name(bad)
        self.assertEqual(sharer.validate_name("  Report 2026.pdf "), "Report 2026.pdf")

    def test_clean_upload_name_and_unique_path(self):
        self.assertEqual(sharer.clean_upload_name("../../evil:name?.txt"), "evil_name_.txt")
        self.assertEqual(sharer.clean_upload_name("NUL.txt"), "_NUL.txt")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "a.txt").write_text("x")
            self.assertEqual(sharer.unique_path(folder, "a.txt").name, "a (1).txt")

    def test_password_hash_is_salted(self):
        with tempfile.TemporaryDirectory() as directory:
            one = sharer.ShareState(Path(directory), "correct horse battery")
            two = sharer.ShareState(Path(directory), "correct horse battery")
            self.assertTrue(one.authenticate("correct horse battery"))
            self.assertFalse(one.authenticate("wrong"))
            self.assertNotEqual(one.password_hash, two.password_hash)

    def test_rate_limit_and_delay_increase(self):
        with tempfile.TemporaryDirectory() as directory:
            state = sharer.ShareState(Path(directory), "strong-password")
            delays = [state.record_login_failure("a") for _ in range(sharer.LOGIN_MAX_FAILURES)]
            self.assertEqual(delays, sorted(delays))
            self.assertFalse(state.login_allowed("a"))
            self.assertTrue(state.login_allowed("b"))

    def test_client_ip_only_trusts_headers_from_loopback(self):
        self.assertEqual(sharer.client_ip_for("127.0.0.1", {"CF-Connecting-IP": "203.0.113.9"}), "203.0.113.9")
        self.assertEqual(sharer.client_ip_for("192.168.1.5", {"CF-Connecting-IP": "203.0.113.9"}), "192.168.1.5")
        self.assertEqual(sharer.client_ip_for("127.0.0.1", {"CF-Connecting-IP": "garbage"}), "127.0.0.1")

    def test_nickname_validation_and_uniqueness(self):
        with tempfile.TemporaryDirectory() as directory:
            state = sharer.ShareState(Path(directory), "strong-password")
            first = state.new_session("127.0.0.1", "test")
            second = state.new_session("127.0.0.2", "test")
            self.assertTrue(state.set_nickname(first, "Phone")[0])
            self.assertFalse(state.set_nickname(second, "phone")[0])

    def test_search(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs" / "Needle.txt").write_text("x")
            self.assertEqual([r for _, r in sharer.search_files(root, "needle")], ["docs/Needle.txt"])


class ServerTests(unittest.TestCase):
    PASSWORD = "a-long-test-password"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name).resolve()
        cls.share = cls.root / "share"
        cls.share.mkdir()
        cls.state = sharer.ShareState(cls.share, cls.PASSWORD)
        cls.server = sharer.ReusableServer(("127.0.0.1", 0), sharer.FolderHandler)
        cls.server.share_state = cls.state
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls._sleep = sharer.time.sleep
        sharer.time.sleep = lambda _s: None  # do not slow wrong-password tests
        cls.cookie = cls.login()

    @classmethod
    def tearDownClass(cls):
        sharer.time.sleep = cls._sleep
        cls.server.shutdown(); cls.server.server_close(); cls.tmp.cleanup()

    @classmethod
    def request(cls, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse(); data = response.read()
        result = (response.status, dict(response.getheaders()), data); conn.close()
        return result

    @classmethod
    def login(cls):
        status, headers, _ = cls.request("POST", "/login", "password=" + cls.PASSWORD, {"Content-Type": "application/x-www-form-urlencoded"})
        token = headers["Set-Cookie"].split(";")[0]
        cls.request("POST", "/nickname", "nickname=Tester", {"Cookie": token, "Content-Type": "application/x-www-form-urlencoded"})
        return token

    def post(self, route, **fields):
        body = "&".join(f"{k}={quote(v)}" for k, v in fields.items())
        return self.request("POST", route, body, {"Cookie": self.cookie, "Content-Type": "application/x-www-form-urlencoded"})

    def test_hostile_filename_cannot_inject_script(self):
        evil = "');alert(1);x.txt"
        (self.share / evil).write_text("x")
        status, _, data = self.request("GET", "/browse?path=", headers={"Cookie": self.cookie})
        page = data.decode()
        self.assertEqual(status, 200)
        self.assertNotIn("startDownload", page)
        self.assertNotIn("onclick=\"", page.split("<table>")[1])
        self.assertNotIn("');alert(1)", page)
        (self.share / evil).unlink()

    def test_rename_collision_rejected_and_nothing_overwritten(self):
        (self.share / "one.txt").write_text("1"); (self.share / "two.txt").write_text("2")
        status, _, data = self.post("/rename", path="one.txt", name="two.txt")
        self.assertEqual(status, 400)
        self.assertEqual((self.share / "two.txt").read_text(), "2")
        self.assertNotIn(str(self.share).encode(), data)  # no disk paths leaked
        for bad in ["a:b", ".folder_share_uploads"]:
            self.assertEqual(self.post("/rename", path="one.txt", name=bad)[0], 400)
        self.assertEqual(self.post("/mkdir", path="", name="two.txt")[0], 400)

    def test_download_unicode_name_head_and_range(self):
        (self.share / "café ☕.txt").write_text("hello world")
        url = "/download?path=" + quote("café ☕.txt")
        status, headers, data = self.request("GET", url, headers={"Cookie": self.cookie, "Range": "bytes=6-"})
        self.assertEqual((status, data), (206, b"world"))
        self.assertIn("filename*=UTF-8''caf%C3%A9%20%E2%98%95.txt", headers["Content-Disposition"])
        status, headers, data = self.request("HEAD", url, headers={"Cookie": self.cookie})
        self.assertEqual((status, data, headers["Content-Length"]), (200, b"", "11"))

    def test_chunk_upload_auto_renames(self):
        (self.share / "photo.jpg").write_text("existing")
        def put(upload_id, offset, total, payload, name="photo.jpg"):
            q = f"/upload_chunk?path=&name={quote(name)}&upload_id={upload_id}&offset={offset}&total={total}"
            return self.request("PUT", q, payload, {"Cookie": self.cookie, "Content-Length": str(len(payload))})
        self.assertEqual(put("u" * 20, 0, 6, b"abc")[0], 200)
        result = json.loads(put("u" * 20, 3, 6, b"def")[2])
        self.assertTrue(result["complete"])
        self.assertEqual(result["name"], "photo (1).jpg")
        self.assertEqual((self.share / "photo (1).jpg").read_bytes(), b"abcdef")
        self.assertEqual((self.share / "photo.jpg").read_text(), "existing")

    def test_cross_site_post_and_bad_methods_blocked(self):
        status, _, _ = self.request("POST", "/mkdir", "path=&name=evil", {"Cookie": self.cookie, "Origin": "https://evil.example", "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 403)
        self.assertFalse((self.share / "evil").exists())
        self.assertEqual(self.request("DELETE", "/", headers={"Cookie": self.cookie})[0], 405)

    def test_broken_symlink_does_not_crash_listing(self):
        link = self.share / "dangling"
        try:
            link.symlink_to(self.root / "does-not-exist")
        except OSError:
            self.skipTest("symlinks unavailable")
        self.assertEqual(self.request("GET", "/browse?path=", headers={"Cookie": self.cookie})[0], 200)
        link.unlink()


class UpdaterTests(unittest.TestCase):
    def _payload(self, version="9.9.9", extra=b""):
        source = f'APP_VERSION = "{version}"\n'.encode() + extra
        return source, hashlib.sha256(source).hexdigest() + "  sharer.py\n"

    def test_good_update_is_accepted(self):
        source, checksum = self._payload()
        sharer.verify_update_payload(source, checksum, "9.9.9")

    def test_tampered_update_is_rejected(self):
        source, checksum = self._payload()
        with self.assertRaises(ValueError):
            sharer.verify_update_payload(source + b"# tampered\n", checksum, "9.9.9")
        with self.assertRaises(ValueError):
            sharer.verify_update_payload(source, "not-a-checksum", "9.9.9")

    def test_wrong_version_syntax_error_and_oversize_rejected(self):
        source, checksum = self._payload()
        with self.assertRaises(ValueError):
            sharer.verify_update_payload(source, checksum, "1.0.0")
        broken, broken_sum = self._payload(extra=b"def oops(:\n")
        with self.assertRaises(ValueError):
            sharer.verify_update_payload(broken, broken_sum, "9.9.9")
        big, big_sum = self._payload(extra=b"#" * (sharer.MAX_UPDATE_BYTES + 1))
        with self.assertRaises(ValueError):
            sharer.verify_update_payload(big, big_sum, "9.9.9")

    def test_parse_release_notes_min_version_and_prereleases(self):
        base = {"tag_name": "v99.0.0", "draft": False, "prerelease": False, "body": "### Fixed\n- thing\n\nmin_version: 2.5.0\ncritical: true\n",
                "assets": [{"name": "sharer.py", "browser_download_url": "https://github.com/x/sharer.py"}, {"name": "sharer.py.sha256", "browser_download_url": "https://github.com/x/sharer.py.sha256"}]}
        parsed = sharer.parse_release(base)
        self.assertEqual((parsed["version"], parsed["min_version"], parsed["critical"]), ("99.0.0", "2.5.0", True))
        self.assertNotIn("critical", parsed["notes"])
        self.assertIsNone(sharer.parse_release({**base, "prerelease": True}))
        self.assertIsNone(sharer.parse_release({**base, "tag_name": "v0.0.1"}))
        self.assertIsNone(sharer.parse_release({**base, "assets": []}))

    def test_crashing_update_is_rolled_back(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            names = ("BACKUP_DIR", "VERSION_PATH", "PENDING_UPDATE_PATH", "ROLLBACK_NOTICE_PATH", "LOG_PATH", "CLOUDFLARED_DIR")
            saved = {name: getattr(sharer, name) for name in names}
            try:
                sharer.CLOUDFLARED_DIR = home; sharer.BACKUP_DIR = home / "backups"; sharer.BACKUP_DIR.mkdir()
                sharer.VERSION_PATH = home / "v.txt"; sharer.PENDING_UPDATE_PATH = home / "pending.json"
                sharer.ROLLBACK_NOTICE_PATH = home / "notice.txt"; sharer.LOG_PATH = home / "log.txt"
                target = home / "sharer.py"; target.write_text("# broken new version\n")
                (sharer.BACKUP_DIR / "sharer-2.0.0.py").write_text('APP_VERSION = "2.0.0"\n')
                sharer.VERSION_PATH.write_text("2.0.0\n")
                self.assertIsNone(sharer.startup_update_guard(target))  # first launch after update: marker only
                self.assertTrue(sharer.PENDING_UPDATE_PATH.exists())
                self.assertEqual(sharer.startup_update_guard(target), "2.0.0")  # never confirmed -> restore
                self.assertEqual(target.read_text(), 'APP_VERSION = "2.0.0"\n')
                self.assertFalse(sharer.PENDING_UPDATE_PATH.exists())
                self.assertTrue(sharer.ROLLBACK_NOTICE_PATH.exists())
            finally:
                for name, value in saved.items():
                    setattr(sharer, name, value)


class FakeTree:
    """Minimal stand-in for ttk.Treeview that, like the real one, drops the
    selection of any row that is deleted."""
    def __init__(self):
        self.rows: dict[str, tuple] = {}
        self.selected: set[str] = set()
        self.deletions = 0

    def get_children(self):
        return tuple(self.rows)

    def exists(self, iid):
        return iid in self.rows

    def item(self, iid, **options):
        self.rows[iid] = tuple(options["values"])

    def insert(self, parent, index, iid, values):
        self.rows[iid] = tuple(values)

    def delete(self, iid):
        self.deletions += 1
        self.rows.pop(iid, None)
        self.selected.discard(iid)


class DevicePanelTests(unittest.TestCase):
    def test_refresh_keeps_selected_device_selected(self):
        tree = FakeTree()
        sharer.sync_tree_rows(tree, [("a", ("Ann", "Online")), ("b", ("Ben", "Online"))])
        tree.selected = {"b"}
        sharer.sync_tree_rows(tree, [("a", ("Ann", "Away")), ("b", ("Ben", "Online")), ("c", ("Cy", "Online"))])
        self.assertEqual(tree.selected, {"b"})
        self.assertEqual(tree.deletions, 0)
        self.assertEqual(tree.rows["a"], ("Ann", "Away"))
        self.assertEqual(list(tree.rows), ["a", "b", "c"])

    def test_refresh_removes_only_disconnected_devices(self):
        tree = FakeTree()
        sharer.sync_tree_rows(tree, [("a", ("Ann",)), ("b", ("Ben",))])
        tree.selected = {"a", "b"}
        sharer.sync_tree_rows(tree, [("b", ("Ben",))])
        self.assertEqual(list(tree.rows), ["b"])
        self.assertEqual(tree.selected, {"b"})

    def test_device_order_is_stable_when_last_seen_changes(self):
        state = sharer.ShareState(Path(tempfile.gettempdir()), "correct horse battery")
        first = state.new_session("1.1.1.1", "ua")
        second = state.new_session("2.2.2.2", "ua")
        self.assertTrue(state.set_nickname(first, "Ann")[0])
        self.assertTrue(state.set_nickname(second, "Ben")[0])
        before = [token for token, _ in state.devices()]
        state.touch(first)
        state.touch(second)
        state.touch(first)
        self.assertEqual([token for token, _ in state.devices()], before)
        self.assertTrue(state.revoke_session(first))
        self.assertEqual([token for token, _ in state.devices()], [second])


if __name__ == "__main__":
    unittest.main()
