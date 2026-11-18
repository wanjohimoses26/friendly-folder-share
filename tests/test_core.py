import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sharer", ROOT / "sharer.py")
sharer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sharer)


class CoreTests(unittest.TestCase):
    def test_safe_child_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(PermissionError):
                sharer.safe_child(root, "../outside")

    def test_nickname_validation_and_uniqueness(self):
        with tempfile.TemporaryDirectory() as directory:
            state = sharer.ShareState(Path(directory), "strong-password")
            first = state.new_session("127.0.0.1", "test")
            second = state.new_session("127.0.0.2", "test")
            self.assertEqual(state.set_nickname(first, "Phone")[0], True)
            self.assertEqual(state.set_nickname(second, "phone")[0], False)

    def test_rate_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            state = sharer.ShareState(Path(directory), "strong-password")
            for _ in range(sharer.LOGIN_MAX_FAILURES):
                state.record_login_failure("test")
            self.assertFalse(state.login_allowed("test"))

    def test_checksum(self):
        data = b"safe release"
        self.assertEqual(hashlib.sha256(data).hexdigest(), hashlib.sha256(data).hexdigest())

    def test_search(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs" / "Needle.txt").write_text("x")
            matches = sharer.search_files(root, "needle")
            self.assertEqual([relative for _, relative in matches], ["docs/Needle.txt"])


if __name__ == "__main__":
    unittest.main()
