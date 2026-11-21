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
        self.assertIn(self.license.read_bytes().decode('utf-8'),
                      outputs[self.root / 'THIRD_PARTY_NOTICES.txt'])
        self.assertEqual(json.loads(outputs[self.root / 'docs/dependencies.json'])['missing_notices'], [])

    def test_crlf_notice_bytes_are_preserved(self):
        self.license.write_bytes(b'Example permission notice\r\nSecond line\r\n')
        self.entry['notices'][0]['sha256'] = hashlib.sha256(self.license.read_bytes()).hexdigest()
        output = self.render()[self.root / 'THIRD_PARTY_NOTICES.txt'].encode('utf-8')
        self.assertIn(self.license.read_bytes(), output)

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

    def test_declared_license_selection_and_origin(self):
        self.metadata['packages'][0]['license'] = 'MIT OR Apache-2.0'
        self.entry['selected_license'] = 'Apache-2.0'
        self.entry['notices'][0]['origin'] = 'official license text'
        record = json.loads(self.render()[self.root / 'docs/dependencies.json'])['packages'][0]
        self.assertEqual(record['selected_license'], 'Apache-2.0')
        self.assertEqual(record['notices'][0]['origin'], 'official license text')

    def test_undeclared_license_is_rejected(self):
        self.entry['selected_license'] = 'Apache-2.0'
        with self.assertRaisesRegex(ValueError, 'declared alternative'):
            self.render()

    def test_excerpt_must_match_pinned_source(self):
        source = self.package / 'example.c'
        source.write_bytes(self.license.read_bytes() + b'int example;\n')
        n = self.entry['notices'][0]
        n.update(source_path='example.c', source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                 source_start_line=1, source_end_line=1)
        self.render()
        n['source_end_line'] = 2
        with self.assertRaisesRegex(ValueError, 'excerpt mismatch'):
            self.render()
        n['source_end_line'] = 1
        source.write_bytes(source.read_bytes() + b'int added;\n')
        with self.assertRaisesRegex(ValueError, 'source digest mismatch'):
            self.render()


if __name__ == '__main__':
    unittest.main()
