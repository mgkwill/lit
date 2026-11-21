"""Real Minisign signatures and rejection tests; no publisher keys or network."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("minisign_release", ROOT / "tools/release/minisign_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
COMMIT = "a" * 40


def keypair(folder, name="test", password=None):
    key, pub = folder / (name + ".key"), folder / (name + ".pub")
    args = ["minisign", "-G", "-s", str(key), "-p", str(pub)]
    if password is None:
        args.append("-W")  # Disposable qualification keys only.
    subprocess.run(args, input=None if password is None else password + "\n" + password + "\n",
                   capture_output=True, text=True, check=True, timeout=90)
    key.chmod(0o600)
    return key, pub


class MinisignRelease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("minisign"):
            raise RuntimeError("Install minisign to run signature tests; missing verifier is not a pass")
        cls.keys = tempfile.TemporaryDirectory(prefix="openlit-test-keys-")
        cls.addClassCleanup(cls.keys.cleanup)
        cls.key, cls.pub = keypair(Path(cls.keys.name))
        _, cls.other_pub = keypair(Path(cls.keys.name), "other")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openlit-signatures-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.targets = list(release.TARGETS)
        for name in release.names(self.targets):
            (self.folder / name).write_bytes(b"signature policy fixture\x00" + name.encode())
        release.sign(self.folder, self.key, self.pub, COMMIT, self.targets, True)

    def verify(self, **changes):
        args = dict(folder=self.folder, public_key=self.pub, commit=COMMIT, targets=self.targets, qualification=True)
        args.update(changes)
        return release.verify(**args)

    def reject(self, **changes):
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            self.verify(**changes)

    def resign(self, name):
        subprocess.run(["minisign", "-S", "-s", str(self.key), "-m", str(self.folder / name)],
                       check=True, capture_output=True)

    def test_all_five_archives_and_manifests_verify(self):
        self.assertEqual(len(self.verify()["archives"]), 5)

    def test_archive_tampering_and_wrong_key(self):
        self.reject(public_key=self.other_pub)
        archive = self.folder / release.names(self.targets)[0]
        archive.write_bytes(archive.read_bytes() + b"tamper")
        self.reject()

    def test_signature_and_trusted_comment_tampering(self):
        signature = self.folder / "SHA256SUMS.minisig"
        original = signature.read_bytes()
        lines = original.splitlines()
        lines[1] = b"A" * len(lines[1])
        signature.write_bytes(b"\n".join(lines) + b"\n")
        self.reject()
        signature.write_bytes(original.replace(b"trusted comment: OpenLit", b"trusted comment: Changed"))
        self.reject()

    def test_manifest_tampering_even_with_updated_unsigned_checksum(self):
        manifest = self.folder / "SHA256SUMS"
        manifest.write_bytes(manifest.read_bytes() + b"injected\n")
        self.reject()

    def test_wrong_source_target_set_and_test_key_purpose(self):
        self.reject(commit="b" * 40)
        self.reject(targets=self.targets[:-1])
        self.reject(qualification=False)

    def test_missing_and_unexpected_files(self):
        extra = self.folder / "extra.key"
        extra.write_bytes(b"not a real key")
        self.reject()
        extra.unlink()
        (self.folder / "RELEASE.json.minisig").unlink()
        self.reject()

    def test_authenticated_metadata_must_match_actual_files(self):
        path = self.folder / "RELEASE.json"
        data = json.loads(path.read_text())
        data["source_commit"] = "b" * 40
        path.write_text(json.dumps(data))
        self.resign("RELEASE.json")
        self.reject()

    def test_signed_duplicate_or_escape_manifest_entries(self):
        path = self.folder / "SHA256SUMS"
        original = path.read_text()
        for content in (original + original.splitlines()[0] + "\n", "a" * 64 + "  ../escape\n"):
            path.write_text(content)
            self.resign("SHA256SUMS")
            self.reject()

    @unittest.skipIf(os.name == "nt", "Windows symlinks require elevated privileges")
    def test_archive_symlink_is_rejected(self):
        archive = self.folder / release.names(self.targets)[0]
        outside = Path(self.keys.name) / "outside-archive"
        outside.write_bytes(archive.read_bytes())
        archive.unlink()
        archive.symlink_to(outside)
        self.reject()

    def test_refuses_key_in_checkout_without_reading_key_contents(self):
        with tempfile.NamedTemporaryFile(dir=ROOT, prefix="dummy-key-", delete=False) as file:
            file.write(b"not a private key")
            path = Path(file.name)
        try:
            with self.assertRaisesRegex(ValueError, "outside Git"):
                release.sign(self.folder, path, self.pub, COMMIT, self.targets)
        finally:
            path.unlink()

    def test_failed_signing_cleans_generated_metadata(self):
        for path in list(self.folder.iterdir()):
            if path.name not in release.names(self.targets):
                path.unlink()
        with self.assertRaises(subprocess.CalledProcessError):
            release.sign(self.folder, self.key, self.other_pub, COMMIT, self.targets)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), release.names(self.targets))

    def test_encrypted_external_key_signs_without_password_arguments(self):
        password = secrets.token_urlsafe(32)
        key, pub = keypair(Path(self.keys.name), "encrypted", password)
        for path in list(self.folder.iterdir()):
            if path.name not in release.names(self.targets):
                path.unlink()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/release/minisign_release.py"),
            "sign", str(self.folder), "--secret-key", str(key), "--public-key", str(pub),
            "--commit", COMMIT], input=password + "\n", capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(password, result.stdout + result.stderr)
        self.verify(public_key=pub, qualification=False)


if __name__ == "__main__":
    unittest.main()
