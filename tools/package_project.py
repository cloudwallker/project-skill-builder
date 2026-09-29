"""Assemble a reviewable release using an explicit product file allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile


ROOT_FILES = {'README.md', 'README_ZH.md', 'LICENSE', '.gitignore'}
DOC_FILES = {'docs/usage.md', 'docs/validation.md', 'docs/images/workflow.svg'}
TREE_RULES = {
    'skills/project-skill-builder': {
        'SKILL.md', 'agents/openai.yaml',
        'references/discovery.md', 'references/bundle-contract.md',
        'references/refresh.md', 'references/evaluation.md',
        'assets/brief.json', 'assets/candidates.json', 'assets/manifest.json', 'assets/evals.json',
        'scripts/validate_bundle.py', 'scripts/project_snapshot.py', 'scripts/refresh_bundle.py',
    },
    'tests': {'__init__.py', 'test_distribution.py', 'test_validate_bundle.py',
              'test_project_snapshot.py', 'test_refresh_bundle.py'},
    'examples/csv-tool': {'README.md', 'summary.py', 'sample.csv', 'tests/test_summary.py'},
    'examples/candidates': {'pandas-summary/SKILL.md', 'stdlib-summary/SKILL.md', 'skip-bad-rows/SKILL.md'},
}
SENSITIVE_NAMES = {'credentials', 'credentials.json', 'credentials.yaml', 'credentials.yml',
                   'auth.json', 'secrets.json', 'secrets.yaml', 'secrets.yml',
                   'id_rsa', 'id_ed25519', '.netrc', '.npmrc', '.pypirc'}
EXTRA_FILES = {'evals/grade_csv.py', 'evals/scenarios.json', 'evals/results.json',
               'tools/install.py', 'tools/package_project.py'}


def linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def collect_files(root):
    root = Path(root).absolute()
    if not root.is_dir() or any(linked(x) for x in (root, *root.parents)):
        raise ValueError('An unlinked source directory is required')
    found = set()
    for name in ROOT_FILES | DOC_FILES | EXTRA_FILES:
        path = root / name
        if path.exists() or path.is_symlink():
            if any(linked(x) for x in (path, *path.parents) if x != root.parent):
                raise ValueError('Linked publication path')
            if path.is_file():
                found.add(name)
    for directory, allowed_names in TREE_RULES.items():
        start = root / directory
        if not start.exists():
            continue
        if any(linked(x) for x in (start, *start.parents)):
            raise ValueError('Linked publication tree ancestor')
        for path in (start, *start.rglob('*')):
            if linked(path):
                raise ValueError('Linked publication path')
            name = path.relative_to(root).as_posix()
            parts = path.relative_to(start).parts
            if any(x in {'__pycache__', '.pytest_cache'} for x in parts):
                continue
            filename = path.name.lower()
            if filename in SENSITIVE_NAMES or filename == '.env' or filename.startswith('.env.') or path.suffix.lower() in {'.key', '.pem', '.p12', '.pfx'}:
                raise ValueError('Sensitive filename in product tree')
            if path.is_file() and path.relative_to(start).as_posix() in allowed_names:
                found.add(name)
    return sorted(found)


def package_project(root, destination, archive):
    root, destination, archive = map(lambda p: Path(p).absolute(), (root, destination, archive))
    files = collect_files(root)
    if not files:
        raise ValueError('No product files found')
    if destination.exists() or archive.exists() or destination.is_symlink() or archive.is_symlink():
        raise ValueError('Release output already exists')
    if destination == root or any(destination == root / prefix or root / prefix in destination.parents for prefix in TREE_RULES):
        raise ValueError('Release destination overlaps product source')
    for path in (destination.parent, archive.parent):
        if any(x.exists() and linked(x) for x in (path, *path.parents)):
            raise ValueError('Linked release output')
    destination.mkdir(parents=True)
    hashes = {}
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as output:
        for name in files:
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            output.writestr(entry, (destination / name).read_bytes())
    return {'files': files, 'sha256': hashes,
            'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--destination', required=True)
    parser.add_argument('--archive', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(package_project(args.root, args.destination, args.archive), indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({'error': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
