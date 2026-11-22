"""Privacy projection preserves verifiable historical evidence identities."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'audits/evidence/tools'))
from history_projection import blob, commit, load, verify_binding
from redaction_policy import sanitize

POLICY = {'private_owner': 'example-owner',
          'host_map': {'node-private-1': 'host-a'},
          'network_map': {'198.51.100.90': '192.0.2.10'},
          'deployment_alias_map': {'example-deployment-7': 'model-under-test'}}


class RedactionTests(unittest.TestCase):
    def test_private_paths_and_labels(self):
        data = b'/home/example-owner/private/file node-private-1 example-deployment-7'
        self.assertEqual(sanitize(data, POLICY), b'<private-path> host-a model-under-test')

    def test_json_remains_valid(self):
        data = json.dumps({'path': '/Users/example-owner/artifact.json',
                           'host': 'node-private-1', 'address': '198.51.100.90'}).encode()
        clean = json.loads(sanitize(data, POLICY))
        self.assertEqual(clean, {'path': '<private-path>', 'host': 'host-a',
                                 'address': '192.0.2.10'})

    def test_windows_paths(self):
        value = json.dumps({'path': r'C:\Users\example-owner\private.txt'}).encode()
        self.assertEqual(json.loads(sanitize(value, POLICY)), {'path': '<private-path>'})

    def test_idempotent_and_public_protocols_preserved(self):
        original = b'127.0.0.1 localhost https://example.invalid node-private-1'
        clean = sanitize(original, POLICY)
        self.assertEqual(clean, b'127.0.0.1 localhost https://example.invalid host-a')
        self.assertEqual(sanitize(clean, POLICY), clean)

    def test_projection_and_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'audits/privacy/history-map.json'
            path.parent.mkdir(parents=True)
            baseline = '0' * 40
            mapping = {'schema_version': 1, 'original_mit_baseline': baseline,
                       'original_to_sanitized_commits': {baseline: baseline, '1'*40: '2'*40},
                       'changed_blobs': {'3'*40: '4'*40}}
            path.write_text(json.dumps(mapping))
            manifest = {'history_projection': {'file': 'audits/privacy/history-map.json',
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}
            self.assertEqual(commit(root, '1'*40), '2'*40)
            self.assertEqual(blob(root, '3'*40), '4'*40)
            self.assertEqual(commit(root, '5'*40), '5'*40)
            verify_binding(root, manifest)
            path.write_text(path.read_text() + ' ')
            with self.assertRaises(AssertionError):
                verify_binding(root, manifest)

    def test_changed_mit_baseline_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'audits/privacy/history-map.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'schema_version': 1, 'original_mit_baseline': '0'*40,
                         'original_to_sanitized_commits': {'0'*40: '1'*40}}))
            with self.assertRaises(AssertionError):
                load(root)


if __name__ == '__main__':
    unittest.main()
