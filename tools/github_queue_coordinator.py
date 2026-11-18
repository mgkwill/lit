"""Durable, fail-closed admission and conflict cleanup for a GitHub merge queue.

Proposal branches must be covered by an active, exact-ref update/deletion rule
with no bypass actors. GitHub dequeue has no expected-head argument, so this
server-enforced immutability is part of the safety contract. Policy admins and
the operator's credential source are trusted. No tokens are written to state.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CHECK = "Agent workflow candidate"
TERMINAL = {"merged", "held_conflict", "superseded", "closed"}


class ApiError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"GitHub HTTP {code}; no credential data retained")


class ResponseUnavailable(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    def __init__(self, url):
        parsed = urlsplit(url)
        if url != "https://api.github.com" and not (
                parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                and parsed.port and not parsed.username and not parsed.path):
            raise ValueError("API must be GitHub or an explicit loopback test server")
        self.url = url
        self.local = parsed.hostname == "127.0.0.1"
        self.observer_token = os.environ.get("LIT_QUEUE_OBSERVER_TOKEN") or None
        self.token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            self.token = subprocess.check_output(
                ["gh", "auth", "token", "--hostname", "github.com"], text=True, timeout=20).strip()
        self.opener = build_opener(NoRedirect())

    def api(self, path, payload=None, writer=False):
        token = self.token if writer else self.observer_token or self.token
        request = Request(self.url + "/" + path,
                          encoded(payload).encode() if payload is not None else None,
                          {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                           "Content-Type": "application/json", "User-Agent": "lit-queue-coordinator"})
        try:
            with self.opener.open(request, timeout=30) as response:
                result = json.load(response)
        except HTTPError as error:
            raise ApiError(error.code) from None
        except (URLError, http.client.HTTPException, TimeoutError, json.JSONDecodeError):
            raise ResponseUnavailable("GitHub response unavailable; reconcile the durable intent before retrying") from None
        if result.get("errors"):
            raise RuntimeError("GitHub returned GraphQL errors; reconcile intent before retrying")
        return result

    def authenticate_writer(self, repo):
        if self.observer_token:
            installed = self.api("installation/repositories", writer=True)
            if (installed["total_count"] != 1
                    or [r["full_name"] for r in installed["repositories"]] != [repo]):
                raise RuntimeError("the writer installation token must be limited to this repository")

    def snapshot(self, repo, number):
        owner, name = repo.split("/")
        result = self.api("graphql", {"operationName": "QueueSnapshot", "query": """
            query QueueSnapshot($owner:String!, $repo:String!, $number:Int!) {
              repository(owner:$owner,name:$repo) {
                ref(qualifiedName:"refs/heads/main"){target{oid}}
                pullRequest(number:$number) {
                  id headRefOid headRefName baseRefName merged closed mergeable
                  headRepository{nameWithOwner} author{__typename login}
                  mergeQueueEntry{id state headCommit{oid}}
                }
              }
            }""", "variables": {"owner": owner, "repo": name, "number": number}})
        return result["data"]["repository"]

    def mutate(self, kind, node, head):
        if kind == "enqueue":
            name = "EnqueueProposal"
            query = """mutation EnqueueProposal($pr:ID!, $head:GitObjectID!) {
              result:enqueuePullRequest(input:{pullRequestId:$pr,expectedHeadOid:$head}){
                mergeQueueEntry{id}
              }}"""
            variables = {"pr": node, "head": head}
        else:
            name = "WithdrawProposal"
            query = """mutation WithdrawProposal($pr:ID!) {
              result:dequeuePullRequest(input:{id:$pr}){mergeQueueEntry{id}}
            }"""
            variables = {"pr": node}
        return self.api("graphql", {"operationName": name, "query": query, "variables": variables}, writer=True)

    def check(self, repo, head):
        checks = self.api(f"repos/{repo}/commits/{head}/check-runs?check_name={quote(CHECK)}&filter=latest&per_page=100")
        eligible = [c for c in checks["check_runs"] if c["name"] == CHECK
                    and c["head_sha"] == head and c["status"] == "completed"
                    and c["conclusion"] == "success" and c["app"]["id"] == 15368]
        if not eligible:
            return False
        runs = self.api(f"repos/{repo}/actions/runs?head_sha={head}&event=pull_request&per_page=100")
        return any(r["event"] == "pull_request" and r["head_sha"] == head
                   and r["path"] == ".github/workflows/agent-merge-gate.yml"
                   and r["conclusion"] == "success"
                   and any(r["check_suite_id"] == c["check_suite"]["id"] for c in eligible)
                   for r in runs["workflow_runs"])


def policies(github, manifest, specs):
    repo = manifest["repo"]
    main = github.api(f"repos/{repo}/rulesets/{manifest['main_ruleset']}")
    frozen = github.api(f"repos/{repo}/rulesets/{manifest['freeze_ruleset']}")
    for rule in (main, frozen):
        if "bypass_actors" not in rule:
            raise RuntimeError("bypass actors are hidden; supply a policy observer with sufficient read access")
        if rule["target"] != "branch" or rule["enforcement"] != "active" or rule["bypass_actors"]:
            raise RuntimeError("active branch rules with no bypass actors are required")
    if main["conditions"]["ref_name"] != {"include": ["refs/heads/main"], "exclude": []}:
        raise RuntimeError("main ruleset must target exactly main")
    types = {r["type"]: r for r in main["rules"]}
    if not {"pull_request", "deletion", "non_fast_forward", "merge_queue", "required_status_checks"} <= types.keys():
        raise RuntimeError("main protection is incomplete")
    queue = types["merge_queue"]["parameters"]
    checks = types["required_status_checks"]["parameters"]["required_status_checks"]
    if (queue["grouping_strategy"] != "ALLGREEN" or queue["merge_method"] != "MERGE"
            or queue["max_entries_to_merge"] != 1
            or {"context": CHECK, "integration_id": 15368} not in checks):
        raise RuntimeError("Actions-only merge queue policy is required")
    refs = frozen["conditions"]["ref_name"]
    if (refs["exclude"] or not {"update", "deletion"} <= {r["type"] for r in frozen["rules"]}
            or not all("refs/heads/" + s["branch"] in refs["include"] for s in specs)
            or any("*" in ref or ref.startswith("~") for ref in refs["include"])):
        raise RuntimeError("proposal branches must be frozen by exact refs without bypasses")


def record(db, number, phase, **details):
    previous = db.execute("SELECT phase FROM proposals WHERE number=?", (number,)).fetchone()[0]
    with db:
        db.execute("UPDATE proposals SET phase=? WHERE number=?", (phase, number))
        if phase != previous or details:
            db.execute("INSERT INTO events(number,kind,data,created) VALUES (?,?,?,?)",
                       (number, phase, encoded(details), time.time()))
    if phase != previous or details:
        output(event=phase, number=number, **details)


def action(db, github, number, kind, pr):
    with db:
        cursor = db.execute("INSERT INTO actions(number,kind,status,entry_id,created) VALUES (?,?,'pending',?,?)",
                            (number, kind, (pr["mergeQueueEntry"] or {}).get("id"), time.time()))
    output(event="intent", number=number, kind=kind, action=cursor.lastrowid)
    try:
        github.mutate(kind, pr["id"], pr["headRefOid"])
    except ApiError as error:
        if error.code == 401:
            with db:
                db.execute("UPDATE actions SET status='denied' WHERE id=?", (cursor.lastrowid,))
        raise
    with db:
        db.execute("UPDATE actions SET status='acknowledged' WHERE id=?", (cursor.lastrowid,))


def resolve(db, number):
    with db:
        db.execute("UPDATE actions SET status='observed' WHERE number=? AND status IN ('pending','acknowledged')",
                   (number,))


def identity(repo, spec, pr):
    return (pr["headRefOid"] == spec["head"] and pr["headRefName"] == spec["branch"]
            and pr["baseRefName"] == "main" and pr["headRepository"]
            and pr["headRepository"]["nameWithOwner"] == repo
            and pr["author"] and pr["author"]["__typename"] == "Bot"
            and pr["author"]["login"].removesuffix("[bot]") == spec["author"].removesuffix("[bot]"))


def conflict_entry(pr):
    entry = pr["mergeQueueEntry"]
    return (entry is not None and entry["headCommit"] is None
            and (entry["state"] == "UNMERGEABLE"
                 or (entry["state"] == "QUEUED" and pr["mergeable"] == "CONFLICTING")))


def conflict_proof(github, manifest, spec, main, args):
    if args.test_git_remote and not github.local:
        raise ValueError("a test Git remote is allowed only with a loopback API fixture")
    remote = args.test_git_remote or f"https://github.com/{manifest['repo']}.git"
    mirror = args.state.resolve() / "mirror.git"
    with tempfile.TemporaryDirectory(prefix="lit-queue-auth-") as home:
        askpass = Path(home) / "askpass"
        askpass.write_text('#!/bin/sh\nprintf "%s\\n" "$LIT_QUEUE_TOKEN"\n')
        askpass.chmod(0o700)
        env = {"PATH": os.environ["PATH"], "HOME": home, "LC_ALL": "C",
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0",
               "LIT_QUEUE_TOKEN": github.token}

        def git(*command, expected=True):
            result = subprocess.run(["git", "-c", "credential.helper=",
                "-c", "credential.username=x-access-token", "-c", "http.followRedirects=false",
                *command], env=env, text=True, capture_output=True, timeout=90)
            if expected and result.returncode:
                raise RuntimeError("Git conflict proof unavailable; no queue mutation performed")
            return result

        if not mirror.exists():
            git("init", "--bare", "-q", str(mirror))
        git("--git-dir", str(mirror), "fetch", "--no-tags", remote,
            "refs/heads/main:refs/coordinator/main", spec["head"])
        found = git("--git-dir", str(mirror), "rev-parse", "refs/coordinator/main").stdout.strip()
        if found != main:
            return None
        result = git("--git-dir", str(mirror), "merge-tree", "--write-tree", main, spec["head"], expected=False)
        if result.returncode == 0:
            return None
        if result.returncode != 1 or "CONFLICT" not in result.stdout:
            raise RuntimeError("Git did not establish a merge conflict; refusing withdrawal")
        return {"main": main, "head": spec["head"],
                "git_conflict_sha256": hashlib.sha256(result.stdout.encode()).hexdigest()}


def tick(db, github, manifest, args):
    rows = list(db.execute("SELECT * FROM proposals ORDER BY number"))
    specs = [json.loads(row["spec"]) for row in rows]
    github.authenticate_writer(manifest["repo"])
    policies(github, manifest, specs)
    for row in sorted(rows, key=lambda r: (json.loads(r["spec"]).get("kind") == "shared", r["number"])):
        if row["phase"] in TERMINAL:
            continue
        spec = json.loads(row["spec"])
        number = spec["number"]
        snapshot = github.snapshot(manifest["repo"], number)
        pr = snapshot["pullRequest"]
        if not identity(manifest["repo"], spec, pr):
            record(db, number, "superseded")
            continue
        if pr["merged"] or pr["closed"]:
            resolve(db, number)
            record(db, number, "merged" if pr["merged"] else "closed")
            continue
        pending = db.execute("SELECT * FROM actions WHERE number=? AND status IN ('pending','acknowledged') ORDER BY id DESC LIMIT 1",
                             (number,)).fetchone()
        entry = pr["mergeQueueEntry"]
        if entry:
            if pending and pending["kind"] == "dequeue":
                record(db, number, "awaiting_dequeue")
                continue
            resolve(db, number)
            record(db, number, "queued")
            since = row["stale_since"] if row["entry_id"] == entry["id"] else None
            if not conflict_entry(pr):
                with db:
                    db.execute("UPDATE proposals SET stale_since=NULL,entry_id=? WHERE number=?",
                               (entry["id"], number))
                continue
            since = since if since is not None else time.time()
            with db:
                db.execute("UPDATE proposals SET stale_since=?,entry_id=? WHERE number=?",
                           (since, entry["id"], number))
            if time.time() - since < args.stale_seconds:
                continue
            main = snapshot["ref"]["target"]["oid"]
            proof = conflict_proof(github, manifest, spec, main, args)
            if not proof:
                continue
            policies(github, manifest, specs)
            latest = github.snapshot(manifest["repo"], number)
            fresh = latest["pullRequest"]
            if not identity(manifest["repo"], spec, fresh):
                record(db, number, "superseded")
                continue
            current = fresh["mergeQueueEntry"]
            if not current:
                record(db, number, "held_conflict", **proof)
            elif (latest["ref"]["target"]["oid"] == main and current["id"] == entry["id"]
                  and conflict_entry(fresh)):
                record(db, number, "queued", **proof)
                action(db, github, number, "dequeue", fresh)
            continue
        if pending and pending["kind"] == "dequeue":
            resolve(db, number)
            record(db, number, "held_conflict")
        elif pr["mergeable"] == "CONFLICTING":
            resolve(db, number)
            record(db, number, "held_conflict")
        elif pending:
            record(db, number, "unknown_enqueue")
        elif row["phase"] in {"queued", "removed", "awaiting_outcome"}:
            # Entry absence can precede the merged/closed fields becoming visible.
            # 'removed' is reconciled for journals written by the initial build.
            record(db, number, "awaiting_outcome")
        elif github.check(manifest["repo"], spec["head"]):
            policies(github, manifest, specs)
            latest = github.snapshot(manifest["repo"], number)["pullRequest"]
            if not identity(manifest["repo"], spec, latest):
                record(db, number, "superseded")
            elif not latest["mergeQueueEntry"] and not latest["merged"] and not latest["closed"]:
                action(db, github, number, "enqueue", latest)
        else:
            record(db, number, "waiting_checks")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def output(**record):
    print(json.dumps(record), flush=True)


@contextmanager
def writer(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / "writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another coordinator owns this state directory") from None
        db = sqlite3.connect(root / "queue.sqlite")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS proposals (
                number INTEGER PRIMARY KEY, spec TEXT NOT NULL, phase TEXT NOT NULL,
                stale_since REAL, entry_id TEXT);
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY, number INTEGER NOT NULL, kind TEXT NOT NULL,
                status TEXT NOT NULL, entry_id TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, number INTEGER, kind TEXT NOT NULL,
                data TEXT NOT NULL, created REAL NOT NULL);
        """)
        try:
            yield db
        finally:
            db.close()


