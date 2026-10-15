"""Prevent private content from reaching the public commit, including forced staging."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_source", ROOT / "scripts/check_public_source.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class PublicSourceTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".local").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".local")
        self.root = Path(self.temp.name)
        self.git("init", "--quiet")
        (self.root / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True).stdout

    def write(self, name, body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def post(self, draft):
        return f'+++\ntitle = "Test"\ndate = "2099-01-01"\n{draft}\n+++\nPRIVATE BODY SENTINEL'

    def test_local_tools_and_drafts_are_ignored_by_normal_add(self):
        for path in [*guard.LOCAL_PATHS[2:], "editor/index.html", ".local/drafts/note.md"]:
            self.write(path, "local content")
        self.write("content/posts/ready.md", self.post("draft = false"))
        self.git("add", "--all")
        guard.check_staged(self.root)
        tracked = self.git("ls-files").decode()
        self.assertEqual(set(tracked.splitlines()), {".gitignore", "content/posts/ready.md"})

    def test_forced_staged_editor_and_private_draft_are_rejected(self):
        for path in ["editor/index.html", "scripts/editor_server.py", ".local/drafts/note.md"]:
            with self.subTest(path=path):
                self.write(path, "local content")
                self.git("add", "--force", "--", path)
                with self.assertRaisesRegex(ValueError, "local-only"):
                    guard.check_staged(self.root)
                self.git("rm", "--cached", "--", path)

    def test_draft_missing_flag_and_invalid_metadata_fail_closed(self):
        for content in [self.post("draft = true"), self.post(""), self.post('draft = "false"'), "not metadata"]:
            with self.subTest(content=content):
                self.write("content/posts/note.md", content)
                with self.assertRaises(ValueError) as caught:
                    guard.check_worktree(self.root)
                self.assertNotIn("PRIVATE BODY SENTINEL", str(caught.exception))
                self.git("add", "content/posts/note.md")
                with self.assertRaises(ValueError):
                    guard.check_staged(self.root)

    def test_index_is_checked_even_when_worktree_looks_publishable(self):
        self.write("content/posts/note.md", self.post("draft = true"))
        self.git("add", "content/posts/note.md")
        self.write("content/posts/note.md", self.post("draft = false"))
        guard.check_worktree(self.root)
        with self.assertRaisesRegex(ValueError, "private draft"):
            guard.check_staged(self.root)

    def test_private_directory_is_not_scanned_and_ready_posts_are_allowed(self):
        self.write(".local/drafts/note.md", self.post("draft = true"))
        self.write("content/posts/scheduled.md", self.post("draft = false"))
        guard.check_worktree(self.root)
        self.git("add", "--all")
        guard.check_staged(self.root)


if __name__ == "__main__":
    unittest.main()
