#!/usr/bin/env python3
"""Validate and seal self-contained project skill bundles using Python 3.9+.

Frontmatter support is deliberately a YAML subset: required name/description
strings may be plain, JSON-compatible double-quoted, YAML single-quoted, or
indented literal/folded blocks (|, >, and their +/- variants). This is not a
general YAML parser. Other frontmatter metadata is left to the host validator.

Local references support Markdown links, reference-style link definitions,
and inline code resource paths rooted in agents/assets/evals/references/scripts.
HTTP(S), mailto links and fragment-only links are not local resources. Local
paths must use forward slashes, remain relative, and contain no '..' component.

Source, license and behavior checks validate records and evidence structure;
they do not prove a URL was read, a license legally applies, or a run occurred.
"""

import argparse
from datetime import datetime
import hashlib
import json
import ntpath
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from urllib.parse import unquote, urlsplit


MANIFEST = "bundle-manifest.json"
OVERRIDES = "references/user-overrides.md"
REQUIRED_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/project-context.md",
    "references/decisions.md",
    "references/sources.md",
    OVERRIDES,
    "evals/cases.json",
)
STAGES = {
    "requirements": {"complete", "partial", "not_run", "failed"},
    "discovery": {"complete", "partial", "not_run", "failed"},
    "reading": {"complete", "partial", "not_run", "failed"},
    "license": {"complete", "partial", "not_run", "failed"},
    "static": {"passed", "failed", "not_run"},
    "behavior": {"passed", "failed", "not_run"},
}
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-fA-F]{40}\Z")
_RESOURCE = re.compile(r"(?<![\w./\\])(?:agents|assets|evals|references|scripts)/[^\s`<>\"']+")


def _result():
    return {"valid": False, "errors": [], "warnings": [], "modified_files": []}


def _finish(result):
    for key in ("errors", "warnings", "modified_files"):
        result[key] = sorted(set(result[key]))
    result["valid"] = not result["errors"]
    return result


