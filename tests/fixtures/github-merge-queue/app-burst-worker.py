"""Publish ten pinned proposals from one lab host using an App token on stdin.

Nine edits are disjoint. The tenth competes with the other hosts on the same
new file. After printing ready, wait for a Unix start time on the next stdin line.
The coordinator must start every host before enqueueing any pull request.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

REPO = "100latent/lit-merge-queue-sandbox"
HOSTS = ("host-a", "host-e", "host-f")
REMOTE = f"https://github.com/{REPO}.git"


def emit(**record):
    print(json.dumps(record), flush=True)


def api(path, token, payload=None, method=None):
    request = Request(
        "https://api.github.com" + path,
        json.dumps(payload).encode() if payload is not None else None,
        {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
         "Content-Type": "application/json", "User-Agent": "lit-app-burst"},
        method=method,
    )
    with urlopen(request, timeout=45) as response:
        return json.load(response) if response.status != 204 else None


def branch(run, host, index):
    return f"agents/app-burst/{run}/{host}/{index:02d}"


def run(args, token):
    repos = api("/installation/repositories", token)
    if repos["total_count"] != 1 or [r["full_name"] for r in repos["repositories"]] != [REPO]:
        raise RuntimeError("Expected a token limited to the sandbox")
    with tempfile.TemporaryDirectory(prefix="lit-app-burst-") as scratch:
        root = Path(scratch)
        askpass = root / "askpass"
        askpass.write_text('#!/bin/sh\nprintf "%s\\n" "$LIT_APP_BURST_TOKEN"\n')
        askpass.chmod(0o700)
        env = {
            "HOME": scratch, "PATH": os.environ["PATH"], "LC_ALL": "C",
            "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "LIT_APP_BURST_TOKEN": token,
            "GIT_AUTHOR_NAME": args.app, "GIT_AUTHOR_EMAIL": f"{args.app}@users.noreply.github.com",
            "GIT_COMMITTER_NAME": args.app,
            "GIT_COMMITTER_EMAIL": f"{args.app}@users.noreply.github.com",
        }

        def git(*command, cwd=root, expected=True):
            result = subprocess.run(
                ["git", "-c", "credential.helper=", "-c", "credential.username=x-access-token", *command],
                cwd=cwd, env=env, text=True, capture_output=True, timeout=90,
            )
            if expected and result.returncode:
                raise RuntimeError(result.stderr.replace(token, "[redacted]"))
            return result

        def head(ref):
            values = git("ls-remote", "--refs", REMOTE, ref).stdout.split()
            if len(values) != 2 or values[1] != ref:
                raise RuntimeError(f"Missing remote ref {ref}")
            return values[0]

        git("init", "-q")
        if head("refs/heads/main") != args.base:
            raise RuntimeError("Main advanced before burst preparation")
        git("fetch", "--depth=1", REMOTE, args.base)
        attempts = []
        for index in range(10):
            work = root / f"attempt-{index:02d}"
            git("worktree", "add", "--detach", str(work), args.base)
            path = f"qualification/burst/{args.run}/"
            path += "shared.txt" if index == 9 else f"{args.host}-{index:02d}.txt"
            content = f"run={args.run}\nhost={args.host}\nattempt={index:02d}\nbase={args.base}\n"
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            (work / path).write_text(content)
            git("add", path, cwd=work)
            git("commit", "-q", "-m", f"Burst {args.run} {args.host} attempt {index:02d}", cwd=work)
            oid = git("rev-parse", "HEAD", cwd=work).stdout.strip()
            assert git("rev-parse", "HEAD^", cwd=work).stdout.strip() == args.base
            attempts.append({"work": work, "index": index, "head": oid,
                             "branch": branch(args.run, args.host, index),
                             "path": path, "content": content,
                             "kind": "shared" if index == 9 else "disjoint"})
        emit(event="ready", host=args.host, base=args.base, proposals=len(attempts))
        start = float(sys.stdin.readline().strip())
        if not time.time() <= start <= time.time() + 120:
            raise RuntimeError("Expected a start time within the next two minutes")
        time.sleep(max(0, start - time.time()))

        def publish(attempt):
            began = time.time()
            target = "refs/heads/" + attempt["branch"]
            git("push", "--porcelain", REMOTE, f"HEAD:{target}", cwd=attempt["work"])
            assert head(target) == attempt["head"]
            pr = api(f"/repos/{REPO}/pulls", token, {
                "head": attempt["branch"], "base": "main",
                "title": f"Burst {args.run} {args.host} {attempt['index']:02d} {attempt['kind']}",
                "body": "Synthetic multi-host burst. Pinned base " + args.base
                        + ". Shared-file proposals intentionally conflict. Automatic Actions checks only.",
            })
            assert pr["user"]["login"] == args.app + "[bot]"
            assert pr["head"]["sha"] == attempt["head"] and pr["base"]["sha"] == args.base
            emit(event="published", host=args.host, app=args.app, base=args.base,
                 **{key: value for key, value in attempt.items() if key != "work"},
                 pr=pr["number"], node_id=pr["node_id"], author=pr["user"]["login"],
                 url=pr["html_url"], started_at=began, finished_at=time.time())

        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(publish, attempts))

        # All peers must have published before the coordinator releases this probe.
        emit(event="published_all", host=args.host)
        if sys.stdin.readline().strip() != "challenge":
            raise RuntimeError("Expected challenge barrier release")
        for destination in [branch(args.run, host, 0) for host in HOSTS if host != args.host] + ["main"]:
            ref = "refs/heads/" + destination
            before = head(ref)
            git("fetch", "--depth=1", REMOTE, before)
            tree = git("rev-parse", f"{before}^{{tree}}").stdout.strip()
            probe = git("commit-tree", tree, "-p", before, "-m", "Denied burst cross-owner update").stdout.strip()
            result = git("push", "--porcelain", REMOTE, f"{probe}:{ref}", expected=False)
            if not result.returncode or not re.search(r"GH013|GH006", result.stderr):
                raise RuntimeError(f"Expected server-side branch ownership rejection for {ref}")
            assert head(ref) == before
            emit(event="denied", host=args.host, destination=destination, before=before, probe=probe)
    api("/installation/token", token, method="DELETE")
    emit(event="complete", host=args.host, token_revoked=True, temporary_workspaces_removed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", choices=HOSTS)
    parser.add_argument("app")
    parser.add_argument("base")
    parser.add_argument("run")
    args = parser.parse_args()
    if socket.gethostname().split(".", 1)[0] != args.host:
        parser.error("worker must run on its assigned host")
    if not re.fullmatch(r"[0-9a-f]{40}", args.base):
        parser.error("expected a full base SHA")
    if not all(re.fullmatch(r"[a-z0-9-]+", value) for value in (args.app, args.run)):
        parser.error("expected an App slug and lowercase run identifier")
    token = sys.stdin.readline().strip()
    if not token.startswith("ghs_"):
        parser.error("provide an installation token on stdin")
    run(args, token)


if __name__ == "__main__":
    main()