def register(db, path):
    manifest = json.loads(path.read_text())
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", manifest["repo"]):
        raise ValueError("expected owner/repository")
    for key in ("main_ruleset", "freeze_ruleset"):
        if type(manifest[key]) is not int or manifest[key] <= 0:
            raise ValueError("expected positive ruleset IDs")
    specs = manifest.pop("proposals")
    if not specs or len({s["number"] for s in specs}) != len(specs):
        raise ValueError("expected unique proposals")
    for spec in specs:
        if (type(spec["number"]) is not int or spec["number"] <= 0
                or not re.fullmatch(r"[0-9a-f]{40}", spec["head"])
                or not re.fullmatch(r"[A-Za-z0-9-]+\[bot\]", spec["author"])
                or spec["branch"] == "main"):
            raise ValueError("invalid proposal identity")
        subprocess.run(["git", "check-ref-format", "refs/heads/" + spec["branch"]], check=True,
                       capture_output=True)
    with db:
        current = db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
        if current and current[0] != encoded(manifest):
            raise ValueError("state is already bound to a different repository or policy")
        db.execute("INSERT OR IGNORE INTO metadata VALUES ('manifest', ?)", (encoded(manifest),))
        for spec in specs:
            current = db.execute("SELECT spec FROM proposals WHERE number=?", (spec["number"],)).fetchone()
            if current and current[0] != encoded(spec):
                raise ValueError("registered proposal identity cannot change")
            db.execute("INSERT OR IGNORE INTO proposals(number,spec,phase) VALUES (?,?,'registered')",
                       (spec["number"], encoded(spec)))
    output(event="registered", proposals=len(specs))