def _is_link(path):
    try:
        details = path.lstat()
        return stat.S_ISLNK(details.st_mode) or bool(getattr(details, "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def _path_error(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        return "must be a nonempty relative path string"
    if "\\" in value or ntpath.isabs(value) or ntpath.splitdrive(value)[0] or value.startswith("/"):
        return "must be relative and use forward slashes"
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "must contain no empty, '.' or '..' components"
    if any(":" in part for part in parts):
        return "must contain no colon or filesystem stream"
    return None


def _path_key(relative, root):
    """Use destination identities without conflating distinct Unicode names.

    Windows resolution expands existing aliases, including 8.3 short names;
    normcase then uses Windows path casing without casefold's character
    expansions. POSIX paths retain their original case and spelling.
    """
    if os.name != "nt":
        return relative
    canonical_root = root.resolve()
    resolved = (canonical_root / relative).resolve()
    return ntpath.normcase(resolved.relative_to(canonical_root).as_posix())


def _safe_local(root, relative, result, context, require_file=False):
    problem = _path_error(relative)
    if problem:
        result["errors"].append("Invalid path for {}: {} ({})".format(context, relative, problem))
        return None
    target = root.joinpath(*PurePosixPath(relative).parts)
    cursor = root
    for part in PurePosixPath(relative).parts:
        cursor = cursor / part
        if _is_link(cursor):
            result["errors"].append("Unsafe link/reparse path for {}: {}".format(context, relative))
            return None
    try:
        target.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        result["errors"].append("Escaping path for {}: {}".format(context, relative))
        return None
    if require_file and not target.is_file():
        result["errors"].append("Missing local reference from {}: {}".format(context, relative))
        return None
    return target


def _collect_files(root, result):
    files = []
    if not root.is_dir():
        result["errors"].append("Bundle root is not a directory")
        return files
    # Reject links in root ancestors too; resolving a linked root hides escape.
    for ancestor in [root] + list(root.parents):
        if _is_link(ancestor):
            result["errors"].append("Unsafe link/reparse bundle root")
            return files
    try:
        for current, dirs, names in os.walk(str(root), followlinks=False):
            parent = Path(current)
            for directory in list(dirs):
                target = parent / directory
                if _is_link(target):
                    result["errors"].append("Unsafe link/reparse resource: " + target.relative_to(root).as_posix())
                    dirs.remove(directory)
                elif directory in (".git", "__pycache__"):
                    dirs.remove(directory)
            for name in names:
                target = parent / name
                relative = target.relative_to(root).as_posix()
                if _is_link(target):
                    result["errors"].append("Unsafe link/reparse resource: " + relative)
                elif target.is_file() and target.suffix != ".pyc":
                    problem = _path_error(relative)
                    if problem:
                        result["errors"].append("Invalid resource path: " + relative)
                    else:
                        files.append(relative)
                elif not target.is_file():
                    result["errors"].append("Resource is not a regular file: " + relative)
    except OSError as exc:
        result["errors"].append("Cannot inspect bundle files: " + str(exc))
    return sorted(files)


def _read_text(path, result, context):
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        result["errors"].append("Cannot read {} as UTF-8: {}".format(context, exc))
        return None


def _read_json(path, result, context):
    content = _read_text(path, result, context)
    if content is None:
        return None
    try:
        return json.loads(content)
    except (ValueError, TypeError) as exc:
        result["errors"].append("Invalid JSON in {}: {}".format(context, exc))
        return None


def _scalar(value):
    value = value.strip()
    if value.startswith(('"', "'")):
        match = re.fullmatch(r'''("(?:[^"\\]|\\.)*"|'(?:[^']|'')*')\s*(?:#.*)?''', value)
        if not match:
            raise ValueError("unsupported quoted string")
        quoted = match.group(1)
        return json.loads(quoted) if quoted[0] == '"' else quoted[1:-1].replace("''", "'")
    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    if not value or value[0] in "[{&*!" or ": " in value or value.lower() in ("null", "true", "false", "~"):
        raise ValueError("required field must use a supported string scalar")
    return value


def _frontmatter(text, result):
    if text is None:
        return {}
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        result["errors"].append("SKILL.md frontmatter must start with ---")
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        result["errors"].append("SKILL.md frontmatter has no closing ---")
        return {}
    values = {}
    seen = set()
    index = 1
    while index < end:
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#") or line[0].isspace():
            continue
        match = re.fullmatch(r"([A-Za-z_][\w-]*):\s*(.*)", line)
        if not match:
            result["errors"].append("Unsupported SKILL.md frontmatter syntax")
            continue
        key, raw = match.groups()
        if key in seen:
            result["errors"].append("Duplicate SKILL.md frontmatter field: " + key)
        seen.add(key)
        if key not in ("name", "description"):
            continue
        try:
            if raw.strip() in (">", "|", ">-", "|-", ">+", "|+"):
                blocks = []
                while index < end and (not lines[index].strip() or lines[index][0].isspace()):
                    blocks.append(lines[index].strip())
                    index += 1
                values[key] = (" " if raw.strip().startswith(">") else "\n").join(blocks).strip()
            else:
                values[key] = _scalar(raw)
        except (ValueError, TypeError):
            result["errors"].append("Unsupported SKILL.md frontmatter string for " + key)
    for key in ("name", "description"):
        if not isinstance(values.get(key), str) or not values[key].strip():
            result["errors"].append("SKILL.md frontmatter requires nonempty string: " + key)
    name = values.get("name", "")
    if name and (len(name) > 64 or not _SLUG.fullmatch(name)):
        result["errors"].append("SKILL.md frontmatter name must be a lowercase hyphenated slug (max 64 characters)")
    if len(values.get("description", "")) > 1024:
        result["errors"].append("SKILL.md frontmatter description exceeds 1024 characters")
    return values


def _link_target(raw):
    raw = raw.strip()
    if raw.startswith("<"):
        closing = raw.find(">")
        return raw[1:closing] if closing >= 0 else raw
    # Markdown titles are separated from a URL with whitespace.
    return raw.split()[0] if raw else ""


def _check_reference(root, raw, origin, result, bundle_relative=False):
    target = unquote(_link_target(raw))
    if not target or target.startswith("#"):
        return
    try:
        parsed = urlsplit(target)
    except ValueError:
        result["errors"].append("Invalid reference URL from {}: {}".format(origin, target))
        return
    if parsed.scheme in ("http", "https", "mailto"):
        return
    if parsed.scheme:
        result["errors"].append("Invalid local reference path from {}: {}".format(origin, target))
        return
    relative = parsed.path
    while relative.startswith("./"):
        relative = relative[2:]
    problem = _path_error(relative)
    if problem or parsed.netloc:
        result["errors"].append("Invalid local reference path from {}: {}".format(origin, target))
        return
    if not bundle_relative:
        parent = PurePosixPath(origin).parent
        relative = (parent / relative).as_posix()
    _safe_local(root, relative, result, origin, require_file=True)


def _check_references(root, files, result):
    for relative in files:
        if PurePosixPath(relative).suffix.lower() not in (".md", ".yaml", ".yml"):
            continue
        text = _read_text(root / relative, result, relative)
        if text is None:
            continue
        # Inline links and reference-style definitions use file-relative paths.
        for match in re.finditer(r"!?\[[^\]\n]*\]\(([^\n)]*)\)", text):
            _check_reference(root, match.group(1), relative, result)
        for match in re.finditer(r"^\s*\[[^\]\n]+\]:\s*(\S+)", text, re.MULTILINE):
            _check_reference(root, match.group(1), relative, result)
        # Resource paths inside commands are relative to the bundle root.
        for inline in re.findall(r"`([^`\n]+)`", text):
            for match in _RESOURCE.finditer(inline):
                if match.end() < len(inline) and inline[match.end()] in "<>":
                    continue
                _check_reference(root, match.group(0).rstrip(".,;:"), relative, result, bundle_relative=True)
        if relative == "agents/openai.yaml":
            for match in re.finditer(r"^\s*(?:icon_small|icon_large):\s*(.+)$", text, re.MULTILINE):
                try:
                    icon = _scalar(match.group(1))
                    _check_reference(root, icon, relative, result, bundle_relative=True)
                except (ValueError, TypeError):
                    result["errors"].append("Unsupported icon path string in agents/openai.yaml")


def _iso_time(value):
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        return parsed.tzinfo is not None
    except ValueError:
        return False


def _check_sources(manifest, stages, result):
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        result["errors"].append("Manifest sources must be a list")
        return
    seen = set()
    public_count = 0
    for index, source in enumerate(sources):
        label = "sources[{}]".format(index)
        if not isinstance(source, dict):
            result["errors"].append(label + " must be an object")
            continue
        for key in ("id", "kind", "url", "path", "license", "status", "reason", "version"):
            if key not in source:
                result["errors"].append(label + " requires " + key)
        identifier = source.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            result["errors"].append(label + " has a missing/duplicate id")
        elif isinstance(identifier, str):
            seen.add(identifier)
        kind = source.get("kind")
        if kind not in ("skill", "documentation", "fixture"):
            result["errors"].append(label + " has invalid kind")
        if kind == "fixture":
            if source.get("url") is not None:
                result["errors"].append(label + " fixture must have url:null")
        elif kind in ("skill", "documentation"):
            url = source.get("url")
            try:
                parsed = urlsplit(url) if isinstance(url, str) else None
                if parsed is None or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    result["errors"].append(label + " requires a public https URL without credentials")
                else:
                    public_count += 1
            except ValueError:
                result["errors"].append(label + " has invalid public URL")
        if _path_error(source.get("path")):
            result["errors"].append(label + " has invalid source path")
        status = source.get("status")
        if status not in ("adopted", "rejected", "reference"):
            result["errors"].append(label + " has invalid status")
        reason = source.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            result["errors"].append(label + " requires a reason")
        license_name = source.get("license")
        if license_name is not None and not isinstance(license_name, str):
            result["errors"].append(label + " license must be a string or null")
        unknown = not isinstance(license_name, str) or license_name.strip().lower() in ("", "unknown", "unverified", "unclear", "none", "noassertion", "unlicensed", "unspecified", "not found")
        if status == "adopted" and (unknown or stages.get("license") != "complete"):
            result["errors"].append(label + " adopted content requires a known license and complete license stage")
        version = source.get("version")
        if not isinstance(version, dict) or "commit" not in version or not _iso_time(version.get("read_at")):
            result["errors"].append(label + " version requires commit and timezone-aware ISO8601 read_at")
        else:
            commit = version.get("commit")
            if commit is not None and (not isinstance(commit, str) or not _COMMIT.fullmatch(commit)):
                result["errors"].append(label + " version.commit must be null or a 40-character commit hash")
            if commit is None:
                result["warnings"].append(label + " version is not pinned to a commit")
    if stages.get("discovery") == "complete" and not public_count:
        result["errors"].append("Complete discovery requires a non-fixture public source record")
    if stages.get("reading") == "complete" and not sources:
        result["errors"].append("Complete reading requires source records")


def _check_extension_paths(value, result, location="manifest"):
    # Validate extension path fields without inventing their semantics. Source
    # paths refer to original repositories and need not exist in the bundle.
    if isinstance(value, dict):
        for key, child in value.items():
            label = location + "." + str(key)
            if key == "managed_files":
                continue
            path_field = isinstance(key, str) and (key == "path" or key.endswith("_path") or key.endswith("_paths"))
            if path_field:
                paths = child if isinstance(child, list) else [child]
                for path in paths:
                    if _path_error(path):
                        result["errors"].append("Invalid manifest path in " + label)
            _check_extension_paths(child, result, label)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_extension_paths(child, result, location + "[{}]".format(index))


def _check_snapshot(root, manifest, result):
    snapshot = manifest.get("project_snapshot")
    if snapshot is None:
        return
    if isinstance(snapshot, str):
        target = _safe_local(root, snapshot, result, "project_snapshot", require_file=True)
        if target is None:
            return
        snapshot = _read_json(target, result, "project_snapshot")
    if not isinstance(snapshot, dict) or type(snapshot.get("schema_version")) is not int or snapshot.get("schema_version") != 1 or not isinstance(snapshot.get("files"), dict):
        result["errors"].append("project_snapshot requires schema_version:1 and a files object")
        return
    # These names describe project files; never resolve or read them in a bundle.
    for relative, digest in snapshot["files"].items():
        if _path_error(relative):
            result["errors"].append("Invalid project_snapshot path: " + str(relative))
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            result["errors"].append("Invalid project_snapshot sha256: " + str(relative))


def _check_behavior(root, manifest, stages, result):
    evidence = manifest.get("behavior_results")
    if evidence is None:
        if stages.get("behavior") == "passed":
            result["errors"].append("Passed behavior requires behavior_results pointing to evidence JSON")
        return
    target = _safe_local(root, evidence, result, "behavior_results", require_file=True)
    if target is None:
        return
    record = _read_json(target, result, "behavior_results")
    if not isinstance(record, dict) or type(record.get("schema_version")) is not int or record.get("schema_version") != 1:
        result["errors"].append("behavior_results requires schema_version:1 and a scenarios list")
        return
    scenarios = record.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        result["errors"].append("behavior_results requires nonempty scenarios")
        return
    seen = set()
    for index, scenario in enumerate(scenarios):
        label = "behavior_results.scenarios[{}]".format(index)
        if not isinstance(scenario, dict):
            result["errors"].append(label + " must be an object")
            continue
        identifier = scenario.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            result["errors"].append(label + " requires a unique id")
        else:
            seen.add(identifier)
        for side in ("baseline", "generated"):
            outcome = scenario.get(side)
            if not isinstance(outcome, dict) or outcome.get("status") not in ("passed", "failed"):
                result["errors"].append(label + "." + side + " requires status passed/failed")
            elif side == "generated" and stages.get("behavior") == "passed" and outcome["status"] != "passed":
                result["errors"].append(label + " generated failure conflicts with passed behavior")
    result["warnings"].append("Behavior evidence structure was checked; actual execution and semantic claims require independent review")


def _validate(root, check_hashes=True, skill_only=False, require_managed=True, manifest_override=None):
    result = _result()
    root = Path(root).absolute()
    files = _collect_files(root, result)
    if result["errors"]:
        return _finish(result)
    required = ("SKILL.md",) if skill_only else REQUIRED_FILES
    for relative in required:
        if relative not in files:
            result["errors"].append("Missing required file: " + relative)
    frontmatter = _frontmatter(_read_text(root / "SKILL.md", result, "SKILL.md") if "SKILL.md" in files else None, result)
    _check_references(root, files, result)
    if skill_only:
        return _finish(result)
    if MANIFEST not in files:
        result["errors"].append("Missing required file: " + MANIFEST)
        return _finish(result)
    manifest = manifest_override if manifest_override is not None else _read_json(root / MANIFEST, result, MANIFEST)
    if not isinstance(manifest, dict):
        result["errors"].append("Manifest JSON must be an object")
        return _finish(result)
    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1:
        result["errors"].append("Manifest schema_version must be 1")
    if manifest.get("name") != frontmatter.get("name") or not isinstance(manifest.get("name"), str):
        result["errors"].append("Manifest name must match SKILL.md frontmatter name")
    if not isinstance(manifest.get("version"), str) or not manifest["version"].strip():
        result["errors"].append("Manifest requires a nonempty version string")
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        result["errors"].append("Manifest stages must be an object")
        stages = {}
    for stage, allowed in STAGES.items():
        state = stages.get(stage)
        if not isinstance(state, str) or state not in allowed:
            result["errors"].append("Invalid stage status for " + stage)
    _check_sources(manifest, stages, result)
    _check_extension_paths(manifest, result)
    _check_snapshot(root, manifest, result)
    _check_behavior(root, manifest, stages, result)
    if "evals/cases.json" in files:
        _read_json(root / "evals/cases.json", result, "evals/cases.json")
    managed = manifest.get("managed_files")
    if not isinstance(managed, dict):
        result["errors"].append("Manifest managed_files must be an object of paths and sha256 values")
        managed = {}
    managed_identities = set()
    reserved_identities = {_path_key(name, root) for name in (MANIFEST, OVERRIDES)}
    for relative, digest in managed.items():
        target = _safe_local(root, relative, result, "managed_files")
        if target is not None:
            try:
                identity = _path_key(relative, root)
                if identity in managed_identities:
                    result["errors"].append("Path alias in managed_files: " + relative)
                managed_identities.add(identity)
                if identity in reserved_identities:
                    result["errors"].append("managed_files cannot include " + relative)
            except (OSError, ValueError) as exc:
                result["errors"].append("Cannot resolve managed file path {}: {}".format(relative, exc))
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            result["errors"].append("Invalid sha256 for managed file: " + str(relative))
            continue
        if target is None or not check_hashes:
            continue
        if not target.is_file():
            result["modified_files"].append(relative)
            result["warnings"].append("Managed file is missing: " + relative)
        else:
            try:
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual != digest:
                    result["modified_files"].append(relative)
                    result["warnings"].append("Managed file was modified: " + relative)
            except OSError as exc:
                result["errors"].append("Cannot hash {}: {}".format(relative, exc))
    if require_managed:
        for relative in REQUIRED_FILES:
            if relative != OVERRIDES and relative not in managed:
                result["errors"].append("Required file is not in managed_files: " + relative)
        evidence = manifest.get("behavior_results")
        if stages.get("behavior") == "passed" and isinstance(evidence, str) and evidence not in managed:
            result["errors"].append("behavior_results evidence must be in managed_files: " + evidence)
    return _finish(result)


def validate_bundle(root: Path, check_hashes: bool = True) -> dict:
    """Return structural errors and nonblocking evidence of manual modification."""
    return _validate(root, check_hashes=check_hashes)


def seal_bundle(root: Path) -> dict:
    """Seal existing source/stage metadata; invalid structure never gets written."""
    root = Path(root).absolute()
    result = _validate(root, check_hashes=False, require_managed=False)
    if not result["valid"]:
        return result
    manifest = _read_json(root / MANIFEST, result, MANIFEST)
    files = _collect_files(root, result)
    if result["errors"]:
        return _finish(result)
    try:
        manifest["managed_files"] = {
            relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in files if relative not in (MANIFEST, OVERRIDES)
        }
    except OSError as exc:
        result["errors"].append("Cannot seal file hashes: " + str(exc))
        return _finish(result)
    result = _validate(root, check_hashes=True, manifest_override=manifest)
    if not result["valid"]:
        return result
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", prefix=".bundle-manifest-", suffix=".tmp", dir=str(root), delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(str(temporary), str(root / MANIFEST))
    except OSError as exc:
        result["errors"].append("Cannot write sealed manifest: " + str(exc))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return _finish(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validation = commands.add_parser("validate", help="Check generated bundle or ordinary skill structure")
    validation.add_argument("bundle", type=Path)
    validation.add_argument("--skill-only", action="store_true", help="Check SKILL.md and local references without generated-bundle metadata")
    sealing = commands.add_parser("seal", help="Hash managed content into an existing manifest")
    sealing.add_argument("bundle", type=Path)
    args = parser.parse_args(argv)
    if args.command == "seal":
        result = seal_bundle(args.bundle)
    else:
        result = _validate(args.bundle, skill_only=args.skill_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
