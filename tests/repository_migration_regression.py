#!/usr/bin/env python3
"""Exercise native migration using repositories produced by the actual binaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import stat
import subprocess
import tempfile
import time


def invoke(binary, cwd, *args, credentials=None, error=None):
    env = {name: value for name, value in os.environ.items() if not name.startswith("LIT_")}
    env.update(credentials or {})
    result = subprocess.run(
        (binary, "--json", *args), cwd=cwd, env=env, text=True,
        stdin=subprocess.DEVNULL, capture_output=True, timeout=120, check=False,
    )
    if error:
        assert result.returncode and error in result.stderr, (args, result.stdout, result.stderr)
        return
    assert not result.returncode, (binary, args, result.stdout, result.stderr)
    data = json.loads(result.stdout)
    assert data["status"] == "ok", data
    return data


def sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def tree(root):
    result = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(directory) / name
            mode = stat.S_IMODE(path.lstat().st_mode)
            if path.is_symlink():
                contents = os.readlink(path)
            else:
                contents = sha256(path) if path.is_file() else None
            result[str(path.relative_to(root))] = (mode, contents)
    return result


def history(binary, repo, credentials=None):
    return [entry["hash"] for entry in invoke(binary, repo, "log", credentials=credentials)["commits"]]


def seed(binary, repository, *, credentials=None):
    (repository / "message").write_text("first revision\n")
    (repository / "script").write_text("#!/bin/sh\nprintf fixture\n")
    (repository / "script").chmod(0o755)
    invoke(binary, repository, "add", "message", "script", credentials=credentials)
    return invoke(binary, repository, "commit", "-m", "first", credentials=credentials)["hash"]


def verify(binary, repository, credentials=None):
    result = invoke(binary, repository, "verify", credentials=credentials)
    assert result["valid"], result
    return result


def exercise_worktree(root, maker, candidate, packed):
    source, destination, archive = (root / name for name in ("original", "migrated", "archive"))
    source.mkdir(parents=True)
    invoke(maker, source, "init")
    first = seed(maker, source)
    invoke(maker, source, "branch", "side")
    invoke(maker, source, "tag", "-a", "-m", "fixture tag", "marker")
    invoke(maker, source, "swarm", "register", "fixture-agent")
    invoke(maker, source, "swarm", "lease-acquire", "--agent", "fixture-agent", "--path", "message")
    invoke(maker, source, "lfs", "track", "*.payload")
    payload = bytes(range(256)) * 128
    (source / "data.payload").write_bytes(payload)
    assert invoke(maker, source, "lfs", "migrate", "--threshold", "1000000")["files_migrated"] == 1
    pointer = (source / "data.payload").read_text()
    assert pointer.startswith("version https://lit-lfs.spec/v1\n"), pointer
    oid = pointer.splitlines()[1].split(":")[1]
    local_payload = Path(".lit/lfs/objects") / oid[:4] / oid[4:]
    assert (source / local_payload).read_bytes() == payload
    (source / "message").write_text("second revision\n")
    invoke(maker, source, "add", "message", "data.payload", ".litattributes")
    second = invoke(maker, source, "commit", "-m", "second")["hash"]

    # The tested CLI operations can leave the reflog empty. Seed a record in
    # the MIT-derived schema, then verify that the fixture producer reads it.
    reflog = source / ".lit/reflog/main"
    reflog.parent.mkdir(exist_ok=True)
    reflog.write_text(json.dumps({"entries": [{
        "old_hash": first, "new_hash": second, "action": "commit", "message": "second",
        "timestamp": 1700000000,
    }]}))
    original_reflog = invoke(maker, source, "reflog")["entries"]
    assert original_reflog and second.startswith(original_reflog[0]["new_hash"]), original_reflog
    if packed:
        gc = invoke(maker, source, "gc")
        assert gc["objects_packed"] > 0, gc
        assert list((source / ".lit/packs").glob("*.pack"))

    (source / "message").write_text("dirty worktree\n")
    (source / "staged").write_text("staged without commit\n")
    invoke(maker, source, "add", "staged")
    (source / "untracked").write_bytes(b"\x00untracked\xff")
    (source / "relative-link").symlink_to("message")
    (source / "external-link").symlink_to(root / "outside")
    (root / "outside").write_text("external canary")
    before = tree(source)
    inspected = invoke(candidate, root, "migrate-repo", "inspect", source)
    assert inspected["verification"]["valid"] and tree(source) == before
    copied = invoke(candidate, root, "migrate-repo", "copy", source, destination)
    assert copied["source_sha256"] == inspected["source_sha256"]
    assert copied["format_version"] == 1 and not copied["key_rotated"], copied
    assert copied["verification"]["refs_checked"] == 3, copied
    after = tree(destination)
    marker = after.pop(".lit/repository-format.json")
    assert marker[0] == 0o644 and before == after == tree(source)
    assert verify(candidate, destination)["objects_checked"] == copied["verification"]["objects_checked"]
    assert history(candidate, destination) == [second, first]
    assert invoke(candidate, destination, "reflog")["entries"] == original_reflog
    assert invoke(candidate, destination, "swarm", "list")["details"]["agents"][0]["agent_id"] == "fixture-agent"
    assert (destination / local_payload).read_bytes() == payload
    assert (destination / "data.payload").read_text() == pointer
    assert invoke(candidate, destination, "status")["head"] == second

    invoke(candidate, root, "migrate-repo", "copy", source, destination, error="Destination already exists")
    (destination / "post-migration").write_text("keep this on rollback\n")
    invoke(candidate, destination, "add", "post-migration")
    new = invoke(candidate, destination, "commit", "-m", "after migration")["hash"]
    archived = tree(destination)
    (source / "untracked").write_bytes(b"changed")
    invoke(
        candidate, root, "migrate-repo", "rollback", source, destination, archive,
        error="Original repository differs",
    )
    assert tree(destination) == archived and not archive.exists()
    (source / "untracked").write_bytes(b"\x00untracked\xff")
    archive.mkdir()
    invoke(candidate, root, "migrate-repo", "rollback", source, destination, archive, error="Destination already exists")
    archive.rmdir()
    invoke(candidate, root, "migrate-repo", "rollback", source, destination, archive)
    assert not destination.exists() and tree(archive) == archived and tree(source) == before
    assert history(maker, source) == [second, first]
    assert history(candidate, archive)[0] == new
    return {"objects": copied["verification"]["objects_checked"], "refs": copied["verification"]["refs_checked"]}


def exercise_bare(root, maker, candidate):
    source, working, destination, archive = (root / name for name in ("bare", "working", "copy", "archive"))
    root.mkdir()
    source.mkdir()
    working.mkdir()
    invoke(maker, source, "init", "--bare")
    invoke(candidate, working, "init")
    tip = seed(candidate, working)
    invoke(candidate, working, "remote", "add", "bare", source.as_uri())
    invoke(candidate, working, "push", "bare", "main")
    before = tree(source)
    report = invoke(candidate, root, "migrate-repo", "copy", source, destination)
    assert report["verification"]["objects_checked"] == 4
    assert (destination / ".lit/refs/heads/main").read_text().strip() == tip
    assert (destination / ".lit/config").read_bytes() == (source / ".lit/config").read_bytes()
    clone = root / "clone"
    invoke(candidate, root, "clone", destination.as_uri(), clone)
    assert history(candidate, clone) == [tip]
    invoke(candidate, root, "migrate-repo", "rollback", source, destination, archive)
    assert tree(source) == before and verify(candidate, archive)["valid"]
    return report["verification"]["objects_checked"]


def exercise_encryption(root, maker, candidate, released, packed):
    source, destination, archive = (root / name for name in ("original", "copy", "archive"))
    source.mkdir(parents=True)
    old_file = root / "old"
    new_file = root / "new"
    wrong_file = root / "wrong"
    for path, text in [
        (old_file, "Fixture-Old-Migration-Key-0123!\n"),
        (new_file, "Fixture-New-Migration-Key-7654!\n"),
        (wrong_file, "Fixture-Bad-Migration-Key-9999!\n"),
    ]:
        path.touch(mode=0o600)
        path.write_text(text)
    old = {"LIT_PASSPHRASE_FILE": str(old_file)}
    new = {"LIT_PASSPHRASE_FILE": str(new_file)}
    rotate = dict(old, LIT_NEW_PASSPHRASE_FILE=str(new_file))
    invoke(maker, source, "init")
    if released:
        original_tip = seed(maker, source)
        invoke(maker, source, "branch", "side")
        invoke(maker, source, "tag", "-a", "-m", "encrypted annotated tag", "marker")
    (source / ".lit/encryption.toml").write_text(
        'enabled = true\nkey_file = ".lit/keys/fixture.key"\nfips_mode = true\n'
    )
    if released:
        migration = invoke(maker, source, "migrate-encryption", credentials=old)
        assert migration["index_encrypted"], migration
        packed_refs = source / ".lit/refs.enc"
        assert packed_refs.read_bytes()[0] == 1
        invoke(candidate, source, "status", credentials=old, error="Packed encrypted refs require")
    else:
        original_tip = seed(maker, source, credentials=old)
    original_history = history(maker, source, old)
    assert original_history == [original_tip], original_history
    if packed:
        assert invoke(maker, source, "gc", credentials=old)["objects_packed"] > 0
        pack = next((source / ".lit/packs").glob("*.pack"))
        with pack.open("rb") as file:
            assert file.read(8) == b"LITP\0\0\0" + bytes([1 if released else 2])
    before = tree(source)
    if released:
        invoke(candidate, root, "migrate-repo", "copy", source, destination, credentials=old, error="requires rotation on the copy")
        assert not destination.exists() and tree(source) == before
    invoke(
        candidate, root, "migrate-repo", "copy", source, destination,
        credentials=dict(rotate, LIT_PASSPHRASE_FILE=str(wrong_file)), error="Invalid passphrase",
    )
    assert not destination.exists() and tree(source) == before
    # The correct passphrase is also delayed after a failed guess.
    time.sleep(2.1)
    inspected = invoke(candidate, root, "migrate-repo", "inspect", source, credentials=old)
    report = invoke(candidate, root, "migrate-repo", "copy", source, destination, credentials=rotate)
    assert report["key_rotated"] == inspected["key_rotation_required"] == released, report
    assert not inspected["key_rotated"], inspected
    assert report["source_sha256"] == inspected["source_sha256"]
    assert tree(source) == before
    active = new if released else old
    migrated_history = history(candidate, destination, active)
    assert migrated_history == original_history, (migrated_history, original_history, report)
    verified = verify(candidate, destination, active)
    assert verified["objects_checked"] == (5 if released else 4), verified
    assert verified["refs_checked"] == (3 if released else 1), verified
    if released:
        assert not (destination / ".lit/refs.enc").exists()
        for name in ("heads/main", "heads/side", "tags/marker"):
            assert (destination / ".lit/refs" / name).read_bytes()[0] == 1
    if packed:
        pack = next((destination / ".lit/packs").glob("*.pack"))
        with pack.open("rb") as file:
            assert file.read(8) == b"LITP\0\0\0\x02"
    (destination / "post").write_text("post-migration encrypted write")
    invoke(candidate, destination, "add", "post", credentials=active)
    tip = invoke(candidate, destination, "commit", "-m", "new", credentials=active)["hash"]
    invoke(candidate, root, "migrate-repo", "rollback", source, destination, archive)
    assert tree(source) == before and history(candidate, archive, active)[0] == tip
    assert history(maker, source, old) == original_history
    if released:
        invoke(candidate, archive, "status", credentials=old, error="Invalid passphrase")
        target = source / ".lit/refs/heads/main"
        target.parent.mkdir(exist_ok=True, parents=True)
        assert not target.exists()
        target.write_text("f" * 192)
        invoke(candidate, root, "migrate-repo", "inspect", source, credentials=old, error="Conflicting loose and encrypted packed refs")
        target.unlink()
        original_table = packed_refs.read_bytes()
        packed_refs.write_bytes(original_table[:-1] + bytes([original_table[-1] ^ 1]))
        invoke(candidate, root, "migrate-repo", "inspect", source, credentials=old, error="Decryption failed")
        packed_refs.write_bytes(original_table)
        assert tree(source) == before
    return {"old_key_preserved_at_source": True, "key_rotated_on_copy": released, "packed": packed}


def exercise_interruption(root, reference, candidate):
    source, destination = root / "source", root / "destination"
    source.mkdir(parents=True)
    invoke(reference, source, "init")
    tip = seed(reference, source)
    with (source / "zz-large").open("wb") as file:
        file.truncate(128 * 1024 * 1024)
    before = tree(source)
    command = [candidate, "migrate-repo", "copy", source, destination]

    def start_copy(previous_inode=None):
        child = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                for path in root.glob(".lit-migration-*/repository/zz-large"):
                    try:
                        inode = path.stat().st_ino
                    except FileNotFoundError:
                        continue
                    if inode != previous_inode and not destination.exists():
                        child.send_signal(signal.SIGSTOP)
                        return child
                assert child.poll() is None, child.communicate()
                time.sleep(0.005)
            raise AssertionError("copy never reached staged file")
        except BaseException:
            child.kill()
            child.communicate()
            raise

    child = start_copy()
    invoke(candidate, root, "migrate-repo", "copy", source, destination, error="busy or cannot be locked")
    child.kill()
    child.communicate(timeout=10)
    assert child.returncode < 0 and not destination.exists() and tree(source) == before

    partial = next(root.glob(".lit-migration-*/repository/zz-large"))
    with partial.open("rb") as held_file:
        child = start_copy(os.fstat(held_file.fileno()).st_ino)
    destination.mkdir()
    occupied_inode = destination.stat().st_ino
    child.send_signal(signal.SIGCONT)
    _, stderr = child.communicate(timeout=120)
    assert child.returncode and b"Cannot publish" in stderr, stderr
    assert destination.stat().st_ino == occupied_inode and not list(destination.iterdir())
    destination.rmdir()
    invoke(candidate, root, "migrate-repo", "copy", source, destination)
    assert history(candidate, destination) == [tip]
    assert sha256(source / "zz-large") == sha256(destination / "zz-large")
    assert tree(source) == before and not list(root.glob(".lit-migration-*"))
    return {"killed_before_publish": True, "competing_migration": True, "no_replace_race": True, "resume": True}


def exercise_denials(root, reference, candidate):
    source, destination = root / "source", root / "destination"
    source.mkdir(parents=True)
    invoke(reference, source, "init")
    tip = seed(reference, source)
    invoke(candidate, root, "migrate-repo", "copy", source, source / "nested", error="must be separate")
    active = source / ".lit/transaction.json"
    active.write_text("{}")
    invoke(candidate, root, "migrate-repo", "inspect", source, error="active operation")
    active.unlink()
    link = source / ".lit/link"
    link.symlink_to(root / "outside")
    invoke(candidate, root, "migrate-repo", "copy", source, destination, error="Unsupported migration entry")
    link.unlink()
    object_path = source / ".lit/objects" / tip[:4] / tip[4:]
    saved = object_path.read_bytes()
    object_path.write_bytes(b"corruption")
    invoke(candidate, root, "migrate-repo", "inspect", source, error="object:")
    object_path.write_bytes(saved)
    invoke(candidate, root, "migrate-repo", "copy", source, destination)
    marker = destination / ".lit/repository-format.json"
    receipt = json.loads(marker.read_text())
    receipt["version"] = 999
    marker.write_text(json.dumps(receipt))
    blocked = tree(destination)
    invoke(candidate, destination, "status", error="Unsupported Lit repository format 999")
    invoke(candidate, destination, "add", "message", error="Unsupported Lit repository format 999")
    invoke(candidate, root, "clone", destination.as_uri(), root / "clone", error="Unsupported Lit repository format 999")
    invoke(candidate, root, "migrate-repo", "inspect", destination, error="Unsupported Lit repository format 999")
    assert tree(destination) == blocked
    return {"corrupt_object": True, "metadata_symlink": True, "future_format": True, "active_transaction": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("reference", type=Path)
    args = parser.parse_args()
    candidate, reference = args.candidate.resolve(), args.reference.resolve()
    version = subprocess.check_output([reference, "--version"], text=True).strip()
    assert version == "lit 1.8.0", version
    results = {
        "platform": platform.system(), "arch": platform.machine(),
        "candidate_sha256": sha256(candidate), "reference_sha256": sha256(reference),
        "reference_version": version,
    }
    with tempfile.TemporaryDirectory(prefix="lit-repo-migration-") as path:
        root = Path(path).resolve()
        results["working"] = {
            "released_loose": exercise_worktree(root / "released-loose", reference, candidate, False),
            "released_packed": exercise_worktree(root / "released-packed", reference, candidate, True),
            "recovery_packed": exercise_worktree(root / "recovery-packed", candidate, candidate, True),
        }
        results["bare"] = {
            "released": exercise_bare(root / "bare-release", reference, candidate),
            "recovery": exercise_bare(root / "bare-recovery", candidate, candidate),
        }
        results["encrypted"] = {
            "released_loose": exercise_encryption(root / "crypto-release", reference, candidate, True, False),
            "released_packed": exercise_encryption(root / "crypto-release-pack", reference, candidate, True, True),
            "recovery_packed": exercise_encryption(root / "crypto-recovery", candidate, candidate, False, True),
        }
        results["interruption"] = exercise_interruption(root / "interrupted", reference, candidate)
        results["denials"] = exercise_denials(root / "denials", reference, candidate)
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
