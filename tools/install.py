"""Install this skill into an existing project's Codex skill directory."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile


def reject_links(path):
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and bool(getattr(item.lstat(), 'st_file_attributes', 0) & 0x400)):
            raise ValueError('Linked directories are not supported')


def install_skill(source, project):
    source, project = Path(source).absolute(), Path(project).absolute()
    if not project.is_dir() or not (source / 'SKILL.md').is_file():
        raise ValueError('An existing project and a skill source with SKILL.md are required')
    reject_links(source)
    reject_links(project)
    for item in source.rglob('*'):
        reject_links(item)
    parent = project / '.agents/skills'
    reject_links(parent)
    target = parent / 'project-skill-builder'
    source_root, target_root = source.resolve(), target.resolve()
    if source_root == target_root or source_root in target_root.parents or target_root in source_root.parents:
        raise ValueError('Skill source and install target must not overlap')
    if target.exists() or target.is_symlink():
        raise ValueError('Skill already exists; review and refresh it explicitly')
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.builder-install-', dir=str(parent)))
    try:
        shutil.copytree(source, staging / 'skill', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (staging / 'skill').rename(target)
    finally:
        # staging is the exact temporary directory created above, never a user target.
        shutil.rmtree(staging)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--source', default=str(Path(__file__).resolve().parents[1] / 'skills/project-skill-builder'))
    args = parser.parse_args()
    try:
        target = install_skill(args.source, args.project)
        print(json.dumps({'status': 'installed', 'path': str(target)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
