"""Durable, fail-closed admission and conflict cleanup for a GitHub merge queue.

Proposal branches must be covered by an active, exact-ref update/deletion rule
with no bypass actors. GitHub dequeue has no expected-head argument, so this
server-enforced immutability is part of the safety contract. Policy admins and
the operator's credential source are trusted. No tokens are written to state.
"""
import argparse
from contextlib import contextmanager, ExitStack
import task_ownership as ownership
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
TERMINAL = {"merged", "held_conflict", "superseded", "closed", "ownership_retired"}


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
    def __init__(self, url, token=None, observer_token=None):
        parsed = urlsplit(url)
        if url != "https://api.github.com" and not (
                parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                and parsed.port and not parsed.username and not parsed.path):
            raise ValueError("API must be GitHub or an explicit loopback test server")
        if token is not None and not token:
            raise ValueError("explicit writer credential is empty")
        self.url = url
        self.local = parsed.hostname == "127.0.0.1"
        self.observer_token = (os.environ.get("LIT_QUEUE_OBSERVER_TOKEN") or None) if observer_token is None else observer_token
        self.token = token if token is not None else os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
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
        if isinstance(result, dict) and result.get("errors"):
            raise RuntimeError("GitHub returned GraphQL errors; reconcile intent before retrying")
        return result

    def authenticate_writer(self, repo, force=False):
        if self.observer_token or force:
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
                  id headRefOid headRefName baseRefName merged mergedAt closed mergeable
                  headRepository{nameWithOwner} author{__typename login}
                  mergeCommit{oid}
                  mergeEvents:timelineItems(last:2,itemTypes:[MERGED_EVENT]) {
                    nodes { ... on MergedEvent { id createdAt mergeRefName commit{oid} } }
                  }
                  closeEvents:timelineItems(last:2,itemTypes:[CLOSED_EVENT]) {
                    nodes { ... on ClosedEvent {
                      id createdAt actor{__typename login}
                      closer{__typename ... on Commit{oid}}
                    }}
                  }
                  mergeQueueEntry{id state headCommit{oid}}
                  queueEvents:timelineItems(last:1,itemTypes:[ADDED_TO_MERGE_QUEUE_EVENT,REMOVED_FROM_MERGE_QUEUE_EVENT,MERGED_EVENT]) {
                    nodes { __typename ... on RemovedFromMergeQueueEvent {
                      id reason createdAt actor{__typename login} beforeCommit{oid} pullRequest{id}
                    }}
                  }
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
        elif kind == "close":
            name = "CloseProposal"
            query = """mutation CloseProposal($pr:ID!) {
              result:closePullRequest(input:{pullRequestId:$pr}){pullRequest{id}}
            }"""
            variables = {"pr": node}
        elif kind == "dequeue":
            name = "WithdrawProposal"
            query = """mutation WithdrawProposal($pr:ID!) {
              result:dequeuePullRequest(input:{id:$pr}){mergeQueueEntry{id}}
            }"""
            variables = {"pr": node}
        else:
            raise ValueError("unknown queue action")
        return self.api("graphql", {"operationName": name, "query": query, "variables": variables}, writer=True)

    def check(self, repo, head):
        return self.actions_check(repo, head, 'pull_request') is not None

    def actions_check(self, repo, head, event):
        checks = self.api(f"repos/{repo}/commits/{head}/check-runs?check_name={quote(CHECK)}&filter=latest&per_page=100")
        eligible = [c for c in checks["check_runs"] if c["name"] == CHECK
                    and c["head_sha"] == head and c["status"] == "completed"
                    and c["conclusion"] == "success" and c["app"]["id"] == 15368]
        if not eligible:
            return None
        runs = self.api(f"repos/{repo}/actions/runs?head_sha={head}&event={event}&per_page=100")
        relevant = [r for r in runs['workflow_runs'] if r['event'] == event
                    and r['head_sha'] == head and r['path'] == '.github/workflows/agent-merge-gate.yml']
        if not relevant:
            return None
        latest = max(relevant, key=lambda r: (r['created_at'], r['id']))
        if latest['status'] != 'completed' or latest['conclusion'] != 'success':
            return None
        matching = [c for c in eligible if c['check_suite']['id'] == latest['check_suite_id']]
        if not matching:
            return None
        return {'run_id': latest['id'], 'run_attempt': latest.get('run_attempt'),
                'check_suite_id': latest['check_suite_id'],
                'check_run_id': max(matching, key=lambda c: c.get('id', 0)).get('id'),
                'app_id': 15368, 'event': event}



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