def status(db):
    return {"proposals": [json.loads(row["spec"]) | {"phase": row["phase"]}
                          for row in db.execute("SELECT * FROM proposals ORDER BY number")],
            "actions": [dict(row) for row in db.execute("SELECT * FROM actions ORDER BY id")]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    enrollment = commands.add_parser("register")
    enrollment.add_argument("manifest", type=Path)
    commands.add_parser("status")
    run = commands.add_parser("run")
    run.add_argument("--api-url", default="https://api.github.com")
    run.add_argument("--stale-seconds", type=float, default=120)
    run.add_argument("--watch", action="store_true")
    run.add_argument("--poll-seconds", type=float, default=15)
    run.add_argument("--max-seconds", type=float, default=3600)
    run.add_argument("--test-git-remote", help="local Git fixture, only with a loopback API")
    args = parser.parse_args()
    try:
        if args.command == "run" and (args.stale_seconds < 0 or args.poll_seconds <= 0 or args.max_seconds <= 0):
            raise ValueError("stale threshold must be nonnegative and poll/deadline positive")
        if args.command == "status":
            with sqlite3.connect(f"file:{args.state / 'queue.sqlite'}?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                output(**status(db))
        else:
            with writer(args.state) as db:
                if args.command == "register":
                    register(db, args.manifest)
                else:
                    github = GitHub(args.api_url)
                    saved = db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
                    if not saved:
                        raise ValueError("register a manifest before running the coordinator")
                    manifest = json.loads(saved[0])
                    deadline = time.monotonic() + args.max_seconds
                    while True:
                        try:
                            tick(db, github, manifest, args)
                        except (ResponseUnavailable, ApiError) as error:
                            retryable = isinstance(error, ResponseUnavailable) or error.code in {429, 500, 502, 503, 504}
                            if not args.watch or not retryable:
                                raise
                            output(event="retrying_observation", message=str(error))
                            if time.monotonic() >= deadline:
                                raise RuntimeError("watch deadline reached; durable state preserved for restart")
                            time.sleep(args.poll_seconds)
                            continue
                        current = status(db)
                        output(event="status", **current)
                        done = all(p["phase"] in TERMINAL for p in current["proposals"])
                        if done or not args.watch:
                            return int(any(p["phase"] in {"superseded", "closed"} for p in current["proposals"]))
                        if time.monotonic() >= deadline:
                            raise RuntimeError("watch deadline reached; durable state preserved for restart")
                        time.sleep(args.poll_seconds)
    except (ValueError, RuntimeError, OSError, sqlite3.Error, subprocess.SubprocessError, KeyError) as error:
        print(json.dumps({"event": "error", "message": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
