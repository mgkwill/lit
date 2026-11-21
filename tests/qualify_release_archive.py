"""Verify a release archive by exercising its executable on the native target."""

import argparse
import hashlib
import importlib.util
import shutil
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile


TESTS = Path(__file__).resolve().parent
TARGETS = {
    "x86_64-unknown-linux-gnu": ("Linux", "x86_64"),
    "aarch64-unknown-linux-gnu": ("Linux", "aarch64"),
    "x86_64-apple-darwin": ("Darwin", "x86_64"),
    "aarch64-apple-darwin": ("Darwin", "aarch64"),
    "x86_64-pc-windows-msvc": ("Windows", "x86_64"),
}


def run(*args, timeout=180):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{list(args)} exited {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unpack(archive, directory, name, team=None, certificate=None):
    expected = sorted(["LICENSE", "THIRD_PARTY_NOTICES.txt", name])
    if archive.suffix == ".dmg":
        spec = importlib.util.spec_from_file_location("mac_signing", TESTS.parent / "tools/release/sign_macos.py")
        signing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(signing)
        signing.verify(archive, team or "", certificate or "", runtime=False)
        run("xcrun", "stapler", "validate", archive)
        assert run("spctl", "--status") == "assessments enabled"
        run("spctl", "--assess", "--type", "open", "--context", "context:primary-signature", archive)
        mount = directory / "mount"
        mount.mkdir()
        run("hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, archive)
        try:
            assert sorted(p.name for p in mount.iterdir()) == expected
            assert all(p.is_file() and not p.is_symlink() for p in mount.iterdir())
            for filename in expected:
                shutil.copy2(mount / filename, directory / filename)
        finally:
            run("hdiutil", "detach", mount)
        signing.verify(directory / name, team, certificate)
        run("spctl", "--assess", "--type", "execute", directory / name)
    elif name.endswith(".exe"):
        with zipfile.ZipFile(archive) as package:
            assert sorted(package.namelist()) == expected
            package.extractall(directory)
    else:
        with tarfile.open(archive, "r:gz") as package:
            members = package.getmembers()
            assert sorted(member.name for member in members) == expected
            assert all(member.isfile() for member in members)
            package.extractall(directory, filter="data")
    assert (directory / "LICENSE").read_bytes() == (TESTS.parent / "LICENSE").read_bytes()
    assert (directory / "THIRD_PARTY_NOTICES.txt").read_bytes() == (TESTS.parent / "THIRD_PARTY_NOTICES.txt").read_bytes()
    return directory / name


def glibc_versions(binary):
    host = run("getconf", "GNU_LIBC_VERSION")
    assert host == "glibc 2.35", host
    symbols = re.findall(r"\bGLIBC_(\d+(?:\.\d+)+)\b", run("readelf", "--version-info", binary))
    required = max(tuple(map(int, version.split("."))) for version in symbols)
    assert required <= (2, 35), required
    return {"runtime": host, "highest_required_symbol_version": ".".join(map(str, required))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument("--apple-team")
    parser.add_argument("--certificate", help="Expected platform signer certificate SHA-1 fingerprint")
    parser.add_argument("--require-signed", action="store_true")
    args = parser.parse_args()
    if args.require_signed:
        if "apple" in args.target and (args.archive.suffix != ".dmg" or not args.apple_team or not args.certificate):
            parser.error("Signed macOS qualification requires a DMG, Team ID, and certificate fingerprint")
        if "windows" in args.target and not args.certificate:
            parser.error("Signed Windows qualification requires a certificate fingerprint")
    machine = platform.machine().lower()
    arch = {"arm64": "aarch64", "amd64": "x86_64"}.get(machine, machine)
    assert (platform.system(), arch) == TARGETS[args.target], (platform.system(), machine, args.target)
    native_windows = platform.system() == "Windows"
    receipt = {
        "archive": args.archive.name,
        "archive_sha256": digest(args.archive),
        "git": run("git", "--version"),
        "head": run("git", "rev-parse", "HEAD"),
        "rustc": run("rustc", "--version"),
        "target": args.target,
    }
    metadata = json.loads(run("cargo", "metadata", "--locked", "--format-version", "1"))
    dependency = next(p["id"] for p in metadata["packages"] if p["name"] == "pqcrypto-mldsa")
    features = next(n["features"] for n in metadata["resolve"]["nodes"] if n["id"] == dependency)
    assert features == ["std"], features
    receipt["mldsa_features"] = features
    with tempfile.TemporaryDirectory(prefix="lit-qualify-") as folder:
        binary = unpack(args.archive, Path(folder), "lit.exe" if native_windows else "lit", args.apple_team, args.certificate)
        if native_windows and (args.require_signed or args.certificate):
            run("pwsh", "-NoProfile", "-File", TESTS.parent / "tools/release/sign_windows.ps1",
                "-Binary", binary, "-Thumbprint", args.certificate, "-Mode", "Verify")
        receipt["final_candidate_policy"] = args.require_signed
        receipt["platform_signature_required"] = args.require_signed and platform.system() != "Linux"
        receipt["expected_certificate_sha1"] = args.certificate
        receipt["binary_sha256"] = digest(binary)
        manifest = tomllib.loads((TESTS.parent / "Cargo.toml").read_text())
        receipt["package"] = manifest["package"]["name"]
        receipt["version"] = run(binary, "--version")
        assert receipt["package"] == "openlit"
        assert receipt["version"] == "lit " + manifest["package"]["version"]
        assert "OpenLit" in run(binary, "--help")
        if platform.system() == "Linux":
            receipt["glibc"] = glibc_versions(binary)
        receipt["batch_and_mcp"] = json.loads(run(sys.executable, "-B", TESTS / "release_smoke.py", binary))
        assert receipt["batch_and_mcp"]["candidate_sha256"] == receipt["binary_sha256"]
        receipt["remotes"] = run(sys.executable, "-B", TESTS / "remote_regression.py", binary).splitlines()
        if native_windows:
            receipt["sandbox"] = run(sys.executable, "-B", TESTS / "sandbox_regression.py", binary).splitlines()
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