@contextmanager
def git_mirror(github, manifest, args):
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
                raise RuntimeError("Git proof unavailable")
            return result

        if not mirror.exists():
            git("init", "--bare", "-q", str(mirror))
        def in_mirror(*command, expected=True):
            return git("--git-dir", str(mirror), *command, expected=expected)

        yield in_mirror, remote


def conflict_proof(github, manifest, spec, main, args):
    with git_mirror(github, manifest, args) as (git, remote):
        git("fetch", "--no-tags", remote, "refs/heads/main:refs/coordinator/main", spec["head"])
        found = git("rev-parse", "refs/coordinator/main").stdout.strip()
        if found != main:
            return None
        result = git("merge-tree", "--write-tree", main, spec["head"], expected=False)
        if result.returncode == 0:
            return None
        if result.returncode != 1 or "CONFLICT" not in result.stdout:
            raise RuntimeError("Git did not establish a merge conflict; refusing withdrawal")
        return {"main": main, "head": spec["head"],
                "git_conflict_sha256": hashlib.sha256(result.stdout.encode()).hexdigest()}


def merge_record(pr):
    merges = pr.get('mergeEvents', {}).get('nodes', [])
    if merges:
        if len(merges) == 1 and merges[0] and merges[0].get('mergeRefName') in ('main', 'refs/heads/main'):
            return merges[0], None
        return None, None

    def queue_bot(event):
        actor = (event or {}).get('actor') or {}
        return actor == {'__typename': 'Bot', 'login': 'github-merge-queue'}

    merged_at = pr.get('mergedAt')
    if not merged_at:
        return None, None
    closures = [event for event in pr.get('closeEvents', {}).get('nodes', [])
                if queue_bot(event) and isinstance(event.get('createdAt'), str)
                and event['createdAt'] >= merged_at
                and (event.get('closer') or {}).get('__typename') == 'Commit']
    removals = pr.get('queueEvents', {}).get('nodes', [])
    if len(closures) != 1 or len(removals) != 1 or not queue_bot(removals[0]):
        return None, None
    removal = removals[0]
    if (removal.get('__typename') != 'RemovedFromMergeQueueEvent' or removal.get('reason') != 'merged'
            or removal.get('createdAt') != merged_at or not removal.get('id')
            or (removal.get('pullRequest') or {}).get('id') != pr['id']):
        return None, None
    closure = closures[0]
    return {'id': closure.get('id'), 'createdAt': merged_at,
            'closedAt': closure['createdAt'], 'commit': closure['closer']}, removal


def integration_evidence(github, manifest, spec, snapshot, args):
    """Prove a frozen proposal landed on main and Actions checked that exact merge."""
    repo = manifest['repo']
    pr = snapshot['pullRequest']
    if not pr or not identity(repo, spec, pr) or not pr['merged'] or pr['mergeQueueEntry']:
        return None, 'merge_identity_unconfirmed'
    event, removal = merge_record(pr)
    if not event or not event.get('id'):
        return None, 'merge_event_unconfirmed'
    event_commit = (event.get('commit') or {}).get('oid', '')
    # GitHub can report the preceding PR's merge in the closure. The queue
    # removal identifies this proposal's integration candidate.
    merge = (removal.get('beforeCommit') or {}).get('oid', '') if removal else event_commit
    main = snapshot['ref']['target']['oid']
    if not all(isinstance(oid, str) and re.fullmatch(r'[0-9a-f]{40}', oid)
               for oid in (main, merge, event_commit)):
        return None, 'merge_event_unconfirmed'
    check = github.actions_check(repo, merge, 'merge_group')
    if not check or not check['check_run_id']:
        return None, 'actions_check_unconfirmed'
    with git_mirror(github, manifest, args) as (git, remote):
        git('fetch', '--no-tags', remote, 'refs/heads/main:refs/coordinator/main')
        # main may advance between API observation and fetch. It must still
        # contain the observed main on its first-parent chain.
        history = git('rev-list', '--first-parent', '--parents', 'refs/coordinator/main').stdout.splitlines()
        observed = False
        matches = []
        for line in history:
            commit, *parents = line.split()
            if commit == main:
                observed = True
            if observed and len(parents) == 2 and parents[1] == spec['head']:
                matches.append((commit, parents[0]))
        if len(matches) != 1 or matches[0][0] != merge or (removal and event_commit not in matches[0]):
            return None, 'git_history_unconfirmed'
    summary = (pr.get('mergeCommit') or {}).get('oid')
    receipt = {
        'version': 1, 'repository': repo, 'number': spec['number'],
        'proposal': spec, 'pull_request_id': pr['id'], 'base_ref': 'refs/heads/main',
        'observed_main': main, 'merge_commit': merge, 'first_parent': matches[0][1],
        'merge_event_id': event['id'], 'merged_at': event.get('createdAt'),
        'forge_summary_commit': summary,
        'forge_summary_matches': None if summary is None else summary == merge,
        'actions': check, 'verified_at': time.time(),
    }
    if removal:
        receipt.update(merge_event_kind='queue_closure', queue_closure_commit=event_commit,
                       queue_closure_at=event['closedAt'], queue_removal_event_id=removal['id'])
    return receipt, None


