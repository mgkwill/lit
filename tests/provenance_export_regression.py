"""The public record export must not reveal private input/output bodies."""
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('exports', ROOT / 'audits/evidence/tools/export_records.py')
exports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exports)


class ExportIntegrity(unittest.TestCase):
    def test_round_trip_and_body_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            source, dest = Path(directory) / 'original.jsonl', Path(directory) / 'export.jsonl.gz'
            rows = [
                {'type': 'session_meta', 'timestamp': '2026-11-20T00:00:00Z', 'payload': {'id': 'fixture'}},
                {'type': 'response_item', 'payload': {'type': 'custom_tool_call', 'name': 'exec',
                 'call_id': 'call_example', 'input': 'SECRET_IN_CALL /private/location'}},
                {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output',
                 'call_id': 'call_example', 'output': 'SECRET_IN_RESULT'}},
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                 'content': [{'text': 'Use the MIT snapshot, keep upstream black-box. SECRET_IN_MESSAGE'}]}},
                {'type': 'response_item', 'payload': {'type': 'reasoning', 'summary': 'SECRET_IN_REASONING'}},
            ]
            source.write_bytes(b''.join(exports.encode(r) for r in rows))
            receipt = exports.export(source, dest, 'fixture', 'implementation')
            self.assertEqual(exports.verify(source, dest, receipt), 5)
            data = gzip.decompress(dest.read_bytes())
            self.assertNotIn(b'SECRET', data)
            self.assertNotIn(b'/private/location', data)
            self.assertIn(b'upstream-black-box', data)
            original = dest.read_bytes()
            dest.write_bytes(original + b'corruption')
            with self.assertRaises(AssertionError):
                exports.verify(source, dest, receipt)
            dest.write_bytes(original)
            source.write_bytes(source.read_bytes().replace(b'SECRET_IN_RESULT', b'CHANGED_RESULT'))
            with self.assertRaises(AssertionError):
                exports.verify(source, dest, receipt)

    def test_partial_record_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            source, dest = Path(directory) / 'original', Path(directory) / 'export'
            source.write_bytes(b'{"type":"session_meta","payload":{"id":"fixture"}}')
            with self.assertRaises(AssertionError):
                exports.export(source, dest, 'fixture', 'implementation')


if __name__ == '__main__':
    unittest.main()
