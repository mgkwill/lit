"""Run agent-authored data fixtures against the MIT-derived Lit executable."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


BASELINES = {
    "batch-status": {"succeeded": 1, "failed": 0, "clean": True},
    "batch-unknown": {"succeeded": 0, "failed": 1, "error_code": "INVALID_BATCH_OPERATION"},
    "batch-dry-run": {"succeeded": 1, "failed": 0, "dry_run": True},
    "mcp-initialize": {"protocol_version": "2024-11-05", "server": "lit-mcp"},
    "mcp-tools": {"contains_status_tool": True, "schema_type": "object"},
    "status-modified": {"clean": False, "modified": ["tracked.txt"], "untracked": []},
    "status-untracked": {"clean": False, "modified": [], "untracked": ["new.txt"]},
    "git-text": {"content": "literal text\n", "mode": "100644"},
    "git-mode": {"content": "literal text\n", "mode": "100755"},
    "git-tag": {"tag_points_to_head": True, "first_parent_count": 0},
}

DESCRIPTIONS = {
    "batch-status": "Test a successful JSONL status operation against a clean committed repository.",
    "batch-unknown": "Test the structured error for an unsupported JSONL batch operation.",
    "batch-dry-run": "Test the dry-run reply for a valid JSONL status operation.",
    "mcp-initialize": "Test the MCP initialize response over stdio for protocol 2024-11-05.",
    "mcp-tools": "Test that MCP tools/list includes lit_status with an object input schema.",
    "status-modified": "Test status after overwriting one tracked file following its commit.",
    "status-untracked": "Test status after creating one new file in a clean committed repository.",
    "git-text": "Test bytes and ordinary file mode after import from Git and export back to Git.",
    "git-mode": "Test bytes and executable mode after import from Git and export back to Git.",
    "git-tag": "Test that a lightweight tag points to HEAD after a single-commit Git round trip.",
}


def prompt(cases):
    example = {"cases": [{"id": case, "expect": BASELINES[case]} for case in cases]}
    return ("Maintain Lit's executable contract fixtures in this directory. Use your file tools to edit both "
            "cases.json and README.md. If either exists, read it and preserve every existing test case. "
            "cases.json has one key, cases, listing objects with id and expect. Add the following entries "
            "only if absent and write or extend README.md to explain each test, its setup, and the result "
            "it protects. Include each exact case ID in the README. These are fixtures consumed by an "
            "independent executable test. Do not modify other files. "
            + " ".join(DESCRIPTIONS[case] for case in cases) + " " + json.dumps(example))


def command(argv, cwd, *, stdin=None):
    env = {name: value for name, value in os.environ.items() if not name.startswith(("GIT_", "LIT_"))}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    result = subprocess.run(argv, cwd=cwd, env=env, input=stdin, text=True,
                            capture_output=True, timeout=45)
    if result.returncode:
        raise ValueError(f"fixture command failed with exit {result.returncode}: {Path(argv[0]).name} {argv[1]}")
    return result.stdout


def observe(case, binary, root):
    def lit(*args, stdin=None):
        return command((binary, "--json", *args), root, stdin=stdin)

    if case.startswith("git-"):
        source = root / "source"
        source.mkdir()

        def git(path, *args):
            return command(("git", "-c", "user.name=Lit fixture", "-c", "user.email=fixture@example.test",
                            *args), path).strip()

        git(source, "init", "--template=", "--object-format=sha1", "-b", "main")
        entry = source / "tracked.txt"
        entry.write_text("literal text\n")
        if case == "git-mode":
            entry.chmod(0o755)
        git(source, "add", "tracked.txt")
        git(source, "commit", "-m", "fixture")
        git(source, "tag", "fixture")
        lit("init", "--bare")
        lit("import-git", str(source))
        destination = root / "export"
        lit("export-git", str(destination))
        if case == "git-tag":
            parents = git(destination, "rev-list", "--parents", "-n", "1", "HEAD").split()
            return {"tag_points_to_head": git(destination, "rev-parse", "refs/tags/fixture") == parents[0],
                    "first_parent_count": len(parents) - 1}
        return {"content": command(("git", "show", "HEAD:tracked.txt"), destination),
                "mode": git(destination, "ls-tree", "HEAD", "--", "tracked.txt").split()[0]}

    lit("init")
    tracked = root / "tracked.txt"
    tracked.write_text("original\n")
    lit("add", "tracked.txt")
    lit("commit", "-m", "fixture")
    if case.startswith("status-"):
        if case == "status-modified":
            tracked.write_text("changed\n")
        else:
            (root / "new.txt").write_text("new\n")
        result = json.loads(lit("status"))
        return {key: result[key] for key in ("clean", "modified", "untracked")}
    if case.startswith("mcp-"):
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "lit-test", "version": "1"}}}
        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        messages = [json.loads(line) for line in lit("mcp-serve", stdin="\n".join(map(json.dumps, (request, listing))) + "\n").splitlines()]
        responses = {msg["id"]: msg["result"] for msg in messages}
        if case == "mcp-initialize":
            return {"protocol_version": responses[1]["protocolVersion"], "server": responses[1]["serverInfo"]["name"]}
        tool, = (t for t in responses[2]["tools"] if t["name"] == "lit_status")
        return {"contains_status_tool": True, "schema_type": tool["inputSchema"]["type"]}

    verb = "does-not-exist" if case == "batch-unknown" else "status"
    options = ("--dry-run",) if case == "batch-dry-run" else ()
    response = json.loads(lit("batch", *options, stdin=json.dumps({"command": verb, "args": {}}) + "\n"))
    result, = response["results"]
    counts = {key: response[key] for key in ("succeeded", "failed")}
    if case == "batch-unknown":
        return counts | {"error_code": result["error_code"]}
    field = "dry_run" if options else "clean"
    return counts | {field: result["result"][field]}


def verify(binary, scope, selected):
    files = {path.name: path for path in scope.iterdir()}
    if set(files) != {"README.md", "cases.json"} or any(path.is_symlink() or not path.is_file()
                                                     or path.stat().st_size > 12_000 for path in files.values()):
        raise ValueError("expected two small contract files")
    manifest = json.loads(files["cases.json"].read_bytes())
    if not isinstance(manifest, dict) or set(manifest) != {"cases"} or not isinstance(manifest["cases"], list):
        raise ValueError("cases.json must be an object containing a cases array")
    entries = manifest["cases"]
    if any(not isinstance(entry, dict) or set(entry) != {"id", "expect"} or not isinstance(entry["id"], str)
           for entry in entries):
        raise ValueError("each case must contain a string id and an expect value")
    if sorted(entry["id"] for entry in entries) != sorted(set(selected)):
        raise ValueError("fixture must retain all assigned case IDs exactly once")
    text = files["README.md"].read_text()
    if len(text.strip()) < 90 or any(case not in text for case in selected):
        raise ValueError("README must explain the assigned cases")
    for entry in entries:
        case = entry["id"]
        expected = json.dumps(entry["expect"], sort_keys=True)
        if expected != json.dumps(BASELINES[case], sort_keys=True):
            raise ValueError(f"fixture expectation differs for {case}")
        with tempfile.TemporaryDirectory(prefix="lit-contract-") as work:
            actual = observe(case, binary, Path(work).resolve())
        if json.dumps(actual, sort_keys=True) != expected:
            raise ValueError(f"runtime contract differs for {case}")
    return len(entries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("scope", type=Path)
    parser.add_argument("case", nargs="+", choices=BASELINES)
    args = parser.parse_args()
    print(verify(args.binary.resolve(strict=True), args.scope, args.case))
