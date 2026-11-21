"""Sign or verify OpenLit 0.1.0 archives. This tool never publishes anything."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

VERSION = "0.1.0"
TARGETS = {
    "x86_64-unknown-linux-gnu": ".tar.gz",
    "aarch64-unknown-linux-gnu": ".tar.gz",
    "x86_64-apple-darwin": ".tar.gz",
    "aarch64-apple-darwin": ".tar.gz",
    "x86_64-pc-windows-msvc": ".zip",
}
ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def names(targets):
    if not targets or len(set(targets)) != len(targets) or set(targets) - TARGETS.keys():
        raise ValueError("Choose distinct supported targets")
    return sorted(f"openlit-{VERSION}-{t}{TARGETS[t]}" for t in targets)


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing regular file: {path.name}")
    return path


def expected_commit(commit):
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Supply the full approved source commit SHA")


def checksum_text(folder, archives):
    return "".join(f"{digest(regular(folder / name))}  {name}\n" for name in archives)


def verify(folder, public_key, commit, targets, qualification=False):
    """The caller supplies the trusted public key and approved source identity."""
    folder = Path(folder)
    expected_commit(commit)
    archives = names(targets)
    files = archives + ["SHA256SUMS", "RELEASE.json"]
    expected = set(files) | {name + ".minisig" for name in files}
    if {p.name for p in folder.iterdir()} != expected:
        raise ValueError("Candidate file set is incomplete or contains unexpected files")
    regular(Path(public_key))
    for name in files:
        path = regular(folder / name)
        regular(folder / (name + ".minisig"))
        subprocess.run(["minisign", "-V", "-H", "-q", "-p", str(public_key),
                        "-m", str(path)], check=True, capture_output=True, text=True)
    # Parse only authenticated metadata. Do not trust names or hashes from an
    # unsigned manifest, or accept a candidate-selected verification key.
    if (folder / "SHA256SUMS").read_bytes() != checksum_text(folder, archives).encode():
        raise ValueError("Signed checksums do not match the exact archive set")
    expected = {
        "product": "OpenLit", "version": VERSION, "source_commit": commit,
        "purpose": "qualification" if qualification else "release-candidate",
        "archives": {name: digest(folder / name) for name in archives},
    }
    if json.loads((folder / "RELEASE.json").read_bytes()) != expected:
        raise ValueError("Signed release identity differs from the approved candidate")
    return expected


def sign(folder, secret_key, public_key, commit, targets, qualification=False):
    folder = Path(folder).resolve()
    key = Path(secret_key).resolve(strict=True)
    regular(key)
    # Check the resolved location, including keys reached through symlinks.
    if key.is_relative_to(ROOT) or key.is_relative_to(folder) or any(
            (parent / ".git").exists() for parent in key.parents):
        raise ValueError("Private key must be outside Git checkouts and the candidate directory")
    if os.name != "nt" and key.stat().st_mode & 0o077:
        raise ValueError("Private key permissions must exclude group and other users (chmod 600)")
    expected_commit(commit)
    archives = names(targets)
    if set(p.name for p in folder.iterdir()) != set(archives):
        raise ValueError("Signing requires a fresh directory containing exactly the selected archives")
    checksums = checksum_text(folder, archives)
    metadata = {
        "product": "OpenLit", "version": VERSION, "source_commit": commit,
        "purpose": "qualification" if qualification else "release-candidate",
        "archives": {name: digest(folder / name) for name in archives},
    }
    files = archives + ["SHA256SUMS", "RELEASE.json"]
    try:
        (folder / "SHA256SUMS").write_bytes(checksums.encode())
        (folder / "RELEASE.json").write_bytes((json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode())
        # Inherit the terminal: an encrypted key prompts locally, never via a
        # password argument, environment variable, committed file, or receipt.
        subprocess.run(["minisign", "-S", "-q", "-s", str(key), "-t",
                        f"OpenLit {VERSION} {metadata['purpose']} source {commit}",
                        "-m", *[str(folder / name) for name in files]], check=True)
        return verify(folder, public_key, commit, targets, qualification)
    except BaseException:
        # Keep original archives; never leave a partly signed candidate looking
        # complete after a bad password, mismatched key, or interrupted signer.
        for name in ["SHA256SUMS", "RELEASE.json"] + [n + ".minisig" for n in files]:
            (folder / name).unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["sign", "verify"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--public-key", required=True, type=Path,
                        help="Independently trusted public key file")
    parser.add_argument("--secret-key", type=Path)
    parser.add_argument("--commit", required=True, help="Full independently approved source SHA")
    parser.add_argument("--target", action="append", choices=TARGETS,
                        help="Explicit subset for testing; default requires all five platforms")
    parser.add_argument("--qualification", action="store_true", help="Label disposable-key test candidates")
    args = parser.parse_args()
    targets = args.target or list(TARGETS)
    try:
        if args.operation == "sign":
            if not args.secret_key:
                parser.error("sign requires --secret-key outside the checkout")
            result = sign(args.folder, args.secret_key, args.public_key, args.commit, targets, args.qualification)
        else:
            if args.secret_key:
                parser.error("verify needs only the trusted public key")
            result = verify(args.folder, args.public_key, args.commit, targets, args.qualification)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Minisign candidate rejected: {error}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
