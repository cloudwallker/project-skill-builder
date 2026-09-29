import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "project-skill-builder" / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    from refresh_bundle import refresh_bundle
except ImportError:
    refresh_bundle = None


def make_bundle(root, version="1", content="generated old"):
    files = {
        "SKILL.md": "---\nname: example-task\ndescription: Use for repeated example tasks.\n---\n\n" + content + "\n",
        "agents/openai.yaml": 'interface:\n  display_name: "Example Task"\n  short_description: "Repeat project tasks"\n',
        "references/project-context.md": "Synthetic project constraints.\n",
        "references/decisions.md": "No third-party skill adopted.\n",
        "references/sources.md": "No external source adopted.\n",
        "references/user-overrides.md": "User additions.\n",
        "evals/cases.json": json.dumps({"cases": []}),
    }
    root.mkdir(parents=True, exist_ok=True)
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    manifest = {
        "schema_version": 1, "name": "example-task", "version": version, "sources": [],
        "stages": {"requirements": "complete", "discovery": "not_run", "reading": "not_run",
                   "license": "not_run", "static": "not_run", "behavior": "not_run"},
        "managed_files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                          for name in files if name != "references/user-overrides.md"},
    }
    (root / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def tree_bytes(root):
    return {str(path.relative_to(root)).replace("\\", "/"): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


class RefreshBundleTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(refresh_bundle, "refresh_bundle 尚未实现")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.current = self.root / "current"
        self.proposed = self.root / "proposed"
        make_bundle(self.current)
        make_bundle(self.proposed, version="2", content="generated new")

    def test_preview_reports_update_without_writing_any_directory(self):
        before = tree_bytes(self.root)
        result = refresh_bundle(self.current, self.proposed)
        self.assertEqual(result["status"], "preview")
        self.assertIn("SKILL.md", result["changed"])
        self.assertEqual(tree_bytes(self.root), before)
        self.assertIsNone(result["backup"])

    def test_apply_updates_and_backs_up_previous_bytes_outside_bundle(self):
        before = tree_bytes(self.current)
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual((self.current / "SKILL.md").read_bytes(), (self.proposed / "SKILL.md").read_bytes())
        backup = Path(result["backup"])
        self.assertEqual(tree_bytes(backup), before)
        self.assertEqual(backup.parents[2], self.current.parent / ".project-skill-builder-history")
        self.assertNotIn(self.current, backup.parents)

    def test_preserves_user_overrides_and_new_user_files(self):
        (self.current / "references/user-overrides.md").write_bytes(b"My exact additions\n")
        (self.current / "notes.txt").write_bytes(b"user-created bytes")
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual((self.current / "references/user-overrides.md").read_bytes(), b"My exact additions\n")
        self.assertEqual((self.current / "notes.txt").read_bytes(), b"user-created bytes")
        self.assertIn("references/user-overrides.md", result["preserved"])
        self.assertIn("notes.txt", result["preserved"])

    def test_concurrent_managed_edit_keeps_whole_current_and_creates_candidate(self):
        (self.current / "SKILL.md").write_text("human changed skill", encoding="utf-8")
        before = tree_bytes(self.current)
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "conflict", result)
        self.assertEqual(tree_bytes(self.current), before)
        self.assertEqual(result["conflicts"][0]["path"], "SKILL.md")
        self.assertEqual(tree_bytes(Path(result["candidate"])), tree_bytes(self.proposed))
        self.assertIsNone(result["backup"])

    def test_deleted_managed_file_is_a_conflict_when_proposed_changes_it(self):
        (self.current / "SKILL.md").unlink()
        before = tree_bytes(self.current)
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "conflict", result)
        self.assertTrue(any(item["path"] == "SKILL.md" for item in result["conflicts"]))
        self.assertEqual(tree_bytes(self.current), before)

    def test_local_edit_without_upstream_change_preserves_content_and_old_baseline(self):
        (self.current / "references/project-context.md").write_text("Human constraints", encoding="utf-8")
        old_manifest = json.loads((self.current / "bundle-manifest.json").read_text(encoding="utf-8"))
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual((self.current / "references/project-context.md").read_text(encoding="utf-8"), "Human constraints")
        new_manifest = json.loads((self.current / "bundle-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(new_manifest["managed_files"]["references/project-context.md"],
                         old_manifest["managed_files"]["references/project-context.md"])

    def test_unmanaged_same_name_file_and_directory_are_protected(self):
        for directory in [False, True]:
            with self.subTest(directory=directory):
                filename = "new-directory.md" if directory else "new-file.md"
                target = self.current / filename
                if directory:
                    target.mkdir()
                    (target / "note.txt").write_text("human", encoding="utf-8")
                else:
                    target.write_text("human", encoding="utf-8")
                proposed_target = self.proposed / filename
                proposed_target.write_text("generated", encoding="utf-8")
                manifest_path = self.proposed / "bundle-manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["managed_files"][filename] = hashlib.sha256(b"generated").hexdigest()
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                before = tree_bytes(self.current)
                result = refresh_bundle(self.current, self.proposed, apply=True)
                self.assertEqual(result["status"], "conflict", result)
                self.assertEqual(tree_bytes(self.current), before)
                if directory:
                    (target / "note.txt").unlink()
                    target.rmdir()
                else:
                    target.unlink()

    def test_rejects_invalid_proposal_without_mutating_current(self):
        (self.proposed / "agents/openai.yaml").unlink()
        before = tree_bytes(self.root)
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "invalid", result)
        self.assertEqual(tree_bytes(self.root), before)

    def test_rejects_stale_proposed_hash_without_mutating_current(self):
        (self.proposed / "SKILL.md").write_text("unsealed draft", encoding="utf-8")
        before = tree_bytes(self.root)
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "invalid", result)
        self.assertEqual(tree_bytes(self.root), before)

    def test_rejects_same_or_ancestor_roots_and_unsafe_history(self):
        for proposed, history in [(self.current, None), (self.root, None),
                                  (self.current / "nested", None), (self.proposed, self.current / "history")]:
            with self.subTest(proposed=proposed, history=history):
                before = tree_bytes(self.root)
                result = refresh_bundle(self.current, proposed, apply=True, history_root=history)
                self.assertEqual(result["status"], "invalid", result)
                self.assertEqual(tree_bytes(self.root), before)

    def test_rejects_links_and_manifest_path_escape(self):
        manifest_path = self.proposed / "bundle-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["managed_files"]["../outside.txt"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        before = tree_bytes(self.current)
        self.assertEqual(refresh_bundle(self.current, self.proposed, apply=True)["status"], "invalid")
        self.assertEqual(tree_bytes(self.current), before)
        make_bundle(self.proposed, version="2")
        try:
            (self.proposed / "external").symlink_to(self.current, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest("该环境不能创建符号链接: " + str(error))
        self.assertEqual(refresh_bundle(self.current, self.proposed, apply=True)["status"], "invalid")
        self.assertEqual(tree_bytes(self.current), before)

    def test_replace_failure_restores_current_bytes(self):
        before = tree_bytes(self.current)
        original_replace = os.replace

        def fail_final_replace(source, target):
            source_path, target_path = Path(source), Path(target)
            if target_path == self.current and source_path.name.startswith(".project-skill-builder-stage-"):
                raise OSError("synthetic final rename failure")
            return original_replace(source, target)

        with patch("refresh_bundle.os.replace", side_effect=fail_final_replace):
            result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(tree_bytes(self.current), before)

    def test_cli_defaults_to_preview_and_has_help(self):
        script = SCRIPTS / "refresh_bundle.py"
        before = tree_bytes(self.current)
        result = subprocess.run([sys.executable, str(script), "--current", str(self.current),
                                 "--proposed", str(self.proposed)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "preview")
        self.assertEqual(tree_bytes(self.current), before)
        result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)

    def test_cli_accepts_documented_positional_bundle_roots(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / "refresh_bundle.py"), str(self.current),
                                 str(self.proposed)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "preview")

    def test_unmanaged_file_cannot_be_replaced_with_proposed_directory(self):
        (self.current / "user-notes").write_bytes(b"human notes")
        (self.proposed / "user-notes").mkdir()
        (self.proposed / "user-notes" / "generated.md").write_bytes(b"generated notes")
        manifest_path = self.proposed / "bundle-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["managed_files"]["user-notes/generated.md"] = hashlib.sha256(b"generated notes").hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        before = tree_bytes(self.current)
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "conflict", result)
        self.assertEqual(tree_bytes(self.current), before)
        self.assertTrue(any(item["path"] == "user-notes/generated.md" for item in result["conflicts"]))

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive file paths")
    def test_case_alias_cannot_overwrite_unmanaged_file(self):
        from validate_bundle import seal_bundle
        (self.current / "references/User.md").write_bytes(b"USER BYTES")
        (self.proposed / "references/user.md").write_bytes(b"PROPOSED BYTES")
        self.assertTrue(seal_bundle(self.proposed)["valid"])
        before = tree_bytes(self.current)
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "conflict", result)
        self.assertEqual(tree_bytes(self.current), before)
        self.assertTrue(any(item["path"] == "references/user.md" for item in result["conflicts"]))

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive directory paths")
    def test_case_alias_cannot_replace_unmanaged_directory(self):
        from validate_bundle import seal_bundle
        directory = self.current / "references/User.md"
        directory.mkdir()
        (directory / "human.txt").write_bytes(b"USER BYTES")
        (self.proposed / "references/user.md").write_bytes(b"PROPOSED BYTES")
        self.assertTrue(seal_bundle(self.proposed)["valid"])
        before = tree_bytes(self.current)
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "conflict", result)
        self.assertEqual(tree_bytes(self.current), before)

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive parent paths")
    def test_case_alias_parent_file_is_reported_as_conflict_before_staging(self):
        from validate_bundle import seal_bundle
        (self.current / "User-Notes").write_bytes(b"USER BYTES")
        (self.proposed / "user-notes").mkdir()
        (self.proposed / "user-notes/generated.md").write_bytes(b"PROPOSED BYTES")
        self.assertTrue(seal_bundle(self.proposed)["valid"])
        before = tree_bytes(self.current)
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "conflict", result)
        self.assertEqual(tree_bytes(self.current), before)

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive managed paths")
    def test_managed_case_alias_preserves_human_edit_when_upstream_bytes_unchanged(self):
        from validate_bundle import seal_bundle
        (self.current / "references/Policy.md").write_bytes(b"BASELINE BYTES")
        self.assertTrue(seal_bundle(self.current)["valid"])
        (self.current / "references/Policy.md").write_bytes(b"USER BYTES")
        (self.proposed / "references/policy.md").write_bytes(b"BASELINE BYTES")
        self.assertTrue(seal_bundle(self.proposed)["valid"])
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual((self.current / "references/Policy.md").read_bytes(), b"USER BYTES")
        manifest = json.loads((self.current / "bundle-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["managed_files"]["references/policy.md"], hashlib.sha256(b"BASELINE BYTES").hexdigest())

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive reserved paths")
    def test_case_alias_of_user_overrides_cannot_be_managed(self):
        manifest_path = self.proposed / "bundle-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["managed_files"]["references/User-Overrides.md"] = hashlib.sha256(
            (self.proposed / "references/user-overrides.md").read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        before = tree_bytes(self.current)
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "invalid", result)
        self.assertEqual(tree_bytes(self.current), before)

    def test_missing_staged_new_managed_file_does_not_apply_a_broken_candidate(self):
        from validate_bundle import seal_bundle
        (self.proposed / "references/new-resource.txt").write_bytes(b"PROPOSED BYTES")
        self.assertTrue(seal_bundle(self.proposed)["valid"])
        before = tree_bytes(self.current)
        original_copy = __import__("shutil").copy2

        def omit_new_resource(source, target, *args, **kwargs):
            if Path(source) == self.proposed / "references/new-resource.txt":
                return str(target)
            return original_copy(source, target, *args, **kwargs)

        with patch("refresh_bundle.shutil.copy2", side_effect=omit_new_resource):
            result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "invalid", result)
        self.assertEqual(tree_bytes(self.current), before)

    @unittest.skipUnless(os.name == "nt", "Windows filesystem Unicode path identity")
    def test_distinct_unicode_filenames_are_not_merged_by_python_casefold(self):
        from validate_bundle import seal_bundle
        (self.current / "references/Straße.md").write_bytes(b"BASELINE BYTES")
        if (self.current / "references/strasse.md").exists():
            self.skipTest("该文件系统将此Unicode名称视为同一路径")
        self.assertTrue(seal_bundle(self.current)["valid"])
        (self.current / "references/Straße.md").write_bytes(b"USER BYTES")
        (self.proposed / "references/strasse.md").write_bytes(b"BASELINE BYTES")
        self.assertTrue(seal_bundle(self.proposed)["valid"])
        before = tree_bytes(self.current)
        result = refresh_bundle(self.current, self.proposed, apply=True)
        self.assertEqual(result["status"], "conflict", result)
        self.assertEqual(tree_bytes(self.current), before)

    @unittest.skipUnless(os.name == "nt", "Windows junction only")
    def test_junction_root_is_refused_without_changing_target(self):
        junction = self.root / "junction-current"
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(self.current)],
                                 capture_output=True, text=True)
        if created.returncode != 0:
            self.skipTest("该环境不允许创建 Windows junction")
        try:
            before = tree_bytes(self.current)
            result = refresh_bundle(junction, self.proposed, apply=True)
            self.assertEqual(result["status"], "invalid", result)
            self.assertEqual(tree_bytes(self.current), before)
        finally:
            os.rmdir(str(junction))


if __name__ == "__main__":
    unittest.main()
