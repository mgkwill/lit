"""Real cryptographic CI-signing tests with disposable encrypted credentials."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/release"))
import ci_minisign as ci
import minisign_release as release

COMMIT = "a" * 40


def keypair(root, name, password=None):
    key, public = root / (name + ".key"), root / (name + ".pub")
    args = ["minisign", "-G", "-s", str(key), "-p", str(public)]
    if password is None:
        args.append("-W")
    subprocess.run(args, input=None if password is None else password + "\n" + password + "\n",
                   text=True, capture_output=True, check=True, timeout=90)
    key.chmod(0o600)
    return key, public


class CISigning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="openlit-ci-test-keys-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.key_root = Path(cls.temp.name)
        cls.password = secrets.token_urlsafe(32)
        cls.key, cls.public = keypair(cls.key_root, "publisher", cls.password)
        cls.test_key, cls.test_public = keypair(cls.key_root, "qualification")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openlit-ci-regression-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "candidate"
        self.folder.mkdir()
        for name in release.names(list(release.TARGETS)):
            (self.folder / name).write_bytes(b"CI signing fixture\x00" + name.encode())
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()

    def sign(self, key=None, password=None, public=None):
        key = key or self.key
        password = self.password if password is None else password
        env = dict(os.environ, TMPDIR=str(self.scratch), TMP=str(self.scratch), TEMP=str(self.scratch))
        env[ci.SECRET_KEY] = key.read_text()
        env[ci.PASSWORD] = password
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/release/ci_minisign.py"),
            "sign", str(self.folder), "--public-key", str(public or self.public), "--commit", COMMIT],
            env=env, text=True, capture_output=True, timeout=150)
        for secret in [password, key.read_text(), key.read_text().splitlines()[1]]:
            if secret:
                self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertEqual(list(self.scratch.iterdir()), [], "Temporary publisher key leaked")
        return result

    def unsigned_only(self):
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), release.names(list(release.TARGETS)))

    def test_encrypted_signing_and_six_rejection_checks(self):
        result = self.sign()
        self.assertEqual(result.returncode, 0, result.stderr)
        verified = ci.verify_and_reject_tampering(self.folder, self.public, self.test_public, COMMIT)
        self.assertEqual(len(verified["signed_identity"]["archives"]), 5)
        self.assertEqual(len(verified["tamper_tests"]), 6)
        self.assertEqual(set(verified["tamper_tests"].values()), {"rejected"})
        self.assertFalse(verified["published"])

    def test_wrong_password_removes_partial_metadata_and_key(self):
        self.assertNotEqual(self.sign(password="wrong-password").returncode, 0)
        self.unsigned_only()

    def test_wrong_public_key_removes_partial_metadata_and_key(self):
        self.assertNotEqual(self.sign(public=self.test_public).returncode, 0)
        self.unsigned_only()

    def test_missing_password_refuses_to_sign(self):
        self.assertNotEqual(self.sign(password="").returncode, 0)
        self.unsigned_only()

    def test_unencrypted_key_refuses_to_sign(self):
        self.assertNotEqual(self.sign(key=self.test_key).returncode, 0)
        self.unsigned_only()

    def test_existing_candidate_is_not_overwritten(self):
        self.assertEqual(self.sign().returncode, 0)
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        self.assertNotEqual(self.sign().returncode, 0)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.folder.iterdir()})

    def test_child_does_not_inherit_secret_environment_or_arguments(self):
        original = subprocess.run
        observed = []
        def inspect(command, **kwargs):
            self.assertNotIn(ci.SECRET_KEY, os.environ)
            self.assertNotIn(ci.PASSWORD, os.environ)
            self.assertNotIn(self.password, command)
            self.assertNotIn(self.key.read_text(), command)
            observed.append(command)
            return original(command, **kwargs)
        with patch.dict(os.environ, {ci.SECRET_KEY:self.key.read_text(), ci.PASSWORD:self.password}):
            with patch.object(ci.subprocess, "run", side_effect=inspect):
                ci.sign_from_environment(self.folder, self.public, COMMIT)
        self.assertTrue(observed)

    def test_signer_timeout_cleans_key_and_preserves_archives(self):
        key_paths = []
        def timeout(command, **kwargs):
            key = Path(command[command.index("-s") + 1])
            self.assertTrue(key.is_file())
            key_paths.append(key)
            (self.folder / "RELEASE.json").write_text("partial")
            raise subprocess.TimeoutExpired(command, 120)
        with patch.dict(os.environ, {ci.SECRET_KEY:self.key.read_text(), ci.PASSWORD:self.password}):
            with patch.object(ci.subprocess, "run", side_effect=timeout):
                with self.assertRaises(subprocess.TimeoutExpired):
                    ci.sign_from_environment(self.folder, self.public, COMMIT)
        self.assertTrue(key_paths)
        self.assertTrue(all(not p.exists() for p in key_paths))
        self.unsigned_only()

    def downloads(self):
        downloads = self.root / "downloads"
        downloads.mkdir()
        for target in release.TARGETS:
            artifact = downloads / ("openlit-" + target)
            signed = artifact / ("signed-" + target)
            signed.mkdir(parents=True)
            name = release.names([target])[0]
            shutil.copyfile(self.folder / name, signed / name)
            public = artifact / ("signed-" + target + ".pub")
            shutil.copyfile(self.test_public, public)
            identity = release.sign(signed, self.test_key, public, COMMIT, [target], True)
            receipt = {"head": COMMIT, "target": target, "archive": name,
                       "archive_sha256": release.digest(signed / name),
                       "qualification_public_key_sha256": release.digest(public),
                       "minisign": identity, "tamper_tests": dict.fromkeys(
                           ["archive", "manifest", "signature", "wrong_key"], "rejected")}
            (artifact / ("qualification-" + target + ".json")).write_text(json.dumps(receipt))
        return downloads

    def test_assemble_exact_same_run_qualified_bytes(self):
        downloads = self.downloads()
        output = self.root / "assembled"
        result = ci.prepare(downloads, output, COMMIT)
        self.assertEqual(result["archives"], {p.name: release.digest(p) for p in self.folder.iterdir()})
        self.assertEqual(sorted(p.name for p in output.iterdir()), release.names(list(release.TARGETS)))

    def test_reject_other_source_commit_before_assembly(self):
        output = self.root / "assembled"
        with self.assertRaises(ValueError):
            ci.prepare(self.downloads(), output, "b" * 40)
        self.assertFalse(output.exists())

    def test_reject_modified_archive_and_receipt(self):
        downloads = self.downloads()
        target = next(iter(release.TARGETS))
        artifact = downloads / ("openlit-" + target)
        archive = artifact / ("signed-" + target) / release.names([target])[0]
        archive.write_bytes(archive.read_bytes() + b"changed")
        receipt = artifact / ("qualification-" + target + ".json")
        data = json.loads(receipt.read_text())
        data["archive_sha256"] = release.digest(archive)
        receipt.write_text(json.dumps(data))
        with self.assertRaises(subprocess.CalledProcessError):
            ci.prepare(downloads, self.root / "assembled", COMMIT)
        self.assertFalse((self.root / "assembled").exists())

    def test_reject_wrong_receipt_even_when_signature_valid(self):
        downloads = self.downloads()
        receipt = next(downloads.glob("*/qualification-*.json"))
        data = json.loads(receipt.read_text())
        data["tamper_tests"]["archive"] = "accepted"
        receipt.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            ci.prepare(downloads, self.root / "assembled", COMMIT)

    def test_reject_missing_artifact(self):
        downloads = self.downloads()
        shutil.rmtree(next(downloads.iterdir()))
        with self.assertRaises(ValueError):
            ci.prepare(downloads, self.root / "assembled", COMMIT)


if __name__ == "__main__":
    unittest.main()
