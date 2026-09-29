#!/usr/bin/env python3
"""Preview or apply a three-way refresh while preserving human changes."""

import argparse
import copy
import json
import ntpath
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from project_snapshot import _is_link, _reject_links, _relative_path, _sha256


_MANIFEST = "bundle-manifest.json"
_OVERRIDES = "references/user-overrides.md"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _path_key(relative: str, root: Path) -> str:
    """Match Windows destination identities, keeping report paths in their original spelling.

    resolve() also expands existing directory/file aliases (including short names).
    Case-sensitive platforms retain distinct names. Windows bundles deliberately
    reject ambiguous aliases even on an NTFS directory configured as case-sensitive.
    """
    if os.name != "nt":
        return relative
    resolved = (root / relative).resolve()
    # ntpath's casing matches Windows Path comparisons. Python casefold expands
    # distinct filesystem names such as Straße/strasse and is too broad here.
    return ntpath.normcase(resolved.relative_to(root).as_posix())


def _indexed_paths(paths, root: Path) -> dict:
    indexed = {}
    for relative in paths:
        identity = _path_key(relative, root)
        if identity in indexed:
            raise ValueError("Bundle contains multiple aliases of the same filesystem path")
        indexed[identity] = relative
    return indexed


def _inventory(root: Path):
    """Do not follow links, junctions, sockets, or devices when copying a bundle."""
    files, directories = {}, set()
    for parent, directory_names, file_names in os.walk(str(root), followlinks=False):
        parent_path = Path(parent)
        for name in directory_names + file_names:
            path = parent_path / name
            if _is_link(path):
                raise ValueError("Bundle contains a symbolic link or reparse point")
            relative = path.relative_to(root).as_posix()
            _relative_path(relative)
            metadata = path.stat()
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(relative)
            elif stat.S_ISREG(metadata.st_mode):
                files[relative] = path
            else:
                raise ValueError("Bundle contains a non-regular file")
    _indexed_paths(set(files) | directories, root)
    return files, directories


def _manifest(root: Path) -> dict:
    value = json.loads((root / _MANIFEST).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Current and proposed bundles require manifest schema 1")
    if not isinstance(value.get("name"), str) or not value["name"]:
        raise ValueError("Bundle manifest requires a name")
    managed = value.get("managed_files")
    if not isinstance(managed, dict):
        raise ValueError("Bundle managed_files must be an object")
    seen = set()
    reserved = {_path_key(name, root) for name in (_MANIFEST, _OVERRIDES)}
    for relative, digest in managed.items():
        normalized = _relative_path(relative)
        key = _path_key(normalized, root)
        if normalized != relative or key in seen or key in reserved:
            raise ValueError("Invalid, duplicate, or reserved managed file path")
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise ValueError("Managed file baseline requires SHA-256 digests")
        seen.add(key)
    return value


def _state(files: dict, directories: set) -> dict:
    return {"files": {name: _sha256(path) for name, path in files.items()}, "directories": sorted(directories)}


def _plan(current_root, current_files, current_directories, proposed_files, old, new):
    changed, preserved, conflicts = set(), set(), []
    updates, removals = {}, set()
    old_hashes, new_hashes = old["managed_files"], new["managed_files"]
    old_names = _indexed_paths(old_hashes, current_root)
    new_names = _indexed_paths(new_hashes, current_root)
    local_names = _indexed_paths(current_files, current_root)
    local_directory_names = _indexed_paths(current_directories, current_root)
    proposal_names = _indexed_paths(proposed_files, current_root)
    reserved = {_path_key(name, current_root) for name in (_MANIFEST, _OVERRIDES)}
    for identity in sorted(set(old_names) | set(new_names)):
        old_name, new_name = old_names.get(identity), new_names.get(identity)
        relative = new_name if new_name is not None else old_name
        baseline = old_hashes.get(old_name)
        proposed_hash = new_hashes.get(new_name)
        local_name = local_names.get(identity)
        local = (_sha256(current_files[local_name]) if local_name is not None else
                 "<directory>" if identity in local_directory_names else None)
        if baseline is None:
            if local is not None:
                conflicts.append({"path": relative, "reason": "Proposed managed file collides with user-created content"})
            else:
                updates[relative] = proposed_files[proposal_names[identity]]
                changed.add(relative)
        elif local != baseline:
            if proposed_hash != baseline and local != proposed_hash:
                reason = "Locally deleted managed file also changed in proposed version" if local is None else "Managed file changed both locally and in proposed version"
                conflicts.append({"path": relative, "reason": reason})
            elif local == proposed_hash and proposed_hash != baseline:
                changed.add(relative)
            else:
                preserved.add(local_name if local_name is not None else relative)
        elif proposed_hash != baseline:
            changed.add(relative)
            if proposed_hash is None:
                removals.add(local_name)
            else:
                updates[relative] = proposed_files[proposal_names[identity]]

    for relative, source in proposed_files.items():
        identity = _path_key(relative, current_root)
        if identity in reserved or identity in new_names:
            continue
        if identity in local_names or identity in local_directory_names:
            # An untracked proposal never takes ownership of existing local content.
            if identity not in old_names or local_names.get(identity) in removals:
                conflicts.append({"path": relative, "reason": "Proposed file collides with existing unmanaged content"})
        else:
            updates[relative] = source
            changed.add(relative)

    for relative in current_files:
        identity = _path_key(relative, current_root)
        if identity == _path_key(_OVERRIDES, current_root) or identity not in old_names and identity not in reserved:
            preserved.add(relative)
    # Preserve absent overrides too: a refresh never recreates a user-owned file silently.
    preserved.add(_OVERRIDES)

    for relative in updates:
        identity = _path_key(relative, current_root)
        if identity in local_directory_names:
            conflicts.append({"path": relative, "reason": "Proposed file collides with an existing directory"})
        for parent in Path(relative).parents:
            parent_name = parent.as_posix()
            parent_identity = _path_key(parent_name, current_root)
            if parent_name != "." and parent_identity in local_names and local_names[parent_identity] not in removals:
                conflicts.append({"path": relative, "reason": "Proposed file parent collides with an existing file"})
    if old != new:
        changed.add(_MANIFEST)
    unique = {(item["path"], item["reason"]): item for item in conflicts}
    return sorted(changed), sorted(preserved), list(unique.values()), updates, removals


def _version_directory(history_root: Path, name: str) -> Path:
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12]
    target = history_root / name / version
    _reject_links(target)
    target.mkdir(parents=True, exist_ok=False)
    return target


