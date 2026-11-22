"""Negative signing gates and exact-subject attestation policy.

These tests do not simulate successful publisher signing. Native untrusted-file
rejections supplement command-policy tests; genuine signed archives need the
selected publisher credentials and a live notarization/attestation service.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mac = load('mac', 'tools/release/sign_macos.py')
attest = load('attest', 'tools/release/verify_attestations.py')


class ReleaseGates(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.lines = []
        for target, extension in attest.TARGETS.items():
            name = f'openlit-0.1.1-{target}{extension}'
            payload = name.encode()
            (self.folder / name).write_bytes(payload)
            self.lines.append(hashlib.sha256(payload).hexdigest() + '  ' + name)
        self.write_manifest()

    def write_manifest(self):
        (self.folder / 'SHA256SUMS').write_text('\n'.join(self.lines) + '\n')

    def test_complete_exact_subject_set(self):
        self.assertEqual(len(attest.manifest_files(self.folder)), 6)

    def test_modified_final_archive_is_rejected(self):
        (self.folder / self.lines[0].split('  ')[1]).write_bytes(b'changed after signing')
        with self.assertRaisesRegex(ValueError, 'checksum changed'):
            attest.manifest_files(self.folder)

    def test_incomplete_duplicate_and_path_escape_are_rejected(self):
        original = list(self.lines)
        for bad in (original[:-1], original + [original[0]], ['a' * 64 + '  ../escape']):
            with self.subTest(bad=bad):
                self.lines = bad
                self.write_manifest()
                with self.assertRaises(ValueError):
                    attest.manifest_files(self.folder)

    def test_attestation_service_failure_is_not_a_pass(self):
        with patch.object(attest.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'gh')):
            with self.assertRaises(subprocess.CalledProcessError):
                attest.verify(self.folder, 'example/repo', 'a' * 40, 'refs/heads/candidate', '.github/workflows/signing.yml')

    def test_verifier_pins_source_and_workflow_not_only_subject_hash(self):
        result = subprocess.CompletedProcess([], 0, stdout='[{}]')
        with patch.object(attest.subprocess, 'run', return_value=result) as invoke:
            attest.verify(self.folder, 'example/repo', 'a' * 40, 'refs/heads/candidate', '.github/workflows/signing.yml')
        self.assertEqual(invoke.call_count, 6)
        for call in invoke.call_args_list:
            args = call.args[0]
            for flag, expected in (('--repo', 'example/repo'), ('--source-digest', 'a' * 40),
                ('--signer-digest', 'a' * 40), ('--source-ref', 'refs/heads/candidate'),
                ('--signer-workflow', 'example/repo/.github/workflows/signing.yml'),
                ('--cert-oidc-issuer', 'https://token.actions.githubusercontent.com')):
                self.assertEqual(args[args.index(flag) + 1], expected)
            self.assertTrue(call.kwargs['check'])

    def test_empty_verification_result_is_rejected(self):
        with patch.object(attest.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='[]')):
            with self.assertRaisesRegex(ValueError, 'No cryptographically verified'):
                attest.verify(self.folder, 'example/repo', 'a' * 40, 'refs/heads/candidate', '.github/workflows/signing.yml')

    def test_apple_development_identity_is_not_distribution_identity(self):
        with patch.object(mac, 'run', return_value='1) ' + 'A' * 40 + ' "Apple Development: Developer (ABCDE12345)"'):
            with self.assertRaisesRegex(ValueError, 'Developer ID Application identity is unavailable'):
                mac.require_identity('ABCDE12345', 'A' * 40)

    def test_missing_apple_timestamp_or_runtime_is_rejected(self):
        for details in ('CodeDirectory flags=0x10000(runtime)\n', 'Timestamp=Nov 20 2026\nCodeDirectory flags=0x0(none)\n'):
            with patch.object(mac, 'run', side_effect=['', details]):
                with self.assertRaises(ValueError):
                    mac.verify(Path('candidate'), 'ABCDE12345', 'A' * 40)

    def test_native_unsigned_file_is_rejected(self):
        binary = self.folder / 'unsigned'
        binary.write_bytes(b'This is not a signed executable')
        if platform.system() == 'Darwin':
            with self.assertRaises(RuntimeError):
                mac.verify(binary, 'ABCDE12345', 'A' * 40)
        elif platform.system() == 'Windows':
            self.assertIsNotNone(shutil.which('signtool.exe'), 'SDK verifier must be present for this native test')
            result = subprocess.run(['pwsh', '-NoProfile', '-File', str(ROOT / 'tools/release/sign_windows.ps1'),
                '-Binary', str(binary), '-Thumbprint', 'A' * 40], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
        else:
            self.skipTest('Native platform signature verifier unavailable on Linux')

    def test_signed_macos_gate_rejects_unsigned_archive_before_execution(self):
        result = subprocess.run([shutil.which('python3') or shutil.which('python'), '-B',
            str(ROOT / 'tests/qualify_release_archive.py'), 'unsigned.tar.gz',
            'aarch64-apple-darwin', '--require-signed'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires a DMG', result.stderr)


if __name__ == '__main__':
    unittest.main()
