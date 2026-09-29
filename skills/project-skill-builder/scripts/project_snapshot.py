#!/usr/bin/env python3
"""Hash an explicit, bounded selection of project files using only the standard library."""

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path, PureWindowsPath
from typing import Dict, List


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_DIRECTORIES = {".git", ".ssh", ".aws", ".azure", ".kube", ".gnupg", ".codex",
                          ".config", ".docker", "secrets", "credentials"}
_SENSITIVE_NAMES = {"credentials", "credentials.json", "credentials.yaml", "credentials.yml",
                    "secrets.json", "secrets.yaml", "secrets.yml", "password", "passwords",
                    "password.txt", "passwords.txt", "token.txt", "tokens.json", "id_rsa",
                    "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", "authorized_keys",
                    ".npmrc", ".pypirc", ".git-credentials"}


def _relative_path(value: str) -> str:
    """Reject drive-qualified, absolute, alternate-stream, and traversing paths."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("File paths must be nonempty relative strings")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if PureWindowsPath(value).drive or normalized.startswith("/") or ":" in normalized:
        raise ValueError("Absolute and drive-qualified paths are not allowed")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Empty components and path traversal are not allowed")
    return "/".join(parts)


def _is_link(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _reject_links(path: Path) -> None:
    """Also reject Windows junctions and links in a root's existing ancestors."""
    for component in [path] + list(path.parents):
        if _is_link(component):
            raise ValueError("Symbolic links and reparse points are not allowed")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sensitive_path(relative: str) -> bool:
    components = relative.lower().split("/")
    name = components[-1]
    return (any(component in _SENSITIVE_DIRECTORIES for component in components)
            or name in _SENSITIVE_NAMES or name == ".env" or name.startswith(".env.")
            or name.endswith((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")))


def snapshot_project(root: Path, files: List[str]) -> dict:
    """Return relative SHA-256 hashes; never enumerate the whole project.

    Sensitive path names are a conservative denylist, not a content security audit.
    The caller must select ordinary project documents and source files explicitly.
    """
    project = Path(root).absolute()
    _reject_links(project)
    if not project.is_dir():
        raise ValueError("Project root must be an existing directory")
    project = project.resolve()
    if any(component.name.lower() in _SENSITIVE_DIRECTORIES for component in [project] + list(project.parents)):
        raise ValueError("Project root must not lie inside a personal credential directory")
    hashes = {}
    for supplied in files:
        relative = _relative_path(supplied)
        if _sensitive_path(relative):
            raise ValueError("Sensitive configuration or credential paths are not allowed")
        target = project / relative
        _reject_links(target)
        resolved = target.resolve()
        if project not in resolved.parents:
            raise ValueError("Selected file escapes the project root")
        if not target.exists():
            raise FileNotFoundError("Selected project file is missing: " + relative)
        if not target.is_file() or not stat.S_ISREG(target.stat().st_mode):
            raise ValueError("Selected project paths must be regular files")
        hashes[relative] = _sha256(target)
    return {"schema_version": 1, "files": dict(sorted(hashes.items()))}


def _snapshot_files(snapshot: dict) -> Dict[str, str]:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ValueError("Unsupported project snapshot schema")
    files = snapshot.get("files")
    if not isinstance(files, dict):
        raise ValueError("Snapshot files must be an object")
    normalized = {}
    for relative, digest in files.items():
        name = _relative_path(relative)
        if name != relative or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ValueError("Invalid snapshot file path or SHA-256 digest")
        normalized[name] = digest
    return normalized


def compare_snapshots(before: dict, after: dict) -> dict:
    old, new = _snapshot_files(before), _snapshot_files(after)
    return {"added": sorted(set(new) - set(old)),
            "changed": sorted(name for name in set(old) & set(new) if old[name] != new[name]),
            "removed": sorted(set(old) - set(new))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="Hash only explicitly selected project files")
    snapshot.add_argument("--root", type=Path, required=True)
    snapshot.add_argument("--files", nargs="+", required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare", help="Compare two snapshot JSON files")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "snapshot":
            result = snapshot_project(arguments.root, arguments.files)
            _reject_links(arguments.output.absolute())
            arguments.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            before = json.loads(arguments.before.read_text(encoding="utf-8"))
            after = json.loads(arguments.after.read_text(encoding="utf-8"))
            result = compare_snapshots(before, after)
    except (OSError, ValueError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