def _cleanup_stage(stage: Optional[Path], parent: Path) -> None:
    if stage is None or not stage.exists():
        return
    _reject_links(stage)
    resolved = stage.resolve()
    if resolved.parent != parent.resolve() or not stage.name.startswith(".project-skill-builder-stage-"):
        raise ValueError("Refusing to remove an unexpected staging directory")
    # The staging directory is freshly allocated by this invocation and lies beside current.
    shutil.rmtree(str(stage))


def refresh_bundle(current: Path, proposed: Path, apply: bool = False,
                   history_root: Optional[Path] = None) -> dict:
    """Preview by default; update only a validated candidate without unresolved conflicts.

    The manifest's hashes are the previous generation baseline, not a hash of human
    additions. Human edits retained from an unchanged upstream file retain that baseline.
    """
    result = {"status": "invalid", "changed": [], "preserved": [], "conflicts": [],
              "backup": None, "candidate": None, "errors": [], "warnings": []}
    stage = None
    moved_current = False
    current_root = Path(current).absolute()
    proposed_root = Path(proposed).absolute()
    backup = None
    try:
        for root in (current_root, proposed_root):
            _reject_links(root)
            if not root.is_dir():
                raise ValueError("Current and proposed roots must be existing directories")
        current_root, proposed_root = current_root.resolve(), proposed_root.resolve()
        if _overlap(current_root, proposed_root):
            raise ValueError("Current and proposed roots must not overlap")
        history = Path(history_root).absolute() if history_root is not None else current_root.parent / ".project-skill-builder-history"
        _reject_links(history)
        history = history.resolve()
        if _overlap(history, current_root) or _overlap(history, proposed_root):
            raise ValueError("History must be outside current and proposed bundle trees")
        current_files, current_directories = _inventory(current_root)
        proposed_files, proposed_directories = _inventory(proposed_root)
        old, new = _manifest(current_root), _manifest(proposed_root)
        if old["name"] != new["name"]:
            raise ValueError("Refresh requires matching bundle names")
        from validate_bundle import validate_bundle
        proposed_validation = validate_bundle(proposed_root, check_hashes=True)
        if not proposed_validation["valid"] or proposed_validation["modified_files"]:
            result["errors"] = proposed_validation["errors"] + [
                "Proposed managed files do not match their sealed baseline: " + name
                for name in proposed_validation["modified_files"]
            ]
            result["warnings"] = proposed_validation["warnings"]
            return result
        changed, preserved, conflicts, updates, removals = _plan(
            current_root, current_files, current_directories, proposed_files, old, new)
        result.update(changed=changed, preserved=preserved, conflicts=conflicts)
        current_state, proposed_state = _state(current_files, current_directories), _state(proposed_files, proposed_directories)
        if conflicts:
            result["status"] = "conflict"
            if apply:
                candidate = _version_directory(history, current_root.name) / "candidate"
                shutil.copytree(str(proposed_root), str(candidate))
                checked = validate_bundle(candidate, check_hashes=True)
                if not checked["valid"] or checked["modified_files"]:
                    raise ValueError("Copied conflict candidate failed validation")
                result["candidate"] = str(candidate)
            return result
        # Validate current only after identifying deletions as three-way conflicts.
        current_validation = validate_bundle(current_root, check_hashes=False)
        if not current_validation["valid"]:
            result["errors"] = current_validation["errors"]
            return result
        if not apply:
            result["status"] = "preview"
            return result
        if not changed:
            result["status"] = "unchanged"
            return result

        stage = Path(tempfile.mkdtemp(prefix=".project-skill-builder-stage-", dir=str(current_root.parent)))
        shutil.copytree(str(current_root), str(stage), dirs_exist_ok=True)
        for relative in removals:
            (stage / relative).unlink()
        for relative, source in updates.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(target))
        staged_manifest = copy.deepcopy(new)
        # new hashes already equal the old baseline for preserved local edits.
        (stage / _MANIFEST).write_text(json.dumps(staged_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staged_validation = validate_bundle(stage, check_hashes=True)
        old_baselines = {_path_key(name, current_root): digest for name, digest in old["managed_files"].items()}
        new_baselines = {_path_key(name, current_root): digest for name, digest in new["managed_files"].items()}
        preserved_identities = {_path_key(name, current_root) for name in preserved}
        unexpected_changes = []
        for name in staged_validation["modified_files"]:
            identity = _path_key(name, current_root)
            if (identity not in preserved_identities or identity not in old_baselines
                    or old_baselines[identity] != new_baselines.get(identity)):
                unexpected_changes.append(name)
        result["warnings"] = staged_validation["warnings"]
        if not staged_validation["valid"] or unexpected_changes:
            result["errors"] = staged_validation["errors"] + [
                "Staged managed file differs from the proposed baseline: " + name for name in unexpected_changes
            ]
            return result
        fresh_current = _inventory(current_root)
        fresh_proposed = _inventory(proposed_root)
        if _state(*fresh_current) != current_state or _state(*fresh_proposed) != proposed_state:
            raise ValueError("Bundle changed while refresh was being prepared; retry from a fresh preview")
        backup = _version_directory(history, current_root.name) / "backup"
        os.replace(str(current_root), str(backup))
        moved_current = True
        os.replace(str(stage), str(current_root))
        stage = None
        moved_current = False
        result.update(status="applied", backup=str(backup))
        return result
    except (OSError, ValueError, ImportError) as error:
        result["status"] = "failed" if stage is not None or moved_current else "invalid"
        result["errors"].append(str(error))
        if moved_current and backup is not None:
            try:
                os.replace(str(backup), str(current_root))
                moved_current = False
            except OSError as restore_error:
                result["backup"] = str(backup)
                result["errors"].append("Automatic restore failed; preserved backup: " + str(restore_error))
        return result
    finally:
        if stage is not None:
            try:
                _cleanup_stage(stage, current_root.parent)
            except (OSError, ValueError):
                # An unexpected path is retained rather than recursively removed.
                result["warnings"].append("Staging cleanup did not finish; inspect the sibling staging directory")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("current_path", nargs="?", type=Path, help="Existing bundle directory")
    parser.add_argument("proposed_path", nargs="?", type=Path, help="Sealed proposed bundle directory")
    parser.add_argument("--current", type=Path, help="Alternative to positional CURRENT_PATH")
    parser.add_argument("--proposed", type=Path, help="Alternative to positional PROPOSED_PATH")
    parser.add_argument("--apply", action="store_true", help="Apply safe updates; default is read-only preview")
    parser.add_argument("--history-root", type=Path, help="External history directory; must not overlap either bundle")
    arguments = parser.parse_args(argv)
    if arguments.current is not None and arguments.current_path is not None:
        parser.error("Specify current once, using a positional path or --current")
    if arguments.proposed is not None and arguments.proposed_path is not None:
        parser.error("Specify proposed once, using a positional path or --proposed")
    current = arguments.current or arguments.current_path
    proposed = arguments.proposed or arguments.proposed_path
    if current is None or proposed is None:
        parser.error("Both current and proposed bundle paths are required")
    result = refresh_bundle(current, proposed, apply=arguments.apply, history_root=arguments.history_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"preview", "applied", "unchanged"} else 1


if __name__ == "__main__":
    sys.exit(main())
