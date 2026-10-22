"""scripts/export-public.sh: clean public export with a leak check (stage 5 S2, S3)."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export-public.sh"
SECRET = "Initech"


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"), "needs bash and git")
class ExportScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        files = {
            "README.md": "Acme on odoo.example.com\n",
            "src/app.py": "print('hello')\n",
            "odd/notes.md": f"internal: {SECRET}\n",
            "odd/release/denylist.txt": f"# private terms\n\n{SECRET}\nsecret-host\\.example\n",
            ".gitignore": "ignored.txt\n",
        }
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        (self.repo / "ignored.txt").write_text(f"{SECRET}\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")
        self.dest = self.tmp / "out" / "agentodoo"

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)

    def commit(self):
        self.git("add", "-A")
        self.git("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "more")

    def run_script(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        result = subprocess.run(["bash", str(SCRIPT), *args], cwd=self.repo, env=env,
                                capture_output=True, text=True)
        self.assertNotIn("secret-host", result.stdout + result.stderr)
        self.assertNotIn("# private terms", result.stdout + result.stderr)
        return result

    def test_clean_export_excludes_odd_and_ignored(self):
        result = self.run_script(str(self.dest))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.dest / "README.md").is_file())
        self.assertTrue((self.dest / "src" / "app.py").is_file())
        self.assertFalse((self.dest / "odd").exists())
        self.assertFalse((self.dest / ".git").exists())
        self.assertFalse((self.dest / "ignored.txt").exists())
        self.assertIn("3 files", result.stdout)
        self.assertIn('git init -b main && git add -A && git commit -m "Initial public release"', result.stdout)
        self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_leak_fails_and_removes_dest(self):
        (self.repo / "src" / "leak.py").write_text("x = 1\n# made for initech\n")
        self.commit()
        result = self.run_script(str(self.dest))
        self.assertEqual(result.returncode, 1)
        self.assertIn("src/leak.py:2", result.stdout + result.stderr)
        self.assertFalse(self.dest.exists())

    def test_dirty_worktree_refused(self):
        (self.repo / "README.md").write_text("changed\n")
        result = self.run_script(str(self.dest))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uncommitted", result.stderr)
        self.assertFalse(self.dest.exists())

    def test_non_empty_dest_refused(self):
        self.dest.mkdir(parents=True)
        (self.dest / "keep.txt").write_text("mine\n")
        result = self.run_script(str(self.dest))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not empty", result.stderr)
        self.assertEqual((self.dest / "keep.txt").read_text(), "mine\n")

    def test_missing_denylist_refused(self):
        result = self.run_script(str(self.dest), str(self.tmp / "nope.txt"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("denylist", result.stderr)
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()
