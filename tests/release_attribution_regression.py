"""Reject archives that omit or alter release attribution, on Unix and Windows."""
import importlib.util
from pathlib import Path
import tarfile
import tempfile
import tomllib
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("qualify", ROOT / "tests/qualify_release_archive.py")
qualify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualify)


class ReleaseAttribution(unittest.TestCase):
    def check_archive(self, windows, changed=None, missing=None):
        with tempfile.TemporaryDirectory(prefix="openlit-attribution-") as tmp:
            root = Path(tmp)
            binary = "lit.exe" if windows else "lit"
            files = {binary:b"test executable fixture"}
            files.update({name:(ROOT / name).read_bytes() for name in
                          ["LICENSE", "NOTICE.md", "THIRD_PARTY_NOTICES.txt"]})
            if changed:
                files[changed] = b"incorrect attribution\n"
            if missing:
                del files[missing]
            archive = root / ("archive.zip" if windows else "archive.tar.gz")
            if windows:
                with zipfile.ZipFile(archive, "w") as package:
                    for name, data in files.items():
                        package.writestr(name, data)
            else:
                source = root / "source"
                source.mkdir()
                with tarfile.open(archive, "w:gz") as package:
                    for name, data in files.items():
                        (source / name).write_bytes(data)
                        package.add(source / name, arcname=name)
            output = root / "unpacked"
            output.mkdir()
            return qualify.unpack(archive, output, binary).read_bytes()

    def test_complete_notices_accepted_in_both_formats(self):
        for windows in (False, True):
            with self.subTest(windows=windows):
                self.assertEqual(self.check_archive(windows), b"test executable fixture")

    def test_missing_notice_rejected_in_both_formats(self):
        for windows in (False, True):
            with self.subTest(windows=windows), self.assertRaises(AssertionError):
                self.check_archive(windows, missing="NOTICE.md")

    def test_changed_attribution_rejected_in_both_formats(self):
        for windows in (False, True):
            for name in ("LICENSE", "NOTICE.md", "THIRD_PARTY_NOTICES.txt"):
                with self.subTest(windows=windows, file=name), self.assertRaises(AssertionError):
                    self.check_archive(windows, changed=name)

    def test_signing_identity_matches_package_version(self):
        spec = importlib.util.spec_from_file_location("signing", ROOT / "tools/release/minisign_release.py")
        signing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(signing)
        version = tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]["version"]
        locked = next(p for p in tomllib.loads((ROOT / "Cargo.lock").read_text())["package"]
                      if p["name"] == "openlit")
        self.assertEqual(signing.VERSION, version)
        self.assertEqual(locked["version"], version)


if __name__ == "__main__":
    unittest.main()
