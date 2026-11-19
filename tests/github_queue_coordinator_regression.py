"""Black-box CLI tests for the durable hosted queue coordinator."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLI = Path(__file__).resolve().parents[1] / "tools/github_queue_coordinator.py"


class Forge:
    """A stateful external GitHub boundary, not a mock of coordinator internals."""
    def __init__(self, proposal):
        self.proposal = proposal
        self.queued = False
        self.queue_state = "QUEUED"
        self.candidate = None
        self.mergeable = "MERGEABLE"
        self.merged = False
        self.merge_event = None
        self.merge_summary = None
        self.merge_check_head = None
        self.merge_check_event = 'merge_group'
        self.merge_check_suite = 100
        self.closed = False
        self.complete_after_enqueue = False
        self.main = "c" * 40
        self.mutations = []
        self.freeze = True
        self.extra_frozen_refs = []
        self.snapshot_numbers = []
        self.track_latest_publication = True
        self.removal_event = None
        self.main_failures = 0
        self.freeze_reads = 0
        self.remove_freeze_on_recheck = False
        self.check_app = 15368
        self.newer_preflight_pending = False
        self.publication_calls = []
        self.published_branch = None
        self.drop_publication_reply = False
        self.block_publication = False
        self.publication_started = threading.Event()
        self.publication_release = threading.Event()
        self.publication_titles = []
        self.next_publication_number = 77
        self.author_type = "Bot"
        self.allow_observer = False
        self.snapshots = 0
        self.replace_head_on_second_read = False
        self.drop_reply = False
        self.drop_without_apply = False
        self.calls = []
        self.block_reply = False
        self.applied = threading.Event()
        self.release = threading.Event()
        forge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, body, code=200):
                if forge.drop_publication_reply and self.command=='POST' and self.path.endswith('/pulls'):
                    self.close_connection=True
                    return
                if (forge.allow_observer and self.headers.get("Authorization") == "Bearer good"
                        and "bypass_actors" in body):
                    body = {key: value for key, value in body.items() if key != "bypass_actors"}
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except BrokenPipeError:
                    pass

            def authorized(self):
                allowed = ["Bearer good"] + (["Bearer observer"] if forge.allow_observer else [])
                if self.headers.get("Authorization") not in allowed:
                    self.respond({"message": "Bad credentials"}, 401)
                    return False
                return True

            def do_GET(self):
                if not self.authorized():
                    return
                if self.path == "/installation/repositories":
                    if self.headers.get("Authorization") != "Bearer good":
                        self.respond({"message": "writer token required"}, 401)
                    else:
                        self.respond({"total_count": 1, "repositories": [
                            {"full_name": "100latent/lit-merge-queue-sandbox"}]})
                elif '/git/commits/' in self.path:
                    self.respond({'sha':'c'*40,'tree':{'sha':'d'*40}})
                elif self.path.endswith('/rulesets/13'):
                    self.respond({'target':'branch','enforcement':'active','bypass_actors':[
                        {'actor_id':99,'actor_type':'Integration','bypass_mode':'exempt'}],
                        'conditions':{'ref_name':{'include':['refs/heads/agents/broker/**/*'],'exclude':[]}},
                        'rules':[{'type':'creation'},{'type':'update'},{'type':'deletion'}]})
                elif self.path.endswith("/rulesets/10"):
                    if forge.main_failures:
                        forge.main_failures -= 1
                        self.respond({"message": "temporary gateway error"}, 502)
                        return
                    self.respond({"target": "branch", "enforcement": "active", "bypass_actors": [],
                                  "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
                                  "rules": [{"type": "pull_request"}, {"type": "deletion"},
                                            {"type": "non_fast_forward"},
                                            {"type": "merge_queue", "parameters": {"grouping_strategy": "ALLGREEN",
                                                "merge_method": "MERGE", "max_entries_to_merge": 1}},
                                            {"type": "required_status_checks", "parameters": {
                                                "required_status_checks": [{"context": "Agent workflow candidate", "integration_id": 15368}]}}]})
                elif self.path.endswith("/rulesets/11"):
                    forge.freeze_reads += 1
                    if forge.remove_freeze_on_recheck and forge.freeze_reads == 2:
                        forge.freeze = False
                    self.respond({"target": "branch", "enforcement": "active" if forge.freeze else "disabled",
                                  "bypass_actors": [], "conditions": {"ref_name": {
                                      "include": ["refs/heads/" + forge.proposal["branch"]] + forge.extra_frozen_refs, "exclude": []}},
                                  "rules": [{"type": "update"}, {"type": "deletion"}]})
                elif "/check-runs?" in self.path:
                    self.respond({"total_count": 1, "check_runs": [{"name": "Agent workflow candidate",
                        "id": 5010, "head_sha": forge.merge_check_head or forge.proposal["head"], "status": "completed", "conclusion": "success",
                        "app": {"id": forge.check_app}, "check_suite": {"id": 100}}]})
                elif "/actions/runs?" in self.path:
                    event = "merge_group" if "event=merge_group" in self.path else "pull_request"
                    runs = [{"id":500,"created_at":"2026-11-17T00:00:00Z","status":"completed","event":forge.merge_check_event if event=="merge_group" else event,
                        "head_sha":forge.merge_check_head if event=="merge_group" else forge.proposal["head"],
                        "check_suite_id":forge.merge_check_suite if event=="merge_group" else 100,
                        "path":".github/workflows/agent-merge-gate.yml","conclusion":"success"}]
                    if forge.newer_preflight_pending:
                        runs.insert(0,runs[0]|{"id":501,"created_at":"2026-11-18T00:00:00Z",
                            "status":"in_progress","conclusion":None,"check_suite_id":101})
                    self.respond({"total_count":len(runs),"workflow_runs":runs})

                else:
                    self.respond({"message": "Unexpected fixture endpoint"}, 404)

            def do_POST(self):
                if not self.authorized():
                    return
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path!='/graphql':
                    forge.publication_calls.append(self.path)
                    if forge.block_publication and self.path.endswith('/git/trees'):
                        forge.block_publication = False
                        forge.publication_started.set()
                        forge.publication_release.wait(20)
                    if self.path.endswith('/git/trees'):
                        self.respond({'sha':'d'*40})
                    elif self.path.endswith('/git/commits'):
                        self.respond({'sha':'e'*40})
                    elif self.path.endswith('/git/refs'):
                        forge.published_branch=request['ref'].removeprefix('refs/heads/')
                        self.respond({'ref':request['ref'],'object':{'sha':'e'*40}})
                    elif self.path.endswith('/pulls'):
                        forge.publication_titles.append(request['title'])
                        number=forge.next_publication_number;forge.next_publication_number+=1
                        if forge.track_latest_publication:
                            forge.proposal.update(number=number,head='e'*40,branch=request['head'],author='broker[bot]')
                        self.respond({'number':number,'head':{'sha':'e'*40,'ref':request['head']},'user':{'login':'broker[bot]'}})
                    else:self.respond({'message':'unexpected publication route'},404)
                    return
                operation = request["operationName"]
                forge.calls.append(operation)
                if operation == "QueueSnapshot":
                    forge.snapshots += 1
                    forge.snapshot_numbers.append(request["variables"]["number"])
                    if forge.replace_head_on_second_read and forge.snapshots == 2:
                        forge.proposal["head"] = "b" * 40
                    p = forge.proposal
                    if forge.complete_after_enqueue and forge.queued:
                        forge.merged = True
                        forge.queued = False
                    self.respond({"data": {"repository": {
                        "ref": {"target": {"oid": forge.main}},
                        "pullRequest": {"id": "PR_7", "headRefOid": p["head"],
                            "headRefName": p["branch"], "baseRefName": "main", "merged": forge.merged,
                            "closed": forge.closed, "mergeable": forge.mergeable,
                            "mergeCommit": {"oid": forge.merge_summary} if forge.merge_summary else None,
                            "mergeEvents": {"nodes": [forge.merge_event] if forge.merge_event else []},
                            "queueEvents":{"nodes":[forge.removal_event] if forge.removal_event else []},
                            "headRepository": {"nameWithOwner": "100latent/lit-merge-queue-sandbox"},
                            "author": {"__typename": forge.author_type,
                                       "login": p["author"].removesuffix("[bot]")}, "mergeQueueEntry": {
                                "id": "MQE_7", "state": forge.queue_state, "headCommit": forge.candidate
                            } if forge.queued else None}}}})
                else:
                    if self.headers.get("Authorization") != "Bearer good":
                        self.respond({"message": "writer token required"}, 401)
                        return
                    if forge.drop_without_apply:
                        self.close_connection = True
                        return
                    forge.mutations.append(operation)
                    if operation == "EnqueueProposal":
                        if request["variables"]["head"] != forge.proposal["head"]:
                            self.respond({"errors": [{"message": "head differs"}]})
                            return
                        forge.queued = True
                    elif operation == "WithdrawProposal":
                        forge.queued = False
                    elif operation == "CloseProposal":
                        forge.closed = True
                    else:
                        self.respond({"errors": [{"message": "unexpected mutation"}]})
                        return
                    forge.applied.set()
                    if forge.block_reply:
                        forge.release.wait(10)
                    if forge.drop_reply:
                        self.close_connection = True
                        return
                    self.respond({"data": {"result": {"mergeQueueEntry": {"id": "MQE_7"}}}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.publication_release.set()
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="lit-queue-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.state = self.root / "state"
        self.manifest = self.root / "manifest.json"
        self.proposal = {"number": 7, "head": "a" * 40, "branch": "agents/test/7",
                         "author": "worker[bot]"}
        self.payload = {"repo": "100latent/lit-merge-queue-sandbox", "main_ruleset": 10,
                        "freeze_ruleset": 11, "proposals": [self.proposal]}
        self.manifest.write_text(json.dumps(self.payload))

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(CLI), "--state", str(self.state), *args],
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def forge(self):
        self.remote = Forge(dict(self.proposal))
        self.addCleanup(self.remote.close)
        self.cli("register", str(self.manifest))

    def run_args(self):
        args = [sys.executable, str(CLI), "--state", str(self.state), "run", "--api-url", self.remote.url,
                "--stale-seconds", "0"]
        if hasattr(self, "git_remote"):
            args += ["--test-git-remote", str(self.git_remote)]
        return args

    def git_fixture(self, conflict=True):
        self.git_remote = self.root / "git-remote"
        self.git_remote.mkdir()
        def git(*args):
            return subprocess.check_output(["git", "-C", str(self.git_remote), *args], text=True,
                env={"PATH": os.environ["PATH"], "HOME": str(self.root), "LC_ALL": "C",
                                  "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                  "GIT_AUTHOR_NAME": "fixture", "GIT_COMMITTER_NAME": "fixture",
                                  "GIT_AUTHOR_EMAIL": "fixture@example.test",
                                  "GIT_COMMITTER_EMAIL": "fixture@example.test"}, stderr=subprocess.DEVNULL).strip()
        git("init", "-q", "-b", "main")
        (self.git_remote / "shared").write_text("base\n")
        git("add", ".")
        git("commit", "-qm", "base")
        git("checkout", "-qb", self.proposal["branch"])
        (self.git_remote / ("shared" if conflict else "disjoint")).write_text("proposal\n")
        git("add", ".")
        git("commit", "-qm", "proposal")
        self.proposal["head"] = git("rev-parse", "HEAD")
        git("checkout", "-q", "main")
        (self.git_remote / "shared").write_text("landed\n")
        git("commit", "-qam", "landed")
        self.manifest.write_text(json.dumps(self.payload))
        self.forge()
        self.remote.main = git("rev-parse", "HEAD")
        self.remote.queued = True
        self.remote.queue_state = "UNMERGEABLE"
        self.remote.mergeable = "UNKNOWN"
        return git

    def merged_fixture(self):
        git = self.git_fixture(conflict=False)
        previous = git('rev-parse', 'HEAD')
        git('merge', '--no-ff', '-m', 'Merge proposal', self.proposal['head'])
        merge = git('rev-parse', 'HEAD')
        git('commit', '--allow-empty', '-qm', 'Later main advance')
        self.remote.main = git('rev-parse', 'HEAD')
        self.remote.queued = False
        self.remote.merged = True
        self.remote.merge_summary = previous
        self.remote.merge_event = {'id': 'merge-event-210', 'commit': {'oid': merge},
                                   'mergeRefName': 'main', 'createdAt': '2026-11-18T16:42:15Z'}
        self.remote.merge_check_head = merge
        return merge

    def test_210_summary_mismatch_uses_main_history_event_and_exact_actions(self):
        merge = self.merged_fixture()
        self.tick()
        status, = self.cli('status')
        result = status['proposals'][0]['integration']
        self.assertEqual(result['state'], 'verified')
        receipt = result['receipt']
        self.assertEqual(receipt['merge_commit'], merge)
        self.assertEqual(receipt['proposal']['head'], self.proposal['head'])
        self.assertEqual(receipt['merge_event_id'], 'merge-event-210')
        self.assertEqual(receipt['forge_summary_commit'], self.remote.merge_summary)
        self.assertFalse(receipt['forge_summary_matches'])
        self.assertEqual(receipt['actions']['check_run_id'], 5010)
        self.assertEqual(receipt['actions']['event'], 'merge_group')
        self.remote.merge_event = None
        self.tick()
        self.assertEqual(self.cli('status')[0]['proposals'][0]['integration'], result)
        self.assertEqual(self.remote.mutations, [])

    def assert_integration_pending(self, reason):
        self.tick()
        proposal = self.cli('status')[0]['proposals'][0]
        self.assertEqual(proposal['phase'], 'merged')
        self.assertEqual(proposal['integration'], {'state': 'pending', 'reason': reason})
        self.assertEqual(self.remote.mutations, [])

    def test_merge_event_and_actions_cannot_substitute_for_git_history(self):
        self.merged_fixture()
        wrong = self.remote.merge_summary
        self.remote.merge_event['commit']['oid'] = wrong
        self.remote.merge_check_head = wrong
        self.assert_integration_pending('git_history_unconfirmed')

    def test_wrong_actions_suite_cannot_issue_receipt(self):
        self.merged_fixture()
        self.remote.merge_check_suite = 101
        self.assert_integration_pending('actions_check_unconfirmed')

    def test_pull_request_check_cannot_issue_merge_receipt(self):
        self.merged_fixture()
        self.remote.merge_check_event = 'pull_request'
        self.assert_integration_pending('actions_check_unconfirmed')

    def test_missing_merge_event_remains_pending(self):
        self.merged_fixture()
        self.remote.merge_event = None
        self.assert_integration_pending('merge_event_unconfirmed')

    def tick(self, expected=0, token="good", extra=(), observer=""):
        result = subprocess.run(self.run_args() + list(extra), text=True, capture_output=True, timeout=20,
                                env=os.environ | {"GH_TOKEN": token, "LIT_QUEUE_OBSERVER_TOKEN": observer})
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn("Bearer good", result.stdout + result.stderr)
        return result

    def test_registration_is_idempotent_and_rejects_changed_attempt(self):
        self.cli("register", str(self.manifest))
        self.cli("register", str(self.manifest))
        status, = self.cli("status")
        self.assertEqual(len(status["proposals"]), 1)
        self.assertEqual(status["proposals"][0]["head"], "a" * 40)
        self.proposal["head"] = "b" * 40
        self.manifest.write_text(json.dumps(self.payload))
        self.cli("register", str(self.manifest), expected=1)
        status, = self.cli("status")
        self.assertEqual(status["proposals"][0]["head"], "a" * 40)

    def test_lost_enqueue_reply_is_reconciled_without_duplicate_mutation(self):
        self.forge()
        self.remote.drop_reply = True
        self.tick(expected=1)
        self.assertTrue(self.remote.queued)
        self.remote.drop_reply = False
        self.tick()
        self.assertEqual(self.remote.mutations, ["EnqueueProposal"])
        status, = self.cli("status")
        self.assertEqual(status["proposals"][0]["phase"], "queued")

    def test_unfrozen_branch_refuses_admission(self):
        self.forge()
        self.remote.freeze = False
        self.tick(expected=1)
        self.assertEqual(self.remote.mutations, [])

    def test_non_actions_check_cannot_admit_proposal(self):
        self.forge()
        self.remote.check_app = 999
        self.tick()
        self.assertEqual(self.remote.mutations, [])

    def test_stale_unknown_mergeability_is_withdrawn_only_after_real_git_conflict(self):
        self.git_fixture()
        self.tick()
        self.tick()
        self.assertEqual(self.remote.mutations, ["WithdrawProposal"])
        status, = self.cli("status")
        self.assertEqual(status["proposals"][0]["phase"], "held_conflict")

    def test_transient_unmergeable_entry_without_git_conflict_is_preserved(self):
        self.git_fixture(conflict=False)
        self.tick()
        self.assertTrue(self.remote.queued)
        self.assertEqual(self.remote.mutations, [])

    def test_changed_head_before_withdrawal_is_fenced(self):
        self.git_fixture()
        self.remote.replace_head_on_second_read = True
        self.tick(expected=1)
        self.assertTrue(self.remote.queued)
        self.assertEqual(self.remote.mutations, [])

    def test_kill_after_remote_acceptance_recovers_without_duplicate_enqueue(self):
        self.forge()
        self.remote.block_reply = True
        worker = subprocess.Popen(self.run_args(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=os.environ | {"GH_TOKEN": "good", "LIT_QUEUE_OBSERVER_TOKEN": ""})
        self.addCleanup(lambda: worker.poll() is None and worker.kill())
        self.assertTrue(self.remote.applied.wait(10))
        worker.kill()
        worker.communicate(timeout=5)
        self.remote.release.set()
        self.remote.block_reply = False
        self.tick()
        self.assertEqual(self.remote.mutations, ["EnqueueProposal"])

    def test_revoked_credentials_stop_then_fresh_credentials_reconcile(self):
        self.forge()
        self.tick(token="revoked", expected=1)
        self.assertEqual(self.remote.mutations, [])
        self.tick()
        self.tick()
        self.assertEqual(self.remote.mutations, ["EnqueueProposal"])

    def test_competing_writer_cannot_mutate_and_killed_owner_releases_lock(self):
        self.forge()
        worker = subprocess.Popen(self.run_args() + ["--watch", "--poll-seconds", "0.1"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=os.environ | {"GH_TOKEN": "good", "LIT_QUEUE_OBSERVER_TOKEN": ""})
        self.addCleanup(lambda: worker.poll() is None and worker.kill())
        self.assertTrue(self.remote.applied.wait(10))
        result = self.tick(expected=1)
        self.assertIn("another coordinator", result.stderr)
        worker.kill()
        worker.communicate(timeout=5)
        self.tick()
        self.assertEqual(self.remote.mutations, ["EnqueueProposal"])

    def test_lost_withdraw_reply_recovers_without_reenqueue(self):
        self.git_fixture()
        self.remote.drop_reply = True
        self.tick(expected=1)
        self.assertFalse(self.remote.queued)
        self.remote.drop_reply = False
        self.tick()
        self.tick()
        self.assertEqual(self.remote.mutations, ["WithdrawProposal"])
        status, = self.cli("status")
        self.assertEqual(status["proposals"][0]["phase"], "held_conflict")

    def test_ambiguous_non_acceptance_is_not_blindly_resubmitted(self):
        self.forge()
        self.remote.drop_without_apply = True
        self.tick(expected=1)
        self.remote.drop_without_apply = False
        self.tick()
        self.tick()
        self.assertEqual(self.remote.calls.count("EnqueueProposal"), 1)
        self.assertEqual(self.remote.mutations, [])
        status, = self.cli("status")
        self.assertEqual(status["proposals"][0]["phase"], "unknown_enqueue")

    def test_removed_branch_lock_prevents_withdrawal(self):
        self.git_fixture()
        self.remote.remove_freeze_on_recheck = True
        self.tick(expected=1)
        self.assertEqual(self.remote.mutations, [])

    def test_changed_head_before_admission_is_fenced(self):
        self.forge()
        self.remote.replace_head_on_second_read = True
        self.tick(expected=1)
        self.assertEqual(self.remote.mutations, [])

    def test_disappeared_entry_awaits_definitive_outcome_without_reenqueue(self):
        self.forge()
        self.tick()
        self.tick()
        self.remote.queued = False
        self.tick()
        self.tick()
        self.assertEqual(self.remote.mutations, ["EnqueueProposal"])
        status, = self.cli("status")
        self.assertEqual(status["proposals"][0]["phase"], "awaiting_outcome")
        self.remote.merged = True
        self.tick()
        status, = self.cli("status")
        self.assertEqual(status["proposals"][0]["phase"], "merged")

    def test_stale_age_survives_restart_and_never_cleans_immediately(self):
        self.git_fixture()
        self.tick(extra=("--stale-seconds", "2"))
        self.assertTrue(self.remote.queued)
        self.assertEqual(self.remote.mutations, [])
        time.sleep(2.1)
        self.tick(extra=("--stale-seconds", "2"))
        self.assertFalse(self.remote.queued)
        self.assertEqual(self.remote.mutations, ["WithdrawProposal"])

    def test_human_actor_with_matching_app_slug_is_not_the_registered_bot(self):
        self.forge()
        self.remote.author_type = "User"
        self.tick(expected=1)
        self.assertEqual(self.remote.mutations, [])

    def test_observer_can_see_policy_but_cannot_replace_revoked_writer(self):
        self.forge()
        self.remote.allow_observer = True
        self.tick(observer="observer")
        self.tick(observer="observer", token="revoked", expected=1)
        self.assertEqual(self.remote.mutations, ["EnqueueProposal"])
        self.tick(observer="observer")
        self.assertEqual(self.remote.mutations, ["EnqueueProposal"])

    def test_watch_reconciles_lost_reply_and_completes_without_a_supervisor_restart(self):
        self.forge()
        self.remote.drop_reply = True
        self.remote.complete_after_enqueue = True
        self.tick(extra=("--watch", "--poll-seconds", "0.1", "--max-seconds", "5"))
        self.assertEqual(self.remote.mutations, ["EnqueueProposal"])
        status, = self.cli("status")
        self.assertEqual(status["proposals"][0]["phase"], "merged")

    def test_watch_retries_read_only_gateway_failure_before_admission(self):
        self.forge()
        self.remote.main_failures = 1
        self.remote.complete_after_enqueue = True
        result = self.tick(extra=("--watch", "--poll-seconds", "0.1", "--max-seconds", "5"))
        self.assertIn("retrying_observation", result.stdout)
        self.assertEqual(self.remote.mutations, ["EnqueueProposal"])

    def test_queued_conflicting_pr_is_withdrawn_after_native_git_proof(self):
        self.git_fixture()
        self.remote.queue_state = "QUEUED"
        self.remote.mergeable = "CONFLICTING"
        self.tick()
        self.tick()
        self.assertEqual(self.remote.mutations, ["WithdrawProposal"])
        status, = self.cli("status")
        self.assertEqual(status["proposals"][0]["phase"], "held_conflict")

    def test_queued_conflict_label_without_git_conflict_is_preserved(self):
        self.git_fixture(conflict=False)
        self.remote.queue_state = "QUEUED"
        self.remote.mergeable = "CONFLICTING"
        self.tick()
        self.assertTrue(self.remote.queued)
        self.assertEqual(self.remote.mutations, [])

    def test_newer_pending_preflight_cannot_reuse_older_success_for_same_sha(self):
        self.forge()
        self.remote.newer_preflight_pending=True
        self.tick()
        self.assertEqual(self.remote.mutations,[])

    def test_queued_conflict_with_a_candidate_commit_is_preserved(self):
        self.git_fixture()
        self.remote.queue_state = "QUEUED"
        self.remote.mergeable = "CONFLICTING"
        self.remote.candidate = {"oid": self.remote.main}
        self.tick()
        self.assertTrue(self.remote.queued)
        self.assertEqual(self.remote.mutations, [])


class OwnershipTests(unittest.TestCase):
    setUp = CoordinatorTests.setUp
    cli = CoordinatorTests.cli
    run_args = CoordinatorTests.run_args
    tick = CoordinatorTests.tick

    def assign(self, generation=0, owner='worker[bot]', request=None, expected=0, task='task-1'):
        return self.cli('assign', task, '--expect', str(generation), '--owner', owner,
                        '--base', 'c' * 40, '--request', request or f'{task}-{generation}-{owner}',
                        expected=expected)

    def bind(self, claim, expected=0, capability=None):
        self.proposal.update({k: claim[k] for k in ('task', 'generation', 'claim', 'base')})
        self.proposal['author'] = claim['owner']
        self.manifest.write_text(json.dumps(self.payload))
        result = subprocess.run([sys.executable, str(CLI), '--state', str(self.state),
            'register', str(self.manifest), '--claims-stdin'], input=json.dumps({
                claim['claim']: capability if capability is not None else claim.get('capability', '')}),
            text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def owned_forge(self):
        self.claim = self.assign()[0]
        self.bind(self.claim)
        self.remote = Forge(dict(self.proposal))
        self.addCleanup(self.remote.close)

    def test_retry_is_stable_and_revoked_retry_cannot_resurrect(self):
        first = self.assign()[0]
        self.assertEqual(first, self.assign()[0])
        self.assign(1, 'second[bot]')
        self.assign(expected=1)
        self.assign(2, 'worker[bot]')
        self.bind(first, expected=1)
        self.assertNotIn(first['capability'], json.dumps(self.cli('status')))
        for file in self.state.iterdir():
            self.assertNotIn(first['capability'].encode(), file.read_bytes())

    def test_request_cannot_change_shape(self):
        self.assign(request='same')
        self.assign(1, 'second[bot]', request='same', expected=1)

    def test_competing_claims_and_reassignments_have_one_winner(self):
        for generation in (0, 1):
            processes = [subprocess.Popen([sys.executable, str(CLI), '--state', str(self.state),
                'assign', 'task-1', '--expect', str(generation), '--owner', f'agent-{i}[bot]',
                '--base', 'c' * 40, '--request', f'{generation}-{i}'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(12)]
            results = [p.communicate(timeout=15) for p in processes]
            self.assertEqual(sum(p.returncode == 0 for p in processes), 1, results)
        claims = self.cli('status')[0]['claims']
        self.assertEqual([c['generation'] for c in claims], [1, 2])

    def test_binding_racing_reassignment_never_escapes_retirement(self):
        for trial in range(8):
            self.state = self.root / f'race-{trial}'
            claim = self.assign()[0]
            self.proposal.update({k: claim[k] for k in ('task', 'generation', 'claim', 'base')})
            self.manifest.write_text(json.dumps(self.payload))
            prefix = [sys.executable, str(CLI), '--state', str(self.state)]
            binder = subprocess.Popen(prefix + ['register', str(self.manifest), '--claims-stdin'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assigner = subprocess.Popen(prefix + ['assign', 'task-1', '--expect', '1',
                '--owner', 'second[bot]', '--base', 'c' * 40, '--request', 'race-next'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            binder.communicate(json.dumps({claim['claim']: claim['capability']}), timeout=15)
            assigner.communicate(timeout=15)
            self.assertIn(binder.returncode, (0, 1))
            self.assertIn(assigner.returncode, (0, 1))
            next_claim = self.assign(1, 'second[bot]', request='race-next')[0]
            current = self.cli('status')[0]
            self.assertEqual(next_claim['phase'], 'draining' if current['proposals'] else 'active')
            self.assertEqual(current['claims'][0]['phase'], 'revoked')
            self.bind(claim, expected=1)

    def test_capability_and_assignment_fields_are_enforced(self):
        claim = self.assign()[0]
        self.bind(claim, expected=1, capability='forged')
        self.bind(claim, expected=1, capability='forged-\u2603')
        for key, value in [('owner', 'impostor[bot]'), ('base', 'd' * 40),
                           ('generation', 2), ('task', 'another'), ('claim', 'unknown')]:
            self.bind(claim | {key: value}, expected=1)
        self.bind(claim)
        self.proposal['number'] = 8
        self.bind(claim, expected=1)
        self.proposal['number'] = 7
        self.proposal['head'] = 'b' * 40
        self.bind(claim, expected=1)

    def test_owned_state_rejects_unowned_registration(self):
        self.assign()
        self.cli('register', str(self.manifest), expected=1)

    def test_existing_legacy_state_cannot_silently_enable_ownership(self):
        self.cli('register', str(self.manifest))
        self.assign(expected=1)

    def test_reassignment_retires_nonconflicting_queue_before_grant(self):
        self.owned_forge()
        self.tick()
        pending = self.assign(1, 'second[bot]')[0]
        self.assertEqual(pending['phase'], 'draining')
        self.assertNotIn('capability', pending)
        self.bind(self.claim, expected=1)
        self.assign(2, 'third[bot]', expected=1)
        self.tick()  # withdraw a valid candidate because its owner was revoked
        self.assertFalse(self.remote.queued)
        self.assertEqual(self.assign(1, 'second[bot]')[0]['phase'], 'draining')
        self.tick()  # close old PR
        self.assertTrue(self.remote.closed)
        self.tick()  # observe closure, activate next attempt
        next_claim = self.assign(1, 'second[bot]')[0]
        self.assertEqual(next_claim['phase'], 'active')
        self.assertIn('capability', next_claim)
        self.assertEqual(self.remote.mutations, ['EnqueueProposal', 'WithdrawProposal', 'CloseProposal'])
        self.tick()
        self.assertEqual(len(self.remote.mutations), 3)

    def test_reassignment_before_admission_only_closes_old_pr(self):
        self.owned_forge()
        self.assign(1, 'second[bot]')
        self.tick()
        self.tick()
        self.assertEqual(self.remote.mutations, ['CloseProposal'])
        self.assertEqual(self.assign(1, 'second[bot]')[0]['phase'], 'active')

    def test_policy_or_identity_failure_keeps_new_owner_fenced(self):
        self.owned_forge()
        self.assign(1, 'second[bot]')
        self.remote.freeze = False
        self.tick(expected=1)
        self.remote.freeze = True
        self.remote.proposal['head'] = 'b' * 40
        self.tick(expected=1)
        self.assertEqual(self.assign(1, 'second[bot]')[0]['phase'], 'draining')
        self.assertEqual(self.remote.mutations, [])

    def test_inflight_publication_blocks_reassignment_but_not_other_tasks(self):
        self.owned_forge()
        self.remote.block_reply = True
        process = subprocess.Popen(self.run_args(), env=dict(os.environ, GH_TOKEN='good',
            LIT_QUEUE_OBSERVER_TOKEN=''), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.assertTrue(self.remote.applied.wait(5))
        self.assign(1, 'second[bot]', expected=1)
        self.assertEqual(self.assign(task='independent')[0]['phase'], 'active')
        process.kill()
        process.communicate(timeout=5)
        self.remote.block_reply = False
        self.remote.release.set()
        self.assign(1, 'second[bot]')
        self.tick()
        self.tick()
        self.tick()
        self.assertEqual(self.assign(1, 'second[bot]')[0]['phase'], 'active')
        self.assertEqual(self.remote.mutations.count('EnqueueProposal'), 1)

    def test_lost_retirement_replies_reconcile_after_restart(self):
        self.owned_forge()
        self.tick()
        self.assign(1, 'second[bot]')
        self.remote.drop_reply = True
        self.tick(expected=1)  # dequeue applied, reply lost
        self.assertNotIn('capability', self.assign(1, 'second[bot]')[0])
        self.tick(expected=1)  # close applied, reply lost
        self.assertNotIn('capability', self.assign(1, 'second[bot]')[0])
        self.remote.drop_reply = False
        self.tick()
        self.assertEqual(self.assign(1, 'second[bot]')[0]['phase'], 'active')
        self.assertEqual(self.remote.mutations, ['EnqueueProposal', 'WithdrawProposal', 'CloseProposal'])

    def test_merged_previous_attempt_is_accounted_before_grant(self):
        self.owned_forge()
        self.tick()
        self.assign(1, 'second[bot]')
        self.remote.merged = True
        self.remote.queued = False
        result = self.tick()
        self.assertEqual(self.assign(1, 'second[bot]')[0]['phase'], 'active')
        self.assertEqual(self.remote.mutations, ['EnqueueProposal'])
        self.assertTrue(any(event.get('merged') is True for event in map(json.loads, result.stdout.splitlines())))
        integration = self.cli('status')[0]['proposals'][0]['integration']
        self.assertEqual(integration, {'state': 'pending', 'reason': 'merge_event_unconfirmed'})

    def test_new_generation_binds_a_distinct_pr_after_retirement(self):
        self.owned_forge()
        self.assign(1, 'second[bot]')
        self.tick()
        self.tick()
        claim = self.assign(1, 'second[bot]')[0]
        self.bind(claim, expected=1)  # cannot reuse the old PR number
        self.proposal['number'] = 8
        self.proposal['head'] = 'd' * 40
        self.proposal['branch'] = 'agents/second/8'
        self.bind(claim)
        self.assertEqual(self.cli('status')[0]['claims'][-1]['proposal'], 8)
        self.bind(self.claim, expected=1)

    def test_revoked_writer_cannot_finish_handoff_using_observer(self):
        self.owned_forge()
        self.assign(1, 'second[bot]')
        self.remote.allow_observer = True
        self.tick(expected=1, token='revoked', observer='observer')
        self.assertNotIn('capability', self.assign(1, 'second[bot]')[0])
        self.assertEqual(self.remote.mutations, [])

    def test_failed_multi_proposal_registration_rolls_back_claim_binding(self):
        claim = self.assign()[0]
        self.payload['proposals'].append(dict(self.proposal, number=8))
        self.bind(claim, expected=1)
        state = self.cli('status')[0]
        self.assertIsNone(state['claims'][0]['proposal'])
        self.assertEqual(state['proposals'], [])

    def test_unknown_close_does_not_activate_or_blindly_retry(self):
        self.owned_forge()
        self.assign(1, 'second[bot]')
        self.remote.drop_without_apply = True
        self.tick(expected=1)
        self.remote.drop_without_apply = False
        self.tick()
        self.assertFalse(self.remote.closed)
        self.assertNotIn('capability', self.assign(1, 'second[bot]')[0])
        self.assertEqual(self.remote.mutations, [])


if __name__ == "__main__":
    unittest.main()
