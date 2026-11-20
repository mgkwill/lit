"""Exercise batch rollback and MCP through an unpacked Lit release binary."""

import argparse
import hashlib
import http.client
import json
from pathlib import Path
import platform
import socket
import subprocess
from tempfile import TemporaryDirectory
import time

from remote_regression import Fixture


def batch(fixture):
    repo = fixture.make("batch")
    fixture.lit(repo, "init", ".")
    tracked = repo / "tracked"
    tracked.write_text("base\n")
    fixture.lit(repo, "add", tracked.name)
    base = fixture.lit(repo, "commit", "-m", "base")["hash"]
    index = repo / ".lit/index"
    initial_index = index.read_bytes()
    journal = repo / ".lit/transaction.json"
    tracked.write_text("pending edit\n")
    extra = repo / "untracked"
    extra.write_bytes(b"preserve\x00this\n")

    def run(*ops, options=(), exit_code=0):
        result = subprocess.run(
            [fixture.binary, "batch", *options], cwd=repo, env=fixture.env,
            input="".join(json.dumps(op) + "\n" for op in ops),
            capture_output=True, text=True, timeout=25,
        )
        assert result.returncode == exit_code, (result.returncode, result.stdout, result.stderr)
        return json.loads(result.stdout)

    add = {"command": "add", "args": {"files": [tracked.name]}}
    commit = {"op": "commit", "message": "provisional"}
    invalid = {"command": "add", "args": {"files": [42]}}
    dry_run = run(add, invalid, options=("--dry-run", "--fail-on-error"), exit_code=1)
    assert dry_run["failed"] == 1 and dry_run["results"][1]["error_code"] == "INVALID_BATCH_OPERATION"
    assert fixture.lit(repo, "status")["head"] == base and index.read_bytes() == initial_index
    assert not journal.exists()

    missing = {"op": "add", "files": ["missing"]}
    failure = run(add, commit, missing, {"command": "status"}, options=("--rollback-on-error",), exit_code=1)
    assert failure["transaction"] == "rolled_back" and failure["succeeded"] == 0
    assert [row["status"] for row in failure["results"]] == ["rolled_back", "rolled_back", "error", "skipped"]
    assert fixture.lit(repo, "status")["head"] == base and index.read_bytes() == initial_index
    assert tracked.read_text() == "pending edit\n" and extra.read_bytes() == b"preserve\x00this\n"
    assert not journal.exists()

    retry = run(add, commit, options=("--rollback-on-error",))
    assert retry["transaction"] == "committed"
    status = fixture.lit(repo, "status")
    assert status["head"] != base and status["head"] == retry["results"][1]["result"]["hash"]
    assert status["staged"] == [] and status["modified"] == [] and status["untracked"] == [extra.name]
    assert not journal.exists()
    return {"dry_run_reports_invalid_line": True, "rollback_restores_head_and_index": True,
            "working_bytes_preserved": True, "retry_committed": True}


def tool_call(request_id, name, **arguments):
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}


def mcp_requests(filename, before, after):
    return [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "lit-release-smoke", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        tool_call("diff", "lit_diff", ref1=before, ref2=after),
        tool_call("branch", "lit_checkout", target="from-mcp", create=True),
        tool_call("legacy-branch", "lit_checkout", target="legacy-mcp", b=True),
        tool_call("add", "lit_add", files=[filename]),
        tool_call("stash", "lit_stash", action="list"),
        tool_call("legacy-stash", "lit_stash", command="list"),
        tool_call("config", "lit_config", command="get", key="airgap.enabled"),
        tool_call("schema", "lit_schema", command="add"),
        tool_call("invalid-action", "lit_stash", action="typo"),
        tool_call("unknown-tool", "unknown"),
        {"jsonrpc": "2.0", "id": "alive", "method": "ping"},
    ]


