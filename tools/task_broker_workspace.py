"""Translate committed Lit workspace changes into a broker publication request."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


REGULAR_MODES = ("100644", "100755")


def command(argv, cwd):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_LAZY_FETCH="1")
    try:
        result = subprocess.run(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=180, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Timed out running {Path(argv[0]).name}") from error
    if result.returncode:
        raise ValueError(f"{Path(argv[0]).name} {argv[1]} failed with exit {result.returncode}")
    return result.stdout


def git(repo, *args):
    return command(("git", "--no-replace-objects", *args), repo)


def run_lit(binary, repo, *args):
    return json.loads(command((binary, "--json", *args), repo))


def git_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Expected a full Git SHA-1 commit ID")
    return value


def install_source(binary, git_source, destination, expected_base):
    expected_base = git_id(expected_base)
    if git(git_source, "rev-parse", "HEAD^{commit}").decode().strip() != expected_base:
        raise ValueError("Git seed does not match the assigned base")
    destination.mkdir(mode=0o700)
    run_lit(binary, destination, "init", "--bare")
    run_lit(binary, destination, "import-git", str(git_source.resolve()))
    history = run_lit(binary, destination, "log", "--count", "1")["commits"]
    if len(history) != 1:
        raise ValueError("Lit seed has no readable base commit")
    return {"git_base": expected_base, "lit_base": history[0]["hash"]}


def proposal_from_workspace(binary, workspace, expected_base):
    expected_base = git_id(expected_base)
    pin = run_lit(binary, workspace, "workspace", "info")
    history = run_lit(binary, workspace, "log", "--count", "2")["commits"]
    if [entry["hash"] for entry in history] != [pin["head"], pin["base"]]:
        raise ValueError("Workspace must contain one proposal commit directly above its pin")

    with tempfile.TemporaryDirectory(prefix="lit-broker-export-") as directory:
        exported = Path(directory) / "candidate.git"
        run_lit(binary, workspace, "export-git", str(exported))
        lineage = git(exported, "rev-list", "--parents", "-n", "1", "HEAD").decode().split()
        if len(lineage) != 2 or lineage[1] != expected_base:
            raise ValueError("Workspace pin does not match the broker assignment")
        head = git_id(lineage[0])
        tree = git_id(git(exported, "rev-parse", "HEAD^{tree}").decode().strip())
        files = committed_changes(exported, expected_base, head)

    if not 0 < len(files) <= 32:
        raise ValueError("Broker proposals require between 1 and 32 file changes")
    return {"git_base": expected_base, "git_head": head, "git_tree": tree,
            "lit_base": pin["base"], "lit_head": pin["head"], "files": files}


def committed_changes(repo, base, head):
    raw = git(repo, "diff-tree", "--no-commit-id", "--raw", "--no-renames", "-r", "-z", base, head)
    fields = raw.split(b"\0")
    if fields.pop() != b"" or len(fields) % 2:
        raise ValueError("Malformed committed file changes")
    files = []
    for header, filename in zip(fields[::2], fields[1::2]):
        if not header.startswith(b":"):
            raise ValueError("Malformed committed file header")
        old_mode, new_mode, old_id, new_id, operation = header[1:].decode("ascii").split()
        path = filename.decode("utf-8")
        if operation == "D" and old_mode in REGULAR_MODES:
            files.append({"path": path, "delete": True})
        elif (operation in ("A", "M") and new_mode in REGULAR_MODES
              and (operation == "A" or old_mode in REGULAR_MODES)):
            file = {"path": path, "mode": new_mode}
            if old_id != new_id:
                content = git(repo, "cat-file", "blob", git_id(new_id))
                try:
                    if b"\0" in content:
                        raise ValueError("binary blob")
                    file["content"] = content.decode("utf-8")
                except ValueError:
                    file["encoding"] = "base64"
                    file["content"] = base64.b64encode(content).decode("ascii")
            files.append(file)
        else:
            raise ValueError("Broker proposals support regular files and executable modes")
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lit", type=Path, required=True)
    subcommands = parser.add_subparsers(dest="command", required=True)
    source = subcommands.add_parser("seed")
    source.add_argument("git_source", type=Path)
    source.add_argument("destination", type=Path)
    source.add_argument("--base", required=True)
    proposal = subcommands.add_parser("changes")
    proposal.add_argument("workspace", type=Path)
    proposal.add_argument("--base", required=True)
    args = parser.parse_args()
    try:
        binary = args.lit.resolve(strict=True)
        if args.command == "seed":
            result = install_source(binary, args.git_source, args.destination, args.base)
        else:
            result = proposal_from_workspace(binary, args.workspace, args.base)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
