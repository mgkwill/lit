"""Exercise the assignment-to-workspace-to-broker payload contract."""
import argparse
import base64
from contextlib import redirect_stderr, redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from workspace_regression import create_git, git

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from task_broker_workspace import committed_changes, install_source, proposal_from_workspace, run_lit


def exercise(binary, root):
    seed = root / "git"
    create_git(seed)
    (seed / "mode").write_text("same bytes\n")
    (seed / "remove").write_text("previous bytes\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "base")
    base = git(seed, "rev-parse", "HEAD")
    source, workspace = root / "source", root / "workspace"
    try:
        install_source(binary, seed, source, "a" * 40)
    except ValueError as error:
        assert "does not match" in str(error) and not source.exists()
    else:
        raise AssertionError("Wrong Git seed succeeded")
    pin = install_source(binary, seed, source, base)
    run_lit(binary, source, "workspace", "create", str(workspace), "--base", pin["lit_base"])
    (workspace / "nested").mkdir()
    (workspace / "nested/binary").write_bytes(b"\0\xff")
    (workspace / "mode").chmod(0o755)
    run_lit(binary, workspace, "add", "mode", "nested/binary")
    commit = run_lit(binary, workspace, "commit", "-m", "proposal")
    (workspace / "not-committed").write_text("ignored")
    result = proposal_from_workspace(binary, workspace, base)
    assert result["lit_head"] == commit["hash"] and result["lit_base"] == pin["lit_base"]
    assert result["files"] == [
        {"path": "mode", "mode": "100755"},
        {"path": "nested/binary", "mode": "100644", "encoding": "base64",
         "content": base64.b64encode(b"\0\xff").decode()},
    ]
    for wrong, msg in (("a" * 40, "does not match"), ("HEAD", "full Git SHA-1")):
        try:
            proposal_from_workspace(binary, workspace, wrong)
        except ValueError as error:
            assert msg in str(error)
        else:
            raise AssertionError("Wrong proposal base accepted")
    run_lit(binary, workspace, "commit", "-m", "second")
    try:
        proposal_from_workspace(binary, workspace, base)
    except ValueError as error:
        assert "one proposal commit" in str(error)
    else:
        raise AssertionError("Multiple commits accepted")

    git(seed, "rm", "remove")
    git(seed, "commit", "-m", "delete")
    assert committed_changes(seed, base, git(seed, "rev-parse", "HEAD")) == [{"path": "remove", "delete": True}]


def exercise_partial_burst():
    fixture = Path(__file__).parent / "fixtures/github-merge-queue/broker-burst-worker.py"
    spec = spec_from_file_location("broker_burst_worker", fixture)
    worker = module_from_spec(spec)
    spec.loader.exec_module(worker)

    def publish(operation, *_):
        if operation["id"] == "rejected":
            raise RuntimeError("private error details")
        return {"id": operation["id"], "receipt": {"number": 1}}

    argv = ["broker-burst-worker", "--host", "host-a", "--profiles", ".", "publish"]
    operations = io.StringIO(json.dumps([{"id": "rejected"}, {"id": "published"}]))
    with (patch.object(worker.socket, "gethostname", return_value="host-a"),
          patch.object(worker.sys, "argv", argv), patch.object(worker.sys, "stdin", operations),
          patch.object(worker, "run", side_effect=publish),
          redirect_stdout(io.StringIO()) as receipts, redirect_stderr(io.StringIO()) as failures):
        assert worker.main() == 1
    assert json.loads(receipts.getvalue()) == {"id": "published", "receipt": {"number": 1}}
    assert json.loads(failures.getvalue()) == {"worker": "rejected", "error": "RuntimeError"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="lit-broker-workspace-") as folder:
        exercise(args.candidate.resolve(strict=True), Path(folder).resolve())
    exercise_partial_burst()
    print("Pinned base, committed-only changes, file operations, and partial burst receipts verified")


if __name__ == "__main__":
    main()