def mcp_http(fixture, repo, requests):
    with socket.socket() as free_port:
        free_port.bind(("127.0.0.1", 0))
        port = free_port.getsockname()[1]
    with subprocess.Popen(
        [fixture.binary, "mcp-serve", "--port", str(port)], cwd=repo, env=fixture.env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as server:
        try:
            deadline = time.monotonic() + 5
            while True:
                assert server.poll() is None, server.communicate()
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                        break
                except ConnectionRefusedError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.025)
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            responses = []
            try:
                for request in requests:
                    connection.request("POST", "/", json.dumps(request), {"Content-Type": "application/json"})
                    response = connection.getresponse()
                    body = response.read()
                    if "id" in request:
                        assert response.status == 200, (response.status, body)
                        responses.append(json.loads(body))
                    else:
                        assert response.status == 204 and body == b"", (response.status, body)
            finally:
                connection.close()
            return responses
        finally:
            server.terminate()
            try:
                server.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.communicate()


def mcp(fixture, transport):
    repo = fixture.make("mcp-" + transport)
    fixture.lit(repo, "init", ".")
    tracked = repo / "history"
    tracked.write_text("base\n")
    before = fixture.lit(repo, "snapshot", "-m", "base")["hash"]
    tracked.write_text("next\n")
    after = fixture.lit(repo, "snapshot", "-m", "next")["hash"]
    path = repo / "from-tool"
    path.write_text("MCP fixture\n")
    requests = mcp_requests(path.name, before, after)
    if transport == "stdio":
        child = subprocess.run(
            [fixture.binary, "mcp-serve", "--stdio"], cwd=repo, env=fixture.env,
            input="".join(json.dumps(request) + "\n" for request in requests),
            capture_output=True, text=True, timeout=25, check=True,
        )
        messages = [json.loads(line) for line in child.stdout.splitlines()]
    else:
        messages = mcp_http(fixture, repo, requests)
    assert [response["id"] for response in messages] == [request["id"] for request in requests if "id" in request]
    assert all(response["jsonrpc"] == "2.0" for response in messages)
    assert messages[0]["result"]["protocolVersion"] == "2024-11-05"
    schemas = {tool["name"]: tool["inputSchema"]["properties"] for tool in messages[1]["result"]["tools"]}
    files = schemas["lit_add"]["files"]
    assert files["type"] == "array" and files["items"] == {"type": "string"} and files["minItems"] == 1
    assert schemas["lit_snapshot"]["metadata"]["type"] == "object"
    assert schemas["lit_checkout"]["create"]["default"] is False
    assert schemas["lit_log"]["count"]["default"] == 10
    assert schemas["lit_stash"]["action"]["default"] == "push"
    assert schemas["lit_config"]["action"]["default"] == "show"
    assert schemas["lit_schema"]["command"]["type"] == "string"

    replies = {message["id"]: message["result"] for message in messages[2:]}

    def content(key):
        result = replies[key]
        assert not result.get("isError"), result
        return json.loads(result["content"][0]["text"])

    assert content("add")["files_added"] == 1
    assert content("diff")["files_changed"] == 1
    assert content("branch")["is_new_branch"] and content("legacy-branch")["is_new_branch"]
    assert content("stash") == content("legacy-stash")
    assert content("config")["key"] == "airgap.enabled"
    assert content("schema")["input"]["properties"]["files"]["type"] == "array"
    assert replies["invalid-action"]["isError"] and replies["unknown-tool"]["isError"]
    assert "alive" in replies
    status = fixture.lit(repo, "status")
    assert status["staged"] == [path.name] and status["branch"] == "legacy-mcp"
    return {"notification_silent": True, "tool_discovery_and_staged_write": True, "invalid_tool_and_ping": True,
            "ref_diff_and_branch_creation": True, "legacy_arguments_preserved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    candidate = args.candidate.resolve(strict=True)
    with candidate.open("rb") as binary:
        digest = hashlib.file_digest(binary, "sha256").hexdigest()
    with TemporaryDirectory(prefix="lit-release-smoke-") as folder:
        root = Path(folder).resolve()
        fixture = Fixture(root, candidate)
        result = {"platform": platform.system(), "arch": platform.machine(), "candidate_sha256": digest,
                  "version": fixture.run(root, candidate, "--version").stdout.strip(),
                  "batch": batch(fixture), "mcp": {name: mcp(fixture, name) for name in ("stdio", "http")}}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
