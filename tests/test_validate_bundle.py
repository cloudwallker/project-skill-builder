"""Exercise the bundle validator against files, not source text."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/project-skill-builder/scripts/validate_bundle.py"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_bundle(root):
    """Build an independent fixture with a controlled, honestly labelled source."""
    files = {
        "SKILL.md": "---\nname: csv-summary\ndescription: 为项目生成 CSV 汇总。\n---\n读取 [项目上下文](references/project-context.md)。\n",
        "agents/openai.yaml": "interface:\n  display_name: CSV summary\n",
        "references/project-context.md": "# Context\nPython standard library only.\n",
        "references/decisions.md": "# Decisions\nUse the controlled fixture.\n",
        "references/sources.md": "# Sources\nA controlled fixture; no public discovery claimed.\n",
        "references/user-overrides.md": "# User overrides\nKeep this text unchanged.\n",
        "evals/cases.json": '{"schema_version": 1, "cases": []}\n',
    }
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "name": "csv-summary",
        "version": "0.1.0",
        "sources": [{
            "id": "controlled-summary",
            "kind": "fixture",
            "url": None,
            "path": "fixtures/summary/SKILL.md",
            "license": "MIT",
            "status": "adopted",
            "reason": "Owned fixture for repeatable policy tests.",
            "version": {"commit": None, "read_at": "2026-09-29T00:00:00Z"},
        }],
        "stages": {"requirements": "complete", "discovery": "partial", "reading": "complete", "license": "complete", "static": "passed", "behavior": "not_run"},
        "managed_files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files if name != "references/user-overrides.md"},
    }
    write_json(root / "bundle-manifest.json", manifest)
    return manifest


class ValidateBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "bundle"
        self.root.mkdir()
        self.manifest = make_bundle(self.root)

    def api(self):
        if not SCRIPT.is_file():
            self.fail("validate_bundle.py has not been implemented")
        if self.__class__.module is None:
            spec = importlib.util.spec_from_file_location("bundle_validator_under_test", SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.__class__.module = module
        return self.__class__.module

    def save_manifest(self):
        write_json(self.root / "bundle-manifest.json", self.manifest)

    def validate(self, check_hashes=True):
        return self.api().validate_bundle(self.root, check_hashes=check_hashes)

    def assert_invalid(self, result, clue):
        self.assertFalse(result["valid"], result)
        self.assertTrue(any(clue.lower() in error.lower() for error in result["errors"]), result)

    def test_complete_bundle_validates(self):
        result = self.validate()
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["modified_files"], [])

    @unittest.skipUnless(os.name == "nt", "Windows path aliases")
    def test_managed_paths_cannot_alias_by_case(self):
        self.manifest['managed_files']['references/DECISIONS.md'] = self.manifest['managed_files']['references/decisions.md']
        self.save_manifest()
        self.assert_invalid(self.validate(), 'alias')

    @unittest.skipUnless(os.name == "nt", "Windows reserved path aliases")
    def test_reserved_user_overrides_case_alias_is_not_managed(self):
        self.manifest['managed_files']['references/User-Overrides.md'] = hashlib.sha256((self.root / 'references/user-overrides.md').read_bytes()).hexdigest()
        self.save_manifest()
        self.assert_invalid(self.validate(), 'user-overrides')

    def test_seal_preserves_distinct_unicode_names_that_casefold_expands(self):
        first = self.root / "references/Straße.md"
        second = self.root / "references/strasse.md"
        first.write_bytes(b"first unicode file\n")
        second.write_bytes(b"second unicode file\n")
        self.assertFalse(os.path.samefile(str(first), str(second)))
        result = self.api().seal_bundle(self.root)
        self.assertTrue(result["valid"], result)
        managed = json.loads((self.root / "bundle-manifest.json").read_text(encoding="utf-8"))["managed_files"]
        self.assertEqual(managed["references/Straße.md"], hashlib.sha256(b"first unicode file\n").hexdigest())
        self.assertEqual(managed["references/strasse.md"], hashlib.sha256(b"second unicode file\n").hexdigest())
        self.assertTrue(self.validate()["valid"])

    @unittest.skipIf(os.name == "nt", "POSIX distinct case-sensitive file names")
    def test_posix_seal_preserves_distinct_upper_and_lower_case_files(self):
        first = self.root / "references/Foo.md"
        second = self.root / "references/foo.md"
        first.write_bytes(b"upper file\n")
        second.write_bytes(b"lower file\n")
        if os.path.samefile(str(first), str(second)):
            self.skipTest("Temporary filesystem does not support distinct case names")
        result = self.api().seal_bundle(self.root)
        self.assertTrue(result["valid"], result)
        managed = json.loads((self.root / "bundle-manifest.json").read_text(encoding="utf-8"))["managed_files"]
        self.assertEqual(managed["references/Foo.md"], hashlib.sha256(b"upper file\n").hexdigest())
        self.assertEqual(managed["references/foo.md"], hashlib.sha256(b"lower file\n").hexdigest())

    @unittest.skipUnless(os.name == "nt", "Windows short path aliases")
    def test_reserved_short_path_alias_is_not_managed(self):
        import ctypes

        target = self.root / "references/user-overrides.md"
        output = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(target), output, len(output))
        if not length:
            self.skipTest("Windows short path lookup is unavailable")
        short_name = Path(output.value).name
        if short_name.lower() == target.name.lower():
            self.skipTest("Temporary filesystem has no 8.3 short alias")
        self.manifest["managed_files"]["references/" + short_name] = hashlib.sha256(target.read_bytes()).hexdigest()
        self.save_manifest()
        self.assert_invalid(self.validate(), "cannot include")

    def test_missing_required_reference_is_error(self):
        (self.root / "references/project-context.md").unlink()
        self.assert_invalid(self.validate(), "project-context.md")

    def test_missing_manifest_is_error(self):
        (self.root / "bundle-manifest.json").unlink()
        self.assert_invalid(self.validate(), "bundle-manifest.json")

    def test_malformed_manifest_returns_errors(self):
        (self.root / "bundle-manifest.json").write_text("{", encoding="utf-8")
        self.assert_invalid(self.validate(), "json")

    def test_unmanaged_required_content_is_error(self):
        del self.manifest["managed_files"]["evals/cases.json"]
        self.save_manifest()
        self.assert_invalid(self.validate(), "evals/cases.json")

    def test_manual_edit_is_warning_without_blocking_refresh(self):
        target = self.root / "references/decisions.md"
        target.write_text("User edited the decision.\n", encoding="utf-8")
        result = self.validate()
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["modified_files"], ["references/decisions.md"])
        self.assertTrue(result["warnings"])

    def test_deleted_optional_managed_content_is_modified(self):
        self.manifest["managed_files"]["references/old-note.md"] = "0" * 64
        self.save_manifest()
        result = self.validate()
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["modified_files"], ["references/old-note.md"])

    def test_disabled_hash_check_does_not_report_modifications(self):
        (self.root / "references/decisions.md").write_text("Edited.\n", encoding="utf-8")
        self.assertEqual(self.validate(check_hashes=False)["modified_files"], [])

    def test_invalid_sha_is_error_even_when_hash_check_disabled(self):
        self.manifest["managed_files"]["SKILL.md"] = "not-a-hash"
        self.save_manifest()
        self.assert_invalid(self.validate(check_hashes=False), "sha256")

    def test_manifest_cannot_manage_itself_or_user_overrides(self):
        for forbidden in ("bundle-manifest.json", "references/user-overrides.md"):
            with self.subTest(path=forbidden):
                self.manifest["managed_files"][forbidden] = "0" * 64
                self.save_manifest()
                self.assert_invalid(self.validate(), forbidden)
                del self.manifest["managed_files"][forbidden]

    def test_manifest_paths_cannot_escape(self):
        for unsafe in ("../outside.md", "/tmp/outside.md", "C:/outside.md", "..\\outside.md", "\\\\server\\share\\outside.md"):
            with self.subTest(path=unsafe):
                self.manifest["managed_files"][unsafe] = "0" * 64
                self.save_manifest()
                self.assert_invalid(self.validate(), "path")
                del self.manifest["managed_files"][unsafe]

    def test_extension_resource_paths_cannot_escape(self):
        self.manifest["resource_paths"] = ["../outside.md"]
        self.save_manifest()
        self.assert_invalid(self.validate(), "path")

    def test_embedded_project_snapshot_cannot_record_escaping_paths(self):
        self.manifest["project_snapshot"] = {"schema_version": 1, "files": {"../outside.md": "0" * 64}}
        self.save_manifest()
        self.assert_invalid(self.validate(), "path")

    def test_embedded_project_snapshot_requires_valid_sha(self):
        self.manifest["project_snapshot"] = {"schema_version": 1, "files": {"README.md": "fake"}}
        self.save_manifest()
        self.assert_invalid(self.validate(), "sha256")

    def test_linked_project_snapshot_file_must_exist(self):
        self.manifest["project_snapshot"] = "references/missing-snapshot.json"
        self.save_manifest()
        self.assert_invalid(self.validate(), "missing-snapshot.json")

    def test_embedded_project_snapshot_records_project_paths_without_reading_project(self):
        self.manifest["project_snapshot"] = {"schema_version": 1, "files": {"README.md": "0" * 64}}
        self.save_manifest()
        self.assertTrue(self.validate()["valid"])

    def test_markdown_missing_local_reference_is_error(self):
        with (self.root / "SKILL.md").open("a", encoding="utf-8") as stream:
            stream.write("[Missing](references/missing.md)\n")
        self.assert_invalid(self.validate(), "missing.md")

    def test_markdown_reference_escape_is_error(self):
        with (self.root / "SKILL.md").open("a", encoding="utf-8") as stream:
            stream.write("[Outside](../outside.md)\n")
        self.assert_invalid(self.validate(), "path")

    def test_percent_encoded_reference_escape_is_error(self):
        with (self.root / "SKILL.md").open("a", encoding="utf-8") as stream:
            stream.write("[Outside](%2e%2e/outside.md)\n")
        self.assert_invalid(self.validate(), "path")

    def test_inline_resource_reference_must_exist(self):
        with (self.root / "SKILL.md").open("a", encoding="utf-8") as stream:
            stream.write("Run `scripts/missing.py` before completion.\n")
        self.assert_invalid(self.validate(), "missing.py")

    def test_inline_project_install_template_is_not_a_bundle_resource(self):
        with (self.root / "SKILL.md").open("a", encoding="utf-8") as stream:
            stream.write("Install under `<project>/.agents/skills/<name>/`.\n")
            stream.write("Use `scripts/<script-name>.py` as a placeholder.\n")
        self.assertTrue(self.validate()["valid"])

    def test_malformed_external_markdown_url_returns_error_instead_of_crashing(self):
        with (self.root / "SKILL.md").open("a", encoding="utf-8") as stream:
            stream.write("[Broken URL](https://[)\n")
        self.assert_invalid(self.validate(), "reference")

    def test_agent_icon_file_must_exist(self):
        (self.root / "agents/openai.yaml").write_text('interface:\n  display_name: CSV\n  icon_small: "./assets/missing.png"\n', encoding="utf-8")
        self.assert_invalid(self.validate(), "missing.png")

    def test_agent_icon_path_is_relative_to_bundle_root(self):
        (self.root / "assets").mkdir()
        (self.root / "assets/icon.png").write_bytes(b"test icon bytes")
        (self.root / "agents/openai.yaml").write_text('interface:\n  display_name: CSV\n  icon_small: "./assets/icon.png"\n', encoding="utf-8")
        self.assertTrue(self.validate()["valid"])

    def test_symlink_resource_is_rejected(self):
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("External content\n", encoding="utf-8")
        linked = self.root / "references/linked.md"
        try:
            linked.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("System cannot create symlinks: " + str(exc))
        self.assert_invalid(self.validate(), "link")

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_windows_junction_is_rejected_without_following_it(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "private.txt").write_text("Example external bytes\n", encoding="utf-8")
        junction = self.root / "assets"
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True, text=True)
        if created.returncode:
            self.skipTest("System cannot create a junction")
        self.addCleanup(lambda: os.rmdir(str(junction)) if junction.exists() else None)
        self.assert_invalid(self.validate(), "link")

    def test_unknown_license_cannot_be_adopted(self):
        self.manifest["sources"][0]["license"] = "unknown"
        self.save_manifest()
        self.assert_invalid(self.validate(), "license")

    def test_adopted_license_must_be_checked(self):
        self.manifest["stages"]["license"] = "partial"
        self.save_manifest()
        self.assert_invalid(self.validate(), "license")

    def test_unknown_license_reference_can_be_recorded(self):
        self.manifest["sources"][0].update(license="unknown", status="reference")
        self.manifest["stages"]["license"] = "partial"
        self.save_manifest()
        self.assertTrue(self.validate()["valid"])

    def test_fixture_cannot_claim_complete_public_discovery(self):
        self.manifest["stages"]["discovery"] = "complete"
        self.save_manifest()
        self.assert_invalid(self.validate(), "discovery")

    def test_fixture_cannot_impersonate_public_url(self):
        self.manifest["sources"][0]["url"] = "https://github.com/example/pretend"
        self.save_manifest()
        self.assert_invalid(self.validate(), "fixture")

    def test_public_source_enables_complete_discovery(self):
        self.manifest["sources"][0].update(kind="skill", url="https://github.com/example/public/blob/main/SKILL.md")
        self.manifest["stages"]["discovery"] = "complete"
        self.save_manifest()
        self.assertTrue(self.validate()["valid"])

    def test_empty_sources_cannot_claim_discovery_or_reading(self):
        self.manifest["sources"] = []
        for stage in ("discovery", "reading"):
            with self.subTest(stage=stage):
                self.manifest["stages"] = {"requirements": "complete", "discovery": "not_run", "reading": "not_run", "license": "not_run", "static": "passed", "behavior": "not_run"}
                self.manifest["stages"][stage] = "complete"
                self.save_manifest()
                self.assert_invalid(self.validate(), stage)

    def test_source_requires_valid_version_and_read_time(self):
        for version in ({"commit": "invented", "read_at": "2026-09-29T00:00:00Z"}, {"commit": None, "read_at": "yesterday"}, {"commit": None, "read_at": "2026-09-29"}):
            with self.subTest(version=version):
                self.manifest["sources"][0]["version"] = version
                self.save_manifest()
                self.assert_invalid(self.validate(), "version")

    def test_source_path_cannot_escape(self):
        self.manifest["sources"][0]["path"] = "../../original/SKILL.md"
        self.save_manifest()
        self.assert_invalid(self.validate(), "path")

    def test_stage_status_cannot_imply_checks_that_did_not_run(self):
        for stage, state in (("requirements", "passed"), ("behavior", "complete"), ("static", "partial")):
            with self.subTest(stage=stage):
                previous = self.manifest["stages"][stage]
                self.manifest["stages"][stage] = state
                self.save_manifest()
                self.assert_invalid(self.validate(), stage)
                self.manifest["stages"][stage] = previous

    def test_behavior_passed_requires_results_file(self):
        self.manifest["stages"]["behavior"] = "passed"
        self.save_manifest()
        self.assert_invalid(self.validate(), "behavior_results")

    def test_behavior_results_must_be_real_structured_evidence(self):
        self.manifest["stages"]["behavior"] = "passed"
        self.manifest["behavior_results"] = "evals/results.json"
        self.save_manifest()
        for evidence in ({"schema_version": 1, "scenarios": []}, {"schema_version": 1, "scenarios": [{"id": "test", "generated": {"status": "passed"}}]}):
            with self.subTest(evidence=evidence):
                write_json(self.root / "evals/results.json", evidence)
                self.assert_invalid(self.validate(), "behavior")

    def test_behavior_results_failed_generated_status_conflicts_with_passed(self):
        self.manifest["stages"]["behavior"] = "passed"
        self.manifest["behavior_results"] = "evals/results.json"
        self.save_manifest()
        write_json(self.root / "evals/results.json", {"schema_version": 1, "scenarios": [{"id": "case", "baseline": {"status": "passed"}, "generated": {"status": "failed"}}]})
        self.assert_invalid(self.validate(), "behavior")

    def test_behavior_results_accept_observed_equal_outcomes(self):
        self.manifest["stages"]["behavior"] = "passed"
        self.manifest["behavior_results"] = "evals/results.json"
        write_json(self.root / "evals/results.json", {"schema_version": 1, "scenarios": [{"id": "case", "baseline": {"status": "passed"}, "generated": {"status": "passed"}}]})
        self.manifest["managed_files"]["evals/results.json"] = hashlib.sha256((self.root / "evals/results.json").read_bytes()).hexdigest()
        self.save_manifest()
        self.assertTrue(self.validate()["valid"])

    def test_frontmatter_requires_name_description_and_unique_fields(self):
        for frontmatter in ("name: csv-summary", "description: example", "name: csv-summary\ndescription: \n", "name: csv-summary\nname: duplicate\ndescription: example", "name: [csv-summary]\ndescription: example"):
            with self.subTest(frontmatter=frontmatter):
                (self.root / "SKILL.md").write_text("---\n" + frontmatter + "\n---\n", encoding="utf-8")
                self.assert_invalid(self.validate(), "frontmatter")

    def test_frontmatter_supports_quoted_unicode_and_folded_description(self):
        for frontmatter in ('name: "csv-summary"\ndescription: "处理 CSV 文件"', "name: csv-summary\ndescription: >\n  处理 CSV 文件\n  并验证项目约束。"):
            with self.subTest(frontmatter=frontmatter):
                (self.root / "SKILL.md").write_text("---\n" + frontmatter + "\n---\n", encoding="utf-8")
                self.assertTrue(self.validate()["valid"])

    def test_manifest_name_matches_skill(self):
        self.manifest["name"] = "wrong-name"
        self.save_manifest()
        self.assert_invalid(self.validate(), "name")

    def test_seal_updates_hashes_preserving_user_overrides_bytes(self):
        overrides = self.root / "references/user-overrides.md"
        overrides.write_bytes(b"User data\r\nwith exact bytes.\r\n")
        (self.root / "references/decisions.md").write_bytes(b"new decision\n")
        (self.root / "scripts").mkdir()
        (self.root / "scripts/helper.py").write_bytes(b"print('hello')\n")
        before = overrides.read_bytes()
        result = self.api().seal_bundle(self.root)
        self.assertTrue(result["valid"], result)
        actual = json.loads((self.root / "bundle-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(overrides.read_bytes(), before)
        self.assertNotIn("bundle-manifest.json", actual["managed_files"])
        self.assertNotIn("references/user-overrides.md", actual["managed_files"])
        self.assertEqual(actual["managed_files"]["scripts/helper.py"], hashlib.sha256(b"print('hello')\n").hexdigest())
        self.assertEqual(actual["managed_files"]["references/decisions.md"], hashlib.sha256(b"new decision\n").hexdigest())
        self.assertTrue(self.validate()["valid"])

    def test_seal_does_not_write_manifest_on_invalid_bundle(self):
        before = (self.root / "bundle-manifest.json").read_bytes()
        (self.root / "references/project-context.md").unlink()
        self.assertFalse(self.api().seal_bundle(self.root)["valid"])
        self.assertEqual((self.root / "bundle-manifest.json").read_bytes(), before)

    def test_seal_can_populate_empty_managed_file_map(self):
        self.manifest["managed_files"] = {}
        self.save_manifest()
        result = self.api().seal_bundle(self.root)
        self.assertTrue(result["valid"], result)
        self.assertTrue(self.validate()["valid"])

    def test_cli_validate_and_seal_emit_json_and_exit_codes(self):
        self.api()
        for command in ("validate", "seal"):
            completed = subprocess.run([sys.executable, str(SCRIPT), command, str(self.root)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(json.loads(completed.stdout)["valid"])
        (self.root / "SKILL.md").unlink()
        completed = subprocess.run([sys.executable, str(SCRIPT), "validate", str(self.root)], capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(json.loads(completed.stdout)["valid"])

    def test_cli_skill_only_does_not_require_generated_bundle_contract(self):
        self.api()
        for path in self.root.rglob("*"):
            if path.is_file() and path.name != "SKILL.md":
                path.unlink()
        (self.root / "SKILL.md").write_text("---\nname: csv-summary\ndescription: 总结项目数据。\n---\nCore workflow.\n", encoding="utf-8")
        completed = subprocess.run([sys.executable, str(SCRIPT), "validate", str(self.root), "--skill-only"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["valid"])


if __name__ == "__main__":
    unittest.main()
