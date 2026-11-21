"""Fetch pinned Minisign 0.12 into a new local directory, without a global install.

These upstream archives were verified with Minisign's published release key
before their SHA-256 hashes were pinned here. No upstream Lit source is used.
https://jedisct1.github.io/minisign/
"""
import argparse
import hashlib
import io
from pathlib import Path
import platform
import tarfile
import urllib.request
import zipfile

ASSETS = {
    "Linux": ("linux.tar.gz", "9a599b48ba6eb7b1e80f12f36b94ceca7c00b7a5173c95c3efc88d9822957e73"),
    "Darwin": ("macos.zip", "89000b19535765f9cffc65a65d64a820f433ef6db8020667f7570e06bf6aac63"),
    "Windows": ("win64.zip", "37b600344e20c19314b2e82813db2bfdcc408b77b876f7727889dbd46d539479"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    system = platform.system()
    arch = {"arm64": "aarch64", "amd64": "x86_64"}.get(platform.machine().lower(), platform.machine().lower())
    if system not in ASSETS or arch not in {"x86_64", "aarch64"}:
        parser.error("Unsupported Minisign platform")
    if system == "Darwin" and arch == "x86_64":
        parser.error("Upstream Minisign 0.12 macOS archive is arm64-only; install native Minisign with Homebrew on Intel")
    asset, expected = ASSETS[system]
    url = "https://github.com/jedisct1/minisign/releases/download/0.12/minisign-0.12-" + asset
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected:
        parser.error("Minisign download checksum mismatch")
    if system == "Linux":
        with tarfile.open(fileobj=io.BytesIO(data)) as package:
            member = package.getmember(f"minisign-linux/{arch}/minisign")
            if not member.isfile():
                parser.error("Expected a regular Minisign executable")
            binary = package.extractfile(member).read()
    else:
        member = "minisign" if system == "Darwin" else f"minisign-win64/{arch}/minisign.exe"
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            binary = package.read(member)
    args.destination.mkdir(parents=True, exist_ok=False)
    target = args.destination / ("minisign.exe" if system == "Windows" else "minisign")
    with target.open("xb") as output:
        output.write(binary)
    target.chmod(0o755)
    print(args.destination.resolve())


if __name__ == "__main__":
    main()
