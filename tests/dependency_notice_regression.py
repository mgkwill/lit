"""Notice collection must reject unpinned or changed supplemental text."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hashlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('notices', ROOT / 'tools/release_dependency_notices.py')
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


class NoticeIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(notices, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.package = self.root / 'package'
        self.package.mkdir()
        (self.package / '.cargo_vcs_info.json').write_text(json.dumps(
            {'git': {'sha1': 'a' * 40}, 'path_in_vcs': 'crates/example'}))
        self.license = self.root / 'docs/licenses/LICENSE'
        self.license.parent.mkdir(parents=True)
        self.license.write_text('Example permission notice\n')
        self.entry = {'name': 'example', 'version': '1.0.0', 'revision': 'a' * 40,
                      'path_in_vcs': 'crates/example', 'notices': [{
                          'path': 'docs/licenses/LICENSE',
                          'url': 'https://example.invalid/pinned/LICENSE',
                          'sha256': hashlib.sha256(self.license.read_bytes()).hexdigest()}]}
        self.metadata = {'packages': [{'name': 'example', 'version': '1.0.0',
            'license': 'MIT', 'repository': 'https://example.invalid/repo',
            'source': 'registry+example', 'manifest_path': str(self.package / 'Cargo.toml')}]}

    def render(self):
        (self.root / 'docs/licenses/supplements.json').write_text(
            json.dumps({'packages': [self.entry]}))
        return notices.render(self.metadata)

    def test_pinned_notice_is_preserved(self):
        outputs = self.render()
        self.assertIn(self.license.read_text(), outputs[self.root / 'THIRD_PARTY_NOTICES.txt'])
        self.assertEqual(json.loads(outputs[self.root / 'docs/dependencies.json'])['missing_notices'], [])

    def test_changed_notice_is_rejected(self):
        self.license.write_text('Changed permission\n')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            self.render()

    def test_wrong_revision_is_rejected(self):
        self.entry['revision'] = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'revision does not match'):
            self.render()

    def test_outdated_package_is_rejected(self):
        self.entry['version'] = '0.9.0'
        with self.assertRaisesRegex(ValueError, 'absent from the lockfile'):
            self.render()

    def test_outside_notice_is_rejected(self):
        self.entry['notices'][0]['path'] = 'outside/LICENSE'
        with self.assertRaises(ValueError):
            self.render()


if __name__ == '__main__':
    unittest.main()
