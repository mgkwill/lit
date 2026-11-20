"""Verify a release archive by exercising its executable on the native target."""

import argparse
import hashlib
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


def unpack(archive, directory, name):
    expected = sorted(["LICENSE", "THIRD_PARTY_NOTICES.txt", name])
    if name.endswith(".exe"):
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
    args = parser.parse_args()
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
        binary = unpack(args.archive, Path(folder), "lit.exe" if native_windows else "lit")
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