def reconcile_integration(db, github, manifest, args):
    # Verify at most one receipt after queue reconciliation. A missing receipt
    # does not change the observed queue outcome or the ownership handoff.
    row = db.execute("""SELECT p.number,p.spec FROM proposals p
        LEFT JOIN integrations i ON i.number=p.number
        WHERE i.receipt IS NULL AND coalesce(i.retry_after,0)<=? AND
          (p.phase='merged' OR (p.phase='ownership_retired' AND EXISTS (
            SELECT 1 FROM events e WHERE e.number=p.number AND e.kind='ownership_retired'
            AND json_extract(e.data,'$.merged')=1)))
        ORDER BY coalesce(i.retry_after,0),p.number LIMIT 1""", (time.time(),)).fetchone()
    if not row:
        return
    try:
        snapshot = github.snapshot(manifest['repo'], row['number'])
        receipt, reason = integration_evidence(github, manifest, json.loads(row['spec']), snapshot, args)
    except (OSError, RuntimeError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        receipt, reason = None, 'integration_evidence_unavailable'
    with db:
        db.execute("""INSERT INTO integrations(number,receipt,reason,retry_after) VALUES (?,?,?,?)
            ON CONFLICT(number) DO UPDATE SET receipt=excluded.receipt,
            reason=excluded.reason,retry_after=excluded.retry_after""",
            (row['number'], encoded(receipt) if receipt else None, reason, time.time() + 60))


def tick(db, github, manifest, args):
    rows = list(db.execute("SELECT * FROM proposals ORDER BY number"))
    specs = [json.loads(row["spec"]) for row in rows]
    github.authenticate_writer(manifest["repo"])
    policies(github, manifest, specs)
    ordered=sorted(rows, key=lambda r: (json.loads(r["spec"]).get("kind") == "shared", r["number"]))
    limit=getattr(args,'max_proposals',None)
    if limit is not None:
        # Broker cycles yield between small batches so remote observations cannot
        # monopolize publication. Standalone CLI ticks retain their full sweep.
        retiring, ordinary = [], []
        for row in ordered:
            spec = json.loads(row['spec'])
            if 'claim' in spec and ownership.binding(db, spec)['phase'] == 'revoked':
                retiring.append(row)
            elif row['phase'] not in TERMINAL:
                ordinary.append(row)

        def rotate(rows, cursor):
            return [r for r in rows if r['number'] > cursor] + [r for r in rows if r['number'] <= cursor]

        retire_limit = limit if not ordinary else max(1, limit - 1)
        priority = rotate(retiring, getattr(args, 'retirement_cursor', 0))[:retire_limit]
        remaining = rotate(ordinary, getattr(args, 'proposal_cursor', 0))[:limit-len(priority)]
        ordered = priority + remaining
        priority_numbers = {row['number'] for row in priority}
    for row in ordered:
        spec = json.loads(row['spec'])
        if limit is not None:
            # Advance even on a transient failure so one stuck task cannot
            # monopolize its lane. Each lane retains its own rotation.
            cursor_name = 'retirement_cursor' if row['number'] in priority_numbers else 'proposal_cursor'
            setattr(args, cursor_name, row['number'])
        with ExitStack() as locks:
            if 'task' in spec:
                locks.enter_context(ownership.fence(args.state, spec['task']))
            proposal_tick(db, github, manifest, args, specs, row)
    reconcile_integration(db, github, manifest, args)


def drain(db, github, manifest, specs, spec, claim):
    if claim['phase'] == 'retired':
        record(db, spec['number'], 'ownership_retired')
        return
    policies(github, manifest, specs)
    pr = github.snapshot(manifest['repo'], spec['number'])['pullRequest']
    if not identity(manifest['repo'], spec, pr):
        raise RuntimeError('revoked proposal identity changed; task remains fenced')
    number = spec['number']
    if (pr['closed'] or pr['merged']) and not pr['mergeQueueEntry']:
        db.execute("UPDATE actions SET status='fenced_by_closure' WHERE number=? AND status IN ('pending','acknowledged')", (number,))
        # Preserve the observed merge before granting a successor. A restart
        # between these commits still sees the revoked claim and resumes drain.
        record(db, number, 'ownership_retired', merged=pr['merged'])
        ownership.retired(db, claim['claim'])
        return
    kind = 'dequeue' if pr['mergeQueueEntry'] else 'close'
    pending = db.execute("SELECT 1 FROM actions WHERE number=? AND kind=? AND status IN ('pending','acknowledged')", (number, kind)).fetchone()
    record(db, number, 'ownership_draining', generation=claim['generation'])
    if not pending:
        action(db, github, number, kind, pr)


def proposal_tick(db, github, manifest, args, specs, row):
    spec = json.loads(row["spec"])
    if db.execute('SELECT 1 FROM tasks LIMIT 1').fetchone():
        claim = ownership.binding(db, spec)
        if claim['phase'] in ('revoked', 'retired'):
            drain(db, github, manifest, specs, spec, claim)
            return
        if claim['phase'] != 'active':
            raise RuntimeError('task is not active')
    if row["phase"] in TERMINAL:
        return
    spec = json.loads(row["spec"])
    number = spec["number"]
    snapshot = github.snapshot(manifest["repo"], number)
    pr = snapshot["pullRequest"]
    if not identity(manifest["repo"], spec, pr):
        record(db, number, "superseded")
        return
    if pr["merged"] or pr["closed"]:
        resolve(db, number)
        record(db, number, "merged" if pr["merged"] else "closed")
        return
    pending = db.execute("SELECT * FROM actions WHERE number=? AND status IN ('pending','acknowledged') ORDER BY id DESC LIMIT 1",
                         (number,)).fetchone()
    entry = pr["mergeQueueEntry"]
    if entry:
        if pending and pending["kind"] == "dequeue":
            record(db, number, "awaiting_dequeue")
            return
        resolve(db, number)
        record(db, number, "queued")
        since = row["stale_since"] if row["entry_id"] == entry["id"] else None
        if not conflict_entry(pr):
            with db:
                db.execute("UPDATE proposals SET stale_since=NULL,entry_id=? WHERE number=?",
                           (entry["id"], number))
            return
        since = since if since is not None else time.time()
        with db:
            db.execute("UPDATE proposals SET stale_since=?,entry_id=? WHERE number=?",
                       (since, entry["id"], number))
        if time.time() - since < args.stale_seconds:
            return
        main = snapshot["ref"]["target"]["oid"]
        proof = conflict_proof(github, manifest, spec, main, args)
        if not proof:
            return
        policies(github, manifest, specs)
        latest = github.snapshot(manifest["repo"], number)
        fresh = latest["pullRequest"]
        if not identity(manifest["repo"], spec, fresh):
            record(db, number, "superseded")
            return
        current = fresh["mergeQueueEntry"]
        if not current:
            record(db, number, "held_conflict", **proof)
        elif (latest["ref"]["target"]["oid"] == main and current["id"] == entry["id"]
              and conflict_entry(fresh)):
            record(db, number, "queued", **proof)
            action(db, github, number, "dequeue", fresh)
        return
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
    with ownership.state_lock(root, "writer.lock", "another coordinator owns this state directory"), database(root) as db:
        yield db


@contextmanager
def database(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
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
        CREATE TABLE IF NOT EXISTS integrations (
            number INTEGER PRIMARY KEY, receipt TEXT, reason TEXT, retry_after REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY, number INTEGER, kind TEXT NOT NULL,
            data TEXT NOT NULL, created REAL NOT NULL);
    """)
    ownership.initialize(db)
    try:
        yield db
    finally:
        db.close()

def register(db, path, capabilities):
    manifest = json.loads(path.read_text())
    specs = manifest.pop("proposals")
    validate_registration(manifest, specs)
    with db:
        db.execute('BEGIN IMMEDIATE')
        register_specs(db, manifest, specs, capabilities)
    output(event="registered", proposals=len(specs))


def validate_registration(manifest, specs):
    if not re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", manifest["repo"]):
        raise ValueError("expected owner/repository")
    for key in ("main_ruleset", "freeze_ruleset"):
        if type(manifest[key]) is not int or manifest[key] <= 0:
            raise ValueError("expected positive ruleset IDs")
    if not specs or len({s["number"] for s in specs}) != len(specs):
        raise ValueError("expected unique proposals")
    for spec in specs:
        if (type(spec["number"]) is not int or spec["number"] <= 0
                or not re.fullmatch(r"[0-9a-f]{40}", spec["head"])
                or not re.fullmatch(r"[A-Za-z0-9-]+\[bot\]", spec["author"])
                or spec["branch"] == "main"):
            raise ValueError("invalid proposal identity")
        result = subprocess.run(["git", "check-ref-format", "refs/heads/" + spec["branch"]],
                                capture_output=True)
        if result.returncode:
            raise ValueError("invalid proposal branch")


def register_specs(db, manifest, specs, capabilities):
    """Bind validated proposals in the caller's write transaction."""
    owned = db.execute('SELECT 1 FROM tasks LIMIT 1').fetchone()
    for spec in specs:
        current = db.execute("SELECT spec FROM proposals WHERE number=?", (spec["number"],)).fetchone()
        if current and current[0] != encoded(spec):
            raise ValueError("registered proposal identity cannot change")
        if owned:
            if (not isinstance(spec.get('task'), str) or not spec['task']
                    or type(spec.get('generation')) is not int):
                raise ValueError('expected task and integer generation')
            ownership.bind(db, spec, capabilities)
        elif 'task' in spec:
            raise ValueError('task authority is missing')
    current = db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
    if current and current[0] != encoded(manifest):
        raise ValueError("state is already bound to a different repository or policy")
    db.execute("INSERT OR IGNORE INTO metadata VALUES ('manifest', ?)", (encoded(manifest),))
    for spec in specs:
        db.execute("INSERT OR IGNORE INTO proposals(number,spec,phase) VALUES (?,?,'registered')",
                   (spec["number"], encoded(spec)))


def status(db):
    integrations = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='integrations'").fetchone():
        integrations = {r['number']: r for r in db.execute('SELECT * FROM integrations')}
    proposals = []
    for row in db.execute("SELECT * FROM proposals ORDER BY number"):
        proposal = json.loads(row['spec']) | {'phase': row['phase']}
        integration = integrations.get(row['number'])
        if integration and integration['receipt']:
            proposal['integration'] = {'state': 'verified', 'receipt': json.loads(integration['receipt'])}
        elif integration or row['phase'] == 'merged':
            proposal['integration'] = {'state': 'pending',
                                       'reason': integration['reason'] if integration else 'not_checked'}
        proposals.append(proposal)
    return {'claims': ownership.status(db), 'proposals': proposals,
            'actions': [dict(row) for row in db.execute("SELECT * FROM actions ORDER BY id")]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    enrollment = commands.add_parser("register")
    enrollment.add_argument("manifest", type=Path)
    enrollment.add_argument('--claims-stdin', action='store_true')
    assign = commands.add_parser('assign', help='trusted scheduler CAS claim or reassignment')
    assign.add_argument('task')
    assign.add_argument('--expect', type=int, required=True)
    assign.add_argument('--owner', required=True)
    assign.add_argument('--base', required=True)
    assign.add_argument('--request', required=True)
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
        if args.command != 'status' and (args.state/'replicated-authority').exists():
            raise ValueError('use the replicated broker to mutate this authority')
        if args.command == "run" and (args.stale_seconds < 0 or args.poll_seconds <= 0 or args.max_seconds <= 0):
            raise ValueError("stale threshold must be nonnegative and poll/deadline positive")
        if args.command == 'assign':
            if (not args.task or args.expect < 0 or not args.request
                    or not re.fullmatch(r'[A-Za-z0-9-]+\[bot\]', args.owner)
                    or not re.fullmatch(r'[0-9a-f]{40}', args.base)):
                raise ValueError('invalid task assignment')
            with database(args.state) as db, ownership.fence(args.state, args.task):
                output(**ownership.assign(db, args))
            return 0
        if args.command == "status":
            with sqlite3.connect(f"file:{args.state / 'queue.sqlite'}?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                output(**status(db))
        else:
            with writer(args.state) as db:
                if args.command == "register":
                    capabilities = json.load(sys.stdin) if args.claims_stdin else {}
                    if not isinstance(capabilities, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in capabilities.items()):
                        raise ValueError('expected claim-to-capability object on stdin')
                    specs = json.loads(args.manifest.read_text())['proposals']
                    with ExitStack() as locks:
                        for task in sorted({s['task'] for s in specs if 'task' in s}):
                            locks.enter_context(ownership.fence(args.state, task))
                        register(db, args.manifest, capabilities)
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
