import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_install_project_layout_and_collision_preserves_edits(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'SKILL.md').write_text('skill', encoding='utf-8')
        (source / 'agents').mkdir()
        (source / 'agents/openai.yaml').write_text('interface: {}', encoding='utf-8')
        (source / '__pycache__').mkdir()
        (source / '__pycache__/ignore.pyc').write_bytes(b'cache')
        project = self.root / 'project'
        project.mkdir()
        api = load('install')
        installed = api.install_skill(source, project)
        self.assertEqual(installed, project / '.agents/skills/project-skill-builder')
        self.assertTrue((installed / 'agents/openai.yaml').is_file())
        self.assertFalse((installed / '__pycache__').exists())
        (installed / 'SKILL.md').write_text('user edit', encoding='utf-8')
        with self.assertRaises(ValueError):
            api.install_skill(source, project)
        self.assertEqual((installed / 'SKILL.md').read_text(encoding='utf-8'), 'user edit')

    def test_install_requires_existing_project(self):
        with self.assertRaises(ValueError):
            load('install').install_skill(self.root, self.root / 'missing')

    def test_install_cannot_copy_source_into_itself(self):
        (self.root / 'SKILL.md').write_text('skill', encoding='utf-8')
        with self.assertRaises(ValueError):
            load('install').install_skill(self.root, self.root)
        self.assertFalse((self.root / '.agents').exists())

    def test_package_excludes_work_plans_and_caches(self):
        for path, data in {'README.md': b'public', 'LICENSE': b'license',
                           'skills/project-skill-builder/SKILL.md': b'skill',
                           'skills/project-skill-builder/__pycache__/cache.pyc': b'cache',
                           '.work/private.json': b'private',
                           'docs/superpowers/plans/plan.md': b'process'}.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        api = load('package_project')
        release = self.root / '.work/release'
        archive = self.root / '.work/project.zip'
        result = api.package_project(self.root, release, archive)
        self.assertEqual(result['files'], ['LICENSE', 'README.md', 'skills/project-skill-builder/SKILL.md'])
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(sorted(z.namelist()), result['files'])
            for name in z.namelist():
                self.assertEqual(z.read(name), (release / name).read_bytes())
        with self.assertRaises(ValueError):
            api.package_project(self.root, release, archive)

    def test_secret_filename_in_skill_fails_closed(self):
        secret = self.root / 'skills/project-skill-builder/.env'
        secret.parent.mkdir(parents=True)
        secret.write_text('EXAMPLE_PLACEHOLDER', encoding='utf-8')
        with self.assertRaises(ValueError):
            load('package_project').collect_files(self.root)

    def test_nested_work_and_process_files_are_not_product_files(self):
        base = self.root / 'skills/project-skill-builder'
        for relative in ['SKILL.md', '.work/private.json', '.git/session.json',
                         'references/transcript.md', 'assets/unknown-private.json']:
            target = base / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('PLACEHOLDER', encoding='utf-8')
        self.assertEqual(load('package_project').collect_files(self.root), ['skills/project-skill-builder/SKILL.md'])

    def test_credential_filename_is_refused_even_inside_a_product_tree(self):
        target = self.root / 'skills/project-skill-builder/references/credentials.json'
        target.parent.mkdir(parents=True)
        target.write_text('PLACEHOLDER', encoding='utf-8')
        with self.assertRaises(ValueError):
            load('package_project').collect_files(self.root)

    def test_symlink_included_file_is_rejected(self):
        source = self.root / 'README.md'
        outside = self.root / 'other.md'
        outside.write_text('outside', encoding='utf-8')
        try:
            source.symlink_to(outside)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaises(ValueError):
            load('package_project').collect_files(self.root)

    def test_linked_parent_of_product_tree_is_rejected(self):
        other = self.root / 'outside'
        (other / 'project-skill-builder').mkdir(parents=True)
        (other / 'project-skill-builder/SKILL.md').write_text('outside', encoding='utf-8')
        try:
            (self.root / 'skills').symlink_to(other, target_is_directory=True)
        except OSError:
            self.skipTest('Directory symlink creation unavailable')
        with self.assertRaises(ValueError):
            load('package_project').collect_files(self.root)


if __name__ == '__main__':
    unittest.main()
