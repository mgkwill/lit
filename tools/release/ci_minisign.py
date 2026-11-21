"""Prepare, sign, and check a CI candidate. No tags or releases are published."""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import minisign_release as release

SECRET_KEY = "OPENLIT_MINISIGN_SECRET_KEY"
PASSWORD = "OPENLIT_MINISIGN_PASSWORD"


def prepare(downloads, output, commit):
    """Accept only the five qualified artifacts from this workflow run."""
    release.expected_commit(commit)
    if output.exists():
        raise ValueError("Candidate output must be new")
    expected = {"openlit-" + target for target in release.TARGETS}
    if {p.name for p in downloads.iterdir()} != expected:
        raise ValueError("Expected exactly five native qualification artifacts")
    archives = {}
    sources = {}
    for target in release.TARGETS:
        artifact = downloads / ("openlit-" + target)
        signed = artifact / ("signed-" + target)
        public = artifact / ("signed-" + target + ".pub")
        receipt_path = artifact / ("qualification-" + target + ".json")
        if artifact.is_symlink() or signed.is_symlink():
            raise ValueError("Qualification directories cannot be symlinks")
        if {p.name for p in artifact.iterdir()} != {signed.name, public.name, receipt_path.name}:
            raise ValueError("Unexpected qualification artifact contents")
        identity = release.verify(signed, public, commit, [target], True)
        receipt = json.loads(release.regular(receipt_path).read_bytes())
        name = release.names([target])[0]
        sha = release.digest(signed / name)
        if not (receipt["head"] == commit and receipt["target"] == target
                and receipt["archive"] == name and receipt["archive_sha256"] == sha
                and receipt["qualification_public_key_sha256"] == release.digest(public)
                and receipt["minisign"] == identity
                and receipt["tamper_tests"] == dict.fromkeys(
                    ["archive", "manifest", "signature", "wrong_key"], "rejected")):
            raise ValueError("Native qualification receipt differs from the candidate")
        archives[name] = sha
        sources[name] = signed / name
    output.mkdir(mode=0o700, parents=True)
    try:
        for name, source in sources.items():
            shutil.copyfile(source, output / name)
            if release.digest(output / name) != archives[name]:
                raise ValueError("Archive changed during assembly")
    except BaseException:
        shutil.rmtree(output)
        raise
    return {"source_commit": commit, "archives": archives}


def sign_from_environment(folder, public, commit):
    """Consume secrets without printing them or placing them in child arguments."""
    key_text = os.environ.pop(SECRET_KEY, None)
    password = os.environ.pop(PASSWORD, None)
    if not key_text or not password or any(c in password for c in "\r\n\0"):
        raise ValueError("Both protected environment secrets are required; password must be one line")
    try:
        key_data = base64.b64decode(key_text.splitlines()[1], validate=True)
        encrypted = key_data[2:4] == b"Sc"
    except (ValueError, IndexError):
        encrypted = False
    if not encrypted:
        raise ValueError("CI requires a password-encrypted Minisign key")
    release.expected_commit(commit)
    archives = release.names(list(release.TARGETS))
    if {p.name for p in folder.iterdir()} != set(archives):
        raise ValueError("Signing requires exactly five fresh archives")
    completed = False
    try:
        with tempfile.TemporaryDirectory(prefix="openlit-ci-key-") as temporary:
            key = Path(temporary) / "publisher.key"
            # Refuse a temp directory redirected into any checkout or artifact tree.
            if any((p / ".git").exists() for p in key.resolve().parents) or key.resolve().is_relative_to(folder.resolve()):
                raise ValueError("Temporary key directory must be outside Git and candidate output")
            descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(key_text)
            # Neither secret remains in the environment inherited by the signer.
            # Minisign reads its password from the pipe, never from arguments/logs.
            identity = release.sign(folder, key, public, commit, list(release.TARGETS), password=password)
            completed = True
            return identity
    finally:
        if not completed:
            for name in ["SHA256SUMS", "RELEASE.json"] + [n + ".minisig" for n in archives + ["SHA256SUMS", "RELEASE.json"]]:
                (folder / name).unlink(missing_ok=True)


def verify_and_reject_tampering(folder, public, wrong_public, commit):
    identity = release.verify(folder, public, commit, list(release.TARGETS))
    before = {p.name: release.digest(p) for p in folder.iterdir()}
    negatives = {}
    with tempfile.TemporaryDirectory(prefix="openlit-ci-tamper-") as temporary:
        changed = Path(temporary) / "candidate"
        shutil.copytree(folder, changed)
        archive = release.names(list(release.TARGETS))[0]
        for kind, name in [("wrong_key", None), ("wrong_source", None),
                           ("archive", archive), ("checksums", "SHA256SUMS"),
                           ("release_identity", "RELEASE.json"), ("signature", archive + ".minisig")]:
            if name:
                path = changed / name
                original = path.read_bytes()
                path.write_bytes(b"invalid signature\n" if kind == "signature" else original + b"tamper")
            bad_commit = ("0" if commit[0] != "0" else "1") + commit[1:]
            try:
                release.verify(changed, wrong_public if kind == "wrong_key" else public,
                               bad_commit if kind == "wrong_source" else commit, list(release.TARGETS))
            except (ValueError, subprocess.CalledProcessError):
                negatives[kind] = "rejected"
            else:
                raise ValueError("Tamper check unexpectedly accepted: " + kind)
            finally:
                if name:
                    path.write_bytes(original)
    if before != {p.name: release.digest(p) for p in folder.iterdir()}:
        raise ValueError("Original candidate changed during rejection tests")
    release.verify(folder, public, commit, list(release.TARGETS))
    return {"signed_identity": identity, "public_key_sha256": release.digest(public),
            "file_sha256": before, "tamper_tests": negatives, "published": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "sign", "verify"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--downloads", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--wrong-public-key", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "prepare":
            if not args.downloads:
                parser.error("prepare requires --downloads")
            result = prepare(args.downloads, args.folder, args.commit)
        elif args.operation == "sign":
            if not args.public_key:
                parser.error("sign requires --public-key")
            result = sign_from_environment(args.folder, args.public_key, args.commit)
        else:
            if not args.public_key or not args.wrong_public_key:
                parser.error("verify requires both public key paths")
            result = verify_and_reject_tampering(args.folder, args.public_key, args.wrong_public_key, args.commit)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        # Secrets are untrusted strings: never render raw exceptions or subprocess output.
        parser.exit(1, "CI signing check failed; candidate was not approved.\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
