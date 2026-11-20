"""Qualify pinned local workspaces using real concurrent Lit processes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import tempfile
from threading import Barrier
import time


def invoke(cli, cwd, *args, credentials=None, error=None):
    environment = {name: value for name, value in os.environ.items() if not name.startswith("LIT_")}
    environment.update(credentials or {})
    result = subprocess.run(
        (cli, "--json", *args), cwd=cwd, env=environment,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
    )
    if error:
        assert result.returncode and error in result.stderr, (args, result.stdout, result.stderr)
        return None
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return json.loads(result.stdout)


def checkout(cli, source, destination, base, branch=None, credentials=None, error=None):
    args = ["workspace", "create", destination, "--base", base]
    if branch is not None:
        args.extend(["--branch", branch])
    return invoke(cli, source, *args, credentials=credentials, error=error)


def file_digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def contents(root):
    snapshot = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            value = os.readlink(path)
        elif path.is_file():
            value = file_digest(path)
        else:
            value = None
        snapshot[str(path.relative_to(root))] = (stat.S_IMODE(path.lstat().st_mode), value)
    return snapshot


def save(cli, root, name, text, message, credentials=None):
    (root / name).write_text(text)
    invoke(cli, root, "add", name, credentials=credentials)
    return invoke(cli, root, "commit", "-m", message, credentials=credentials)


def git_result(root, *args, check=True):
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(
        ("git", "-c", "user.name=Workspace fixture", "-c", "user.email=fixture@example.test", *args),
        cwd=root, env=environment, check=check, capture_output=True, text=True, timeout=30,
    )


def git(root, *args):
    return git_result(root, *args).stdout.strip()


def create_git(root):
    root.mkdir(parents=True)
    git(root, "init", "--template=", "--object-format=sha1", "-b", "main")


def concurrency(cli, root, workers):
    source = root / "source"
    seed = root / "seed"
    create_git(seed)
    (seed / "runner").write_text("#!/bin/sh\nprintf fixture\n")
    (seed / "runner").chmod(0o755)
    (seed / "shared").write_text("common base\n")
    (seed / "safe-link").symlink_to("shared")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "base")
    invoke(cli, root, "clone", seed.as_uri(), source)
    base = invoke(cli, source, "status")["head"]
    latest = save(cli, source, "shared", "source advanced\n", "source advanced")["hash"]
    assert latest != base
    invoke(cli, source, "remote", "add", "source-admin", "git+https://example.test/fixture.git")
    assert invoke(cli, source, "gc")["objects_packed"] > 0
    (source / "shared").write_text("source dirty edit\n")
    (source / "untracked").write_text("never part of a committed pin\n")
    (source / "staged").write_text("source staged edit\n")
    invoke(cli, source, "add", "staged")
    before = contents(source)

    def create(number):
        branch = f"agent/{number}"
        target = root / f"attempt-{number}"
        report = checkout(cli, source, target, base, branch)
        assert report["path"] == str(target) and report["base"] == report["head"] == base
        assert report["branch"] == branch
        assert (target / "shared").read_text() == "common base\n"
        assert stat.S_IMODE(target.stat().st_mode) == 0o700
        assert (target / "runner").stat().st_mode & stat.S_IXUSR
        assert os.readlink(target / "safe-link") == "shared"
        assert invoke(cli, target, "status")["clean"]
        assert not (target / "staged").exists() and not (target / "untracked").exists()
        assert invoke(cli, target, "remote", "list")["remotes"] == []
        return target

    with ThreadPoolExecutor(max_workers=workers) as pool:
        created = list(pool.map(create, range(workers)))
    assert len({(target / "shared").stat().st_ino for target in created}) == workers

    start = Barrier(workers)

    def edit(number):
        target = created[number]
        start.wait(timeout=45)
        # Every agent edits the same path. Each also stages its own new file.
        (target / "only-mine").write_text(f"agent {number}\n")
        invoke(cli, target, "add", "only-mine")
        result = save(cli, target, "shared", f"attempt {number}\n", f"attempt {number}")
        assert result["parent"] == base, result
        info = invoke(cli, target, "workspace", "info")
        assert info["base"] == base and info["head"] == result["hash"]
        assert info["branch"] == f"agent/{number}"
        assert (target / "shared").read_text() == f"attempt {number}\n"
        assert (target / "safe-link").read_text() == f"attempt {number}\n"
        assert (target / "only-mine").read_text() == f"agent {number}\n"
        assert invoke(cli, target, "status")["clean"]
        assert invoke(cli, target, "verify")["valid"]
        return result["hash"]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        tips = list(pool.map(edit, range(workers)))
    assert len(set(tips)) == workers and contents(source) == before
    assert invoke(cli, source, "status")["head"] == latest

    # Each workspace has all the objects it needs after the source moves.
    source.rename(root / "source-retired")
    assert invoke(cli, created[0], "verify")["valid"]
    assert invoke(cli, created[-1], "workspace", "info")["head"] == tips[-1]
    return {"workers": workers, "independent_heads": len(set(tips)), "source_unchanged": True,
            "pinned_packed_history": True, "usable_without_source": True}


@contextmanager
def hold_repository(source):
    with (source / ".lit/transaction.guard").open("rb") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        yield


def wait_stage(parent, child):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if list(parent.glob(".lit-workspace-*")):
            return
        assert child.poll() is None, child.communicate()
        time.sleep(.01)
    raise AssertionError("workspace process never staged")


def publication(cli, root):
    source, working = root / "source", root / "workspaces"
    source.mkdir(parents=True)
    working.mkdir()
    invoke(cli, source, "init")
    base = save(cli, source, "base", "verified\n", "base")["hash"]
    rejected = working / "rejected"
    checkout(cli, source, rejected, base[:16], error="full Lit commit ID")
    checkout(cli, source, rejected, "a" * 192, error="Cannot read workspace base")
    blob = json.loads((source / ".lit/index").read_text())["entries"]["base"]["hash"]
    checkout(cli, source, rejected, blob, error="must identify a commit")
    checkout(cli, source, source / "nested", base, error="outside existing Lit repositories")
    assert not rejected.exists()

    seed = root / "seed"
    create_git(seed)
    (seed / "escape-link").symlink_to("../outside")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "escaping symlink")
    unsafe_source = root / "unsafe-source"
    invoke(cli, root, "clone", seed.as_uri(), unsafe_source)
    unsafe = invoke(cli, unsafe_source, "status")["head"]
    checkout(cli, unsafe_source, rejected, unsafe, error="Workspace symlink must use a relative path")
    assert not rejected.exists()

    target = working / "interrupted"
    with hold_repository(source):
        child = subprocess.Popen(
            (cli, "workspace", "create", target, "--base", base), cwd=source,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            wait_stage(working, child)
        finally:
            child.kill()
            child.communicate(timeout=15)
    assert child.returncode < 0 and not target.exists()
    assert checkout(cli, source, target, base)["base"] == base

    contender_parent = root / "competing-destination"
    contender_parent.mkdir()
    collision = contender_parent / "target"
    with hold_repository(source):
        child = subprocess.Popen(
            (cli, "workspace", "create", collision, "--base", base), cwd=source,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        wait_stage(contender_parent, child)
        collision.mkdir()
        inode = collision.stat().st_ino
    _, stderr = child.communicate(timeout=15)
    assert child.returncode and b"Cannot publish" in stderr, stderr
    assert collision.stat().st_ino == inode and not list(collision.iterdir())

    concurrent = contender_parent / "concurrent"
    command = (cli, "workspace", "create", concurrent, "--base", base)
    children = [subprocess.Popen(command, cwd=source, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    for contender in children:
        contender.communicate(timeout=30)
    assert sorted(contender.returncode for contender in children) == [0, 1]
    assert invoke(cli, concurrent, "workspace", "info")["head"] == base
    assert invoke(cli, concurrent, "verify")["valid"]
    return {"abbreviated_and_non_commit_bases_refused": True, "escaping_symlink_refused": True,
            "interruption_then_retry": True, "empty_target_race_refused": True,
            "same_destination_competing_creators": True}


def encryption(cli, root):
    source, target = root / "source", root / "workspace"
    source.mkdir(parents=True)
    invoke(cli, source, "init")
    secret = root / "passphrase"
    secret.touch(mode=0o600)
    secret.write_text("Fixture-Workspace-Encryption-7421!\n")
    key_environment = {"LIT_PASSPHRASE_FILE": str(secret)}
    (source / ".lit/encryption.toml").write_text(
        'enabled = true\nkey_file = ".lit/keys/source.key"\nfips_mode = true\n'
    )
    base = save(cli, source, "payload", "encrypted fixture\n", "encrypted base", key_environment)["hash"]
    assert invoke(cli, source, "gc", credentials=key_environment)["objects_packed"] > 0
    before = contents(source)
    checkout(cli, source, target, base, error="Passphrase required")
    assert not target.exists()
    assert checkout(cli, source, target, base, credentials=key_environment)["base"] == base
    new_key = target / ".lit/keys/workspace.key"
    assert new_key.read_bytes() != (source / ".lit/keys/source.key").read_bytes()
    assert not (target / ".lit/keys/source.key").exists()
    object_path = target / ".lit/objects" / base[:4] / base[4:]
    assert object_path.read_bytes()[0] == 1
    assert (target / ".lit/index").read_bytes()[0] == 1
    changed = save(cli, target, "payload", "workspace edit\n", "encrypted change", key_environment)
    assert changed["parent"] == base
    assert invoke(cli, target, "verify", credentials=key_environment)["valid"]
    assert invoke(cli, target, "workspace", "info", credentials=key_environment)["base"] == base
    assert contents(source) == before
    return {"independent_key": True, "encrypted_object_and_index": True,
            "credential_required": True, "independent_commit": True}


def git_handoff(cli, root):
    seed, origin, source = root / "seed", root / "protected.git", root / "source"
    path = "pkg/sub/module/input"
    create_git(seed)
    (seed / path).parent.mkdir(parents=True)
    (seed / path).write_text("base\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "Git base")
    original = git(seed, "rev-parse", "HEAD")
    origin.mkdir()
    git(origin, "init", "--bare", "--template=", "--object-format=sha1", "-b", "main")
    git(seed, "push", origin.as_uri(), "main")

    # Model a server that accepts proposal branches while protecting main.
    hook = origin / "hooks/update"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(
        '#!/bin/sh\ncase "$1" in refs/heads/agent/*) exit 0 ;; *) exit 1 ;; esac\n'
    )
    hook.chmod(0o755)
    invoke(cli, root, "clone", origin.as_uri(), source)
    pinned = invoke(cli, source, "status")["head"]
    start = Barrier(2)

    def propose(name):
        target, branch = root / name, f"agent/{name}"
        checkout(cli, source, target, pinned, branch)
        start.wait(timeout=30)
        commit = save(cli, target, path, f"{name} edits the same path\n", f"{name} proposal")
        assert commit["parent"] == pinned
        invoke(cli, target, "remote", "add", "origin", origin.as_uri())
        assert invoke(cli, target, "push", "origin", branch)["updated"]
        return target, branch

    with ThreadPoolExecutor(max_workers=2) as pool:
        proposals = list(pool.map(propose, ("alpha", "beta")))
    for target, branch in proposals:
        ref = f"refs/heads/{branch}"
        assert git(origin, "rev-parse", f"{ref}^") == original
        assert git(origin, "show", f"{ref}:{path}") == (target / path).read_text().strip()

    target, _ = proposals[0]
    invoke(cli, target, "branch", "main")
    invoke(cli, target, "push", "origin", "main", error="Git push failed")
    assert git(origin, "rev-parse", "refs/heads/main") == original
    git(origin, "fsck", "--full", "--strict")

    review = root / "review"
    git(root, "clone", origin.as_uri(), review)
    git(review, "merge", "--ff-only", "origin/agent/alpha")
    collision = git_result(review, "merge", "--no-commit", "origin/agent/beta", check=False)
    assert collision.returncode
    assert git(review, "ls-files", "-u", "--", path)
    assert git(origin, "rev-parse", "refs/heads/main") == original
    return {"proposal_branches": 2, "exact_git_parent_preserved": True,
            "protected_main_push_refused": True, "same_path_conflict_preserved": True}


def git_identity_roundtrip(cli, root):
    seed, origin, source, workspace = (root / name for name in ("seed", "origin.git", "source", "workspace"))
    create_git(seed)
    (seed / "file").write_text("initial\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "Git base")
    git(root, "clone", "--bare", seed.as_uri(), origin)
    invoke(cli, root, "clone", origin.as_uri(), source)

    pinned = save(cli, source, "file", "native publication\n", "native proposal")["hash"]
    assert invoke(cli, source, "push", "origin", "main")["updated"]
    pinned_git = git(origin, "rev-parse", "HEAD")
    future = save(cli, source, "file", "source advanced\n", "newer native change")["hash"]
    assert invoke(cli, source, "push", "origin", "main")["updated"]
    future_git = git(origin, "rev-parse", "HEAD")

    checkout(cli, source, workspace, pinned)
    copied_map = workspace / ".lit/git-export-map"
    assert (copied_map / pinned_git).read_text().strip() == pinned
    assert not (copied_map / future_git).exists()
    invoke(cli, workspace, "remote", "add", "origin", origin.as_uri())
    git(seed, "pull", "--ff-only", origin.as_uri(), "main")
    (seed / "file").write_text("Git after native publication\n")
    git(seed, "commit", "-am", "advance on Git")
    git(seed, "push", origin.as_uri(), "main")

    # Fast-forward requires the importer to recognize the original Lit ID.
    assert invoke(cli, workspace, "pull", "origin", "main")["fast_forward"]
    assert (workspace / "file").read_text() == "Git after native publication\n"
    assert invoke(cli, workspace, "workspace", "info")["base"] == pinned
    assert invoke(cli, workspace, "verify")["valid"]
    assert invoke(cli, source, "status")["head"] == future
    return {"previously_exported_base_fast_forwards": True,
            "unreachable_git_identity_excluded": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()
    if args.workers < 2:
        parser.error("at least two workers are required")
    cli = args.candidate.resolve()
    result = {"os": platform.system(), "arch": platform.machine(), "candidate_sha256": file_digest(cli)}
    with tempfile.TemporaryDirectory(prefix="lit-workspace-test-") as directory:
        root = Path(directory).resolve()
        result["concurrency"] = concurrency(cli, root / "concurrent", args.workers)
        result["publication"] = publication(cli, root / "publication")
        result["git_handoff"] = git_handoff(cli, root / "handoff")
        result["git_identity_roundtrip"] = git_identity_roundtrip(cli, root / "mapping")
        result["encryption"] = encryption(cli, root / "encryption")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
