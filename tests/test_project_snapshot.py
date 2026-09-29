import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "project-skill-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    from project_snapshot import compare_snapshots, snapshot_project
except ImportError:
    compare_snapshots = snapshot_project = None


class ProjectSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(snapshot_project, "snapshot_project 尚未实现")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value.encode("utf-8"))
        return path

    def test_hashes_only_explicit_files_and_emits_relative_paths(self):
        self.write("README.md", "hello\n")
        self.write("src/main.py", "print('ok')\n")
        self.write("unrequested.txt", "leave alone")
        result = snapshot_project(self.root, ["README.md", "src/main.py"])
        self.assertEqual(set(result["files"]), {"README.md", "src/main.py"})
        self.assertEqual(result["files"]["README.md"], hashlib.sha256(b"hello\n").hexdigest())
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_compare_detects_added_changed_and_removed_files(self):
        self.write("README.md", "old")
        self.write("removed.py", "old")
        before = snapshot_project(self.root, ["README.md", "removed.py"])
        self.write("README.md", "new")
        self.write("added.py", "new")
        after = snapshot_project(self.root, ["README.md", "added.py"])
        self.assertEqual(compare_snapshots(before, after), {
            "added": ["added.py"], "changed": ["README.md"], "removed": ["removed.py"]
        })

    def test_refuses_sensitive_paths_without_reading_them(self):
        for name in [".env", ".env.local", ".ssh/id_rsa", "credentials.json", "private.key", "secrets/token.txt",
                     ".codex/auth.json", ".npmrc", ".pypirc", ".git-credentials", ".docker/config.json"]:
            with self.subTest(name=name):
                self.write(name, "synthetic forbidden content")
                with self.assertRaises(ValueError):
                    snapshot_project(self.root, [name])

    def test_refuses_project_roots_nested_inside_personal_credential_directories(self):
        nested = self.root.parent / ".ssh" / "nested-project"
        nested.mkdir(parents=True)
        (nested / "README.md").write_bytes(b"synthetic")
        with self.assertRaises(ValueError):
            snapshot_project(nested, ["README.md"])

    def test_refuses_absolute_parent_and_directory_paths(self):
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        for name in [str(outside), "../outside.txt", "src/../../outside.txt", "."]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                snapshot_project(self.root, [name])

    def test_refuses_file_and_ancestor_symlinks(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        (outside / "data.txt").write_text("outside", encoding="utf-8")
        try:
            (self.root / "linked").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest("该环境不能创建符号链接: " + str(error))
        with self.assertRaises(ValueError):
            snapshot_project(self.root, ["linked/data.txt"])

    def test_missing_file_does_not_produce_a_fake_hash(self):
        with self.assertRaises(FileNotFoundError):
            snapshot_project(self.root, ["missing.txt"])

    def test_cli_writes_snapshot_and_compares_real_json_files(self):
        self.write("README.md", "old")
        script = SCRIPTS / "project_snapshot.py"
        before = self.root.parent / "before.json"
        after = self.root.parent / "after.json"
        for output, content in [(before, "old"), (after, "new")]:
            self.write("README.md", content)
            result = subprocess.run([sys.executable, str(script), "snapshot", "--root", str(self.root),
                                     "--files", "README.md", "--output", str(output)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(set(json.loads(output.read_text(encoding="utf-8"))["files"]), {"README.md"})
        result = subprocess.run([sys.executable, str(script), "compare", "--before", str(before),
                                 "--after", str(after)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"added": [], "changed": ["README.md"], "removed": []})
        help_result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
