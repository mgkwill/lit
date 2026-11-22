"""Sign a fresh native archive with a disposable key, reject tampering, then run it.

The retained public key and signatures are test evidence, not publisher identity.
Private keys exist only in an external temporary directory and are deleted.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release", ROOT / "tools/release/minisign_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def keypair(folder, name):
    key, pub = folder / (name + ".key"), folder / (name + ".pub")
    subprocess.run(["minisign", "-G", "-W", "-s", str(key), "-p", str(pub)],
                   check=True, capture_output=True)
    key.chmod(0o600)
    return key, pub


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("target", choices=release.TARGETS)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory for the signed test bundle")
    args = parser.parse_args()
    if args.archive.name != release.names([args.target])[0]:
        parser.error("Archive must use the exact OpenLit 0.1.1 target name")
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copy2(args.archive, args.output / args.archive.name)
    with tempfile.TemporaryDirectory(prefix="openlit-disposable-keys-") as temporary:
        external = Path(temporary)
        key, pub = keypair(external, "qualification")
        _, wrong_pub = keypair(external, "wrong")
        release.sign(args.output, key, pub, args.commit, [args.target], True)
        # Tamper copies only, so the original signed bytes remain the smoke-test
        # input. Check rejection before the native archive can be extracted/run.
        negatives = {}
        for kind in ["archive", "manifest", "signature", "wrong_key"]:
            changed = external / kind
            shutil.copytree(args.output, changed)
            name = {"archive": args.archive.name, "manifest": "SHA256SUMS",
                    "signature": "RELEASE.json.minisig"}.get(kind)
            if name:
                path = changed / name
                data = bytearray(path.read_bytes())
                data[len(data) // 2] ^= 1
                path.write_bytes(data)
            try:
                release.verify(changed, wrong_pub if kind == "wrong_key" else pub,
                               args.commit, [args.target], True)
            except (ValueError, subprocess.CalledProcessError):
                negatives[kind] = "rejected"
            else:
                raise RuntimeError(f"Tamper check unexpectedly accepted: {kind}")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tests/qualify_release_archive.py"),
            str(args.output / args.archive.name), args.target, "--minisign-public-key", str(pub),
            "--source-commit", args.commit, "--qualification", "--single-target-candidate"],
            capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise RuntimeError(f"Native archive checks failed:\n{result.stdout}\n{result.stderr}")
        receipt = json.loads(result.stdout)
        public_copy = args.output.with_name(args.output.name + ".pub")
        # Never copy the private key into output artifacts.
        with public_copy.open("xb") as output:
            output.write(pub.read_bytes())
        receipt["qualification_public_key_sha256"] = release.digest(public_copy)
        receipt["tamper_tests"] = negatives
        receipt["publisher_identity"] = False
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
