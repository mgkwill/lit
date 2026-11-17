"""Run one GitHub App's sandbox worker probe. Read its installation token on stdin."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen

REPO = "100latent/lit-merge-queue-sandbox"
HOSTS = ("host-a", "host-e", "host-f")
REMOTE = f"https://github.com/{REPO}.git"


def api(path, token, payload=None):
    data = json.dumps(payload).encode() if payload else None
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "lit-three-app-pilot",
    }
    with urlopen(Request("https://api.github.com" + path, data, headers), timeout=25) as response:
        return json.load(response)


def branch(host):
    return f"agents/app-pilot/{host}"


def validate_installation(token):
    repos = api("/installation/repositories", token)
    if repos["total_count"] != 1 or [row["full_name"] for row in repos["repositories"]] != [REPO]:
        raise SystemExit("Expected an installation token limited to the pilot sandbox")


def test(args, token):
    with tempfile.TemporaryDirectory(prefix="lit-app-pilot-") as scratch:
        root = Path(scratch)
        askpass = root / "askpass"
        askpass.write_text('#!/bin/sh\nprintf "%s\\n" "$LIT_APP_PILOT_TOKEN"\n')
        askpass.chmod(0o700)
        env = {
            "HOME": scratch, "PATH": os.environ["PATH"],
            "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "LIT_APP_PILOT_TOKEN": token,
            "GIT_AUTHOR_NAME": args.app, "GIT_AUTHOR_EMAIL": f"{args.app}@users.noreply.github.com",
            "GIT_COMMITTER_NAME": args.app, "GIT_COMMITTER_EMAIL": f"{args.app}@users.noreply.github.com",
            "LC_ALL": "C",
        }

        def git(*command, expected=True):
            result = subprocess.run(
                ["git", "-c", "credential.helper=", "-c", "credential.username=x-access-token", *command],
                cwd=root, env=env, text=True, capture_output=True, timeout=45,
            )
            if expected and result.returncode:
                raise RuntimeError(result.stderr.replace(token, "[redacted]"))
            return result

        def head(ref):
            result = git("ls-remote", "--refs", REMOTE, ref).stdout.split()
            if len(result) == 2 and result[1] == ref:
                return result[0]
            raise RuntimeError(f"Missing remote ref {ref}")

        validate_installation(token)
        git("init", "-q")
        target = branch(args.host)
        if args.action == "publish":
            if head("refs/heads/main") != args.base:
                raise RuntimeError("Main has advanced since pilot setup")
            git("fetch", "--depth=1", REMOTE, args.base)
            git("checkout", "-q", "--detach", args.base)
            path = root / "qualification" / "app-pilot" / f"{args.host}.txt"
            path.parent.mkdir(parents=True)
            path.write_text(f"host={args.host}\napp={args.app}\nbase={args.base}\n")
            git("add", str(path.relative_to(root)))
            git("commit", "-q", "-m", f"Pilot proposal from {args.host}")
            oid = git("rev-parse", "HEAD").stdout.strip()
            git("push", "--porcelain", REMOTE, f"HEAD:refs/heads/{target}")
            assert head(f"refs/heads/{target}") == oid
            pr = api(f"/repos/{REPO}/pulls", token, {
                "head": target, "base": "main", "title": f"Qualify GitHub App ownership on {args.host}",
                "body": "Synthetic branch ownership proposal for the private sandbox pilot.",
            })
            if pr["user"]["login"] != args.app + "[bot]" or pr["head"]["sha"] != oid:
                raise RuntimeError("Pull request identity or SHA differs from the worker receipt")
            print(json.dumps({"host": args.host, "app": args.app, "head": oid,
                              "author": pr["user"]["login"], "pr": pr["number"], "url": pr["html_url"]}))
        else:
            destinations = [branch(host) for host in HOSTS if host != args.host] + ["main"]
            for destination in destinations:
                ref = f"refs/heads/{destination}"
                before = head(ref)
                git("fetch", "--depth=1", REMOTE, before)
                tree = git("rev-parse", f"{before}^{{tree}}").stdout.strip()
                probe = git("commit-tree", tree, "-p", before,
                            "-m", f"Denied {args.host} update to {destination}").stdout.strip()
                result = git("push", "--porcelain", REMOTE, f"{probe}:{ref}", expected=False)
                if not result.returncode or not re.search(r"GH013|GH006", result.stderr):
                    raise RuntimeError(f"Expected server-side branch rule rejection for {ref}")
                assert head(ref) == before
                print(json.dumps({"host": args.host, "app": args.app, "denied": destination,
                                  "unchanged_sha": before, "probe_sha": probe}))


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("action", choices=("publish", "challenge"))
parser.add_argument("host", choices=HOSTS)
parser.add_argument("app")
parser.add_argument("base")
args = parser.parse_args()
if socket.gethostname().split(".", 1)[0] != args.host:
    parser.error("worker must run on its assigned lab host")
if not re.fullmatch(r"[0-9a-f]{40}", args.base) or not re.fullmatch(r"[a-z0-9-]+", args.app):
    parser.error("expected a full base SHA and an App slug")
token = sys.stdin.readline().strip()
if not token.startswith("ghs_"):
    parser.error("provide a GitHub App installation token on stdin")
test(args, token)
