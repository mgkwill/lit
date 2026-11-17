"""Run an isolated Git-backed agent workflow against a recovery build of Lit."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Barrier

MAIN = "refs/heads/main"


@dataclass(frozen=True)
class Submission:
    task: str
    attempt: str
    oid: str

    @property
    def claim(self):
        return f"refs/lit/claims/{self.task}"

    @property
    def ref(self):
        return f"refs/lit/submissions/{self.task}/{self.attempt}"

    @property
    def receipt(self):
        return f"refs/lit/receipts/{self.task}"


class Workflow:
    def __init__(self, root, lit):
        self.root = root
        self.lit = lit
        self.repository = root / "queue.git"
        home = root / "home"
        home.mkdir()
        self.env = {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PATH": os.environ["PATH"],
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Workflow",
            "GIT_AUTHOR_EMAIL": "workflow@example.test",
            "GIT_COMMITTER_NAME": "Workflow",
            "GIT_COMMITTER_EMAIL": "workflow@example.test",
            "GIT_TERMINAL_PROMPT": "0",
        }
        seed = root / "seed"
        seed.mkdir()
        self.git("init", "-q", "--object-format=sha1", "-b", "main", cwd=seed)
        (seed / "hotspot").write_text("original\n")
        (seed / "changes").mkdir()
        (seed / "changes" / ".keep").touch()
        self.git("add", ".", cwd=seed)
        self.git("commit", "-qm", "initial", cwd=seed)
        self.git("clone", "-q", "--bare", str(seed), str(self.repository), cwd=root)
        self.base = self.ref(MAIN)

    def run(self, binary, *args, cwd, data=None, succeeds=True):
        result = subprocess.run(
            [binary, *args], cwd=cwd, env=self.env, input=data, capture_output=True,
            text=True, timeout=120,
        )
        assert (result.returncode == 0) == succeeds, (args, result.stdout, result.stderr)
        return result.stdout.strip(), result.stderr

    def git(self, *args, cwd=None, **kwargs):
        return self.run("git", *args, cwd=cwd or self.repository, **kwargs)[0]

    def ref(self, ref):
        return self.git("rev-parse", "--verify", ref)

    def assert_absent(self, ref):
        self.git("rev-parse", "--verify", "--quiet", ref, succeeds=False)

    def refs(self, *commands, rejected_ref=None):
        request = "\n".join(("start", *commands, "prepare", "commit", ""))
        _, error = self.run("git", "update-ref", "--stdin", cwd=self.repository,
                            data=request, succeeds=rejected_ref is None)
        if rejected_ref is not None:
            assert f"cannot lock ref '{rejected_ref}'" in error, error

    def attempt(self, value):
        return self.git("hash-object", "-w", "--stdin", data=value)

    def worktree(self, name, base):
        destination = self.root / name
        self.git("worktree", "add", "--quiet", "--detach", str(destination), base)
        return destination

    def prepare_worker(self, task, attempt):
        workspace = self.worktree(f"work-{task}-{attempt[:12]}", self.base)
        assert self.git("rev-parse", "HEAD", cwd=workspace) == self.base
        return workspace

    def propose(self, task, attempt, path, value, workspace, barrier=None):
        if barrier:
            barrier.wait(timeout=120)
        (workspace / path).write_text(value)
        self.git("add", "--", path, cwd=workspace)
        self.git("commit", "-qm", f"{task} attempt {attempt[:12]}", cwd=workspace)
        proposal = Submission(task, attempt, self.git("rev-parse", "HEAD", cwd=workspace))
        self.submit(proposal)
        return proposal

    def submit(self, proposal, rejected_ref=None):
        self.refs(
            f"verify {proposal.claim} {proposal.attempt}",
            f"create {proposal.ref} {proposal.oid}",
            rejected_ref=rejected_ref,
        )

    def candidate(self, name, base, proposals, succeeds=True):
        workspace = self.worktree(name, base)
        self.git("cherry-pick", *(proposal.oid for proposal in proposals),
                 cwd=workspace, succeeds=succeeds)
        if not succeeds:
            assert self.git("diff", "--name-only", "--diff-filter=U", cwd=workspace) == "hotspot"
        return workspace

    def check(self, workspace, expected):
        checked = self.git("rev-parse", "HEAD", cwd=workspace)
        for path, contents in expected.items():
            assert (workspace / path).read_text() == contents, path

        # Pass the exact integration candidate through Lit and back to Git.
        self.run(self.lit, "import-git", str(workspace), cwd=workspace)
        verified, _ = self.run(self.lit, "verify", cwd=workspace)
        assert json.loads(verified)["valid"], verified
        exported = self.root / f"exported-{workspace.name}.git"
        self.run(self.lit, "export-git", str(exported), cwd=workspace)
        assert self.git("rev-parse", "HEAD", cwd=exported) == checked
        self.git("fsck", "--full", "--strict", cwd=exported)
        # The check fixture is the writer of this exact-OID verdict.
        self.refs(f"create refs/lit/checks/{checked} {checked}")
        return checked

    def publish(self, base, candidate, proposals, rejected_ref=None):
        self.git("merge-base", "--is-ancestor", base, candidate)
        commands = [
            f"verify refs/lit/checks/{candidate} {candidate}",
            f"update {MAIN} {candidate} {base}",
        ]
        for proposal in proposals:
            commands.extend((
                f"verify {proposal.claim} {proposal.attempt}",
                f"verify {proposal.ref} {proposal.oid}",
                f"create {proposal.receipt} {candidate}",
            ))
        self.refs(*commands, rejected_ref=rejected_ref)

    def assert_receipts(self, oid, proposals):
        # Read a fresh snapshot of the persisted acknowledgments from Git.
        rows = self.git("for-each-ref", "--format=%(refname) %(objectname)",
                        "refs/lit/receipts").splitlines()
        assert dict(row.split() for row in rows) == {
            proposal.receipt: oid for proposal in proposals
        }


def qualify(workers, lit):
    with tempfile.TemporaryDirectory(prefix="lit-agent-workflow-") as directory:
        workflow = Workflow(Path(directory), lit)
        primary = workflow.attempt("attempt 1")
        replacement = workflow.attempt("attempt 2")
        tasks = [f"agent-{i:03d}" for i in range(workers - 2)] + ["hotspot-a", "hotspot-b"]
        expected = {
            f"changes/{name}.txt": f"{name}\n" for name in tasks if not name.startswith("hotspot")
        }
        paths = [(name, f"changes/{name}.txt", expected[f"changes/{name}.txt"])
                 for name in tasks[:-2]]
        paths.extend((name, "hotspot", f"{name}\n") for name in tasks[-2:])
        workflow.refs(*(f"create refs/lit/claims/{task} {primary}" for task in tasks))
        # Prepare shared Git worktree metadata before the concurrent edit and
        # submit phase. No Git subprocess runs before a worker reaches the barrier.
        workspaces = [workflow.prepare_worker(task, primary) for task in tasks]

        barrier = Barrier(workers)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(workflow.propose, task, primary, path, value, workspace, barrier)
                for (task, path, value), workspace in zip(paths, workspaces, strict=True)
            ]
            proposals = [future.result() for future in futures]
        assert len({proposal.oid for proposal in proposals}) == workers
        print(f"{workers} isolated workers submitted pinned proposals", flush=True)

        first_hotspot, second_hotspot = proposals[-2:]
        workflow.candidate("conflict", workflow.base, [first_hotspot, second_hotspot], succeeds=False)
        assert workflow.ref(MAIN) == workflow.base
        workflow.assert_receipts(workflow.base, [])
        accepted = proposals[:-1]
        expected["hotspot"] = "hotspot-a\n"
        gate = workflow.candidate("gate-one", workflow.base, accepted)
        first_candidate = workflow.git("rev-parse", "HEAD", cwd=gate)
        workflow.publish(workflow.base, first_candidate, accepted,
                         rejected_ref=f"refs/lit/checks/{first_candidate}")
        assert workflow.ref(MAIN) == workflow.base
        checked = workflow.check(gate, expected)
        assert checked == first_candidate

        # Reassignment after tests invalidates the checked batch's ownership.
        stale = accepted[0]
        workflow.refs(f"update {stale.claim} {replacement} {primary}")
        workflow.publish(workflow.base, checked, accepted, rejected_ref=stale.claim)
        stale_without_submission = Submission("stale-without-submission", primary, stale.oid)
        workflow.refs(f"create {stale_without_submission.claim} {replacement}")
        workflow.submit(stale_without_submission, rejected_ref=stale_without_submission.claim)
        workflow.assert_absent(stale_without_submission.ref)
        assert workflow.ref(MAIN) == workflow.base
        workflow.assert_receipts(checked, [])
        task, path, text = paths[0]
        workspace = workflow.prepare_worker(task, replacement)
        accepted[0] = workflow.propose(task, replacement, path, text, workspace)

        # A successful independent publication invalidates the candidate's base.
        upstream = workflow.worktree("independent", workflow.base)
        (upstream / "upstream").write_text("advanced main\n")
        workflow.git("add", "upstream", cwd=upstream)
        workflow.git("commit", "-qm", "independent update", cwd=upstream)
        fresh_base = workflow.git("rev-parse", "HEAD", cwd=upstream)
        workflow.refs(f"update {MAIN} {fresh_base} {workflow.base}")
        expected["upstream"] = "advanced main\n"
        fresh_gate = workflow.candidate("gate-two", fresh_base, accepted)
        unchecked = workflow.git("rev-parse", "HEAD", cwd=fresh_gate)
        assert unchecked != checked
        workflow.publish(fresh_base, unchecked, accepted,
                         rejected_ref=f"refs/lit/checks/{unchecked}")
        assert workflow.ref(MAIN) == fresh_base
        rebased = workflow.check(fresh_gate, expected)
        assert rebased == unchecked
        # Verify the stale base with otherwise current ownership and a passing
        # candidate so the expected-old ref is the condition that rejects it.
        workflow.publish(workflow.base, rebased, accepted, rejected_ref=MAIN)
        assert workflow.ref(MAIN) == fresh_base
        workflow.assert_receipts(rebased, [])

        workflow.publish(fresh_base, rebased, accepted)
        assert workflow.ref(MAIN) == rebased
        workflow.assert_receipts(rebased, accepted)
        workflow.publish(rebased, rebased, accepted, rejected_ref=accepted[0].receipt)
        assert workflow.ref(MAIN) == rebased
        workflow.assert_receipts(rebased, accepted)
        workflow.assert_absent(second_hotspot.receipt)
        print(f"{workers} workers: {len(accepted)} proposals landed at checked commit {rebased}; "
              "conflict, missing verdict, stale attempt, stale base, and duplicate publication refused",
              flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lit", type=lambda path: Path(path).resolve())
    parser.add_argument("--workers", nargs="+", type=int, default=[10, 50, 100])
    args = parser.parse_args()
    if min(args.workers) < 3:
        parser.error("each run needs at least three workers")
    for workers in args.workers:
        qualify(workers, args.lit)
