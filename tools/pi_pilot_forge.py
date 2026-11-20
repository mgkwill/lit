"""GitHub rules and independent integration checks for Pi cohorts."""

from copy import deepcopy
import json
import re
import subprocess

from github_queue_coordinator import GitHub
from pi_pilot_runner import PilotError


FIELDS = ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")
GIT_AUTH = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
ACTIONS_APP = 15368


def command(args, data=None, timeout=180):
    result = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise PilotError(f"{args[0]} failed ({result.returncode}): {result.stderr.decode(errors='replace')[-1800:]}")
    return result.stdout


def require(condition, message):
    if not condition:
        raise PilotError(message)


class Forge:
    def __init__(self, root, repo, config):
        self.root = root
        self.repo = repo
        self.owner, self.name = repo.split("/")
        self.rules = config["rules"]
        self.workflow = config["workflow"]
        self.required_check = config["required_check"]
        self.origin = f"https://github.com/{repo}.git"

    def gh(self, path, *args, value=None):
        data = json.dumps(value).encode() if value is not None else None
        return json.loads(command(["gh", "api", path, *args], data))

    def gql(self, fields):
        query = f'query{{repository(owner:{json.dumps(self.owner)},name:{json.dumps(self.name)}){{{fields}}}}}'
        result = self.gh("graphql", "-f", f"query={query}")
        require(not result.get("errors"), "GitHub did not return the requested repository evidence")
        return result["data"]["repository"]

    def policies(self):
        return {key: {field: rule[field] for field in FIELDS}
                for key, number in self.rules.items()
                for rule in [self.gh(f"repos/{self.repo}/rulesets/{number}")]}

    def replace_rule(self, key, target):
        return self.gh(f"repos/{self.repo}/rulesets/{self.rules[key]}", "-X", "PUT", "--input", "-", value=target)

    @staticmethod
    def refs(policy):
        return policy["conditions"]["ref_name"]["include"]

    @staticmethod
    def queue(policy):
        return next(rule["parameters"] for rule in policy["rules"] if rule["type"] == "merge_queue")

    def validate(self, policies):
        main = policies["main"]
        checks = {item["context"]: item["integration_id"]
                  for rule in main["rules"] if rule["type"] == "required_status_checks"
                  for item in rule["parameters"]["required_status_checks"]}
        queue = self.queue(main)
        require(main["enforcement"] == "active" and not main["bypass_actors"]
                and checks == {self.required_check: ACTIONS_APP}
                and queue["min_entries_to_merge"] == queue["max_entries_to_merge"] == 1
                and queue["merge_method"] == "MERGE", "sandbox is missing its Actions-only queue policy")

    def original(self):
        baseline = json.loads((self.root / "policy-before.json").read_bytes())
        self.validate(baseline)
        return baseline

    def current(self, capacity):
        baseline, actual = self.original(), self.policies()
        self.validate(actual)
        allowed = deepcopy(baseline["main"])
        self.queue(allowed)["max_entries_to_build"] = capacity
        observed_freeze = deepcopy(actual["freeze"])
        self.refs(observed_freeze)[:] = self.refs(baseline["freeze"])
        require(actual["main"] in (baseline["main"], allowed)
                and actual["namespace"] == baseline["namespace"]
                and observed_freeze == baseline["freeze"]
                and set(self.refs(baseline["freeze"])) <= set(self.refs(actual["freeze"])),
                "sandbox policies changed outside the pilot's authorized fields")
        return actual

    def set_capacity(self, capacity, allowed_capacity):
        target = self.original()["main"]
        self.queue(target)["max_entries_to_build"] = capacity
        if self.current(allowed_capacity)["main"] != target:
            self.replace_rule("main", target)
        require(self.current(allowed_capacity)["main"] == target, "queue capacity did not persist")
        return capacity

    def original_restored(self, actual):
        baseline = self.original()
        require(actual["main"] == baseline["main"]
                and actual["namespace"] == baseline["namespace"]
                and set(self.refs(actual["freeze"])) == set(self.refs(baseline["freeze"])),
                "sandbox policy was not restored")

    def main(self):
        return self.gh(f"repos/{self.repo}/git/ref/heads/main")["object"]["sha"]

    def empty(self):
        return not self.gql('mergeQueue(branch:"main"){entries(first:1){nodes{id}}}')["mergeQueue"]["entries"]["nodes"]

    def pr(self, number):
        require(type(number) is int and number > 0, "invalid pull request number")
        return self.gql(f"pullRequest(number:{number})"
                        "{state headRefOid mergeQueueEntry{id state headCommit{oid}}}")["pullRequest"]

    def verify_heads(self, rows):
        proof = []
        for offset in range(0, len(rows), 8):
            chunk = rows[offset:offset+8]
            fields = []
            for row in chunk:
                receipt = row["receipt"]
                number, head = receipt["number"], receipt["head"]
                require(type(number) is int and re.fullmatch(r"[0-9a-f]{40}", head), "invalid proposal receipt")
                fields += [f"p{number}:pullRequest(number:{number})"
                           "{number state headRefOid headRefName baseRefName headRepository{nameWithOwner} "
                           "author{__typename login}}",
                           f'c{number}:object(expression:"{head}")'
                           "{... on Commit{oid tree{oid} parents(first:2){nodes{oid}}}}"]
            found = self.gql(" ".join(fields))
            for row in chunk:
                receipt, local = row["receipt"], row["workspace"]
                number, head = receipt["number"], receipt["head"]
                pr, commit = found[f"p{number}"], found[f"c{number}"]
                require(pr["state"] == "OPEN" and pr["number"] == number and pr["headRefOid"] == head
                        and pr["headRefName"] == receipt["branch"] and pr["baseRefName"] == "main"
                        and pr["headRepository"]["nameWithOwner"] == self.repo
                        and pr["author"]["__typename"] == "Bot"
                        and pr["author"]["login"].removesuffix("[bot]") == receipt["author"].removesuffix("[bot]")
                        and commit["oid"] == head and commit["tree"]["oid"] == local["git_tree"]
                        and commit["parents"]["nodes"] == [{"oid": local["git_base"]}],
                        f"proposal #{number} does not match its tested Git tree and pin")
                proof.append({"pr": number, "head": head, "tree": local["git_tree"], "parent": local["git_base"]})
        return proof

    def audit_provider(self, row):
        actions, merge = row["actions"], row["merge_commit"]
        run = self.gh(f"repos/{self.repo}/actions/runs/{actions['run_id']}")
        check = self.gh(f"repos/{self.repo}/check-runs/{actions['check_run_id']}")
        require(run["head_sha"] == merge and run["event"] == "merge_group"
                and run["status"] == "completed" and run["conclusion"] == "success"
                and run["check_suite_id"] == actions["check_suite_id"] and run["path"] == self.workflow
                and check["app"]["id"] == ACTIONS_APP and check["head_sha"] == merge
                and check["name"] == self.required_check and check["status"] == "completed"
                and check["conclusion"] == "success" and check["check_suite"]["id"] == actions["check_suite_id"],
                f"merge #{row['number']} did not match the successful Actions check")
        return {"run": run["id"], "check": check["id"]}

    def preflight(self, repo, rows):
        github = GitHub("https://api.github.com")
        checks = []
        for number, row in rows.items():
            check = github.actions_check(repo, row["head"], "pull_request")
            require(check and check["app_id"] == ACTIONS_APP and check["check_run_id"],
                    f"proposal #{number} lacked its successful pull-request check")
            checks.append({"number": number, "head": row["head"], "actions": check})
        for offset in range(0, len(checks), 7):
            aliases = {f"h{row['number']}": row["head"] for row in checks[offset:offset+7]}
            captured = self.gql(" ".join(f'{alias}:object(expression:"{sha}")'
                                         "{... on Commit{oid status{contexts{context}}}}"
                                         for alias, sha in aliases.items()))
            require(all(captured[alias]["oid"] == sha and not (captured[alias]["status"] or {}).get("contexts", [])
                        for alias, sha in aliases.items()), "proposal heads contain manual commit statuses")
        return checks

    def withdrawal(self, number):
        fields = ("state mergeQueueEntry{id} timelineItems(last:8,itemTypes:"
                  "[ADDED_TO_MERGE_QUEUE_EVENT,REMOVED_FROM_MERGE_QUEUE_EVENT,CLOSED_EVENT])"
                  "{nodes{__typename ... on AddedToMergeQueueEvent{id createdAt} "
                  "... on RemovedFromMergeQueueEvent{id reason createdAt} ... on ClosedEvent{id createdAt}}}")
        pr = self.gql(f"pullRequest(number:{number})" + "{" + fields + "}")["pullRequest"]
        add, remove, close = pr["timelineItems"]["nodes"][-3:]
        require(pr["state"] == "CLOSED" and pr["mergeQueueEntry"] is None
                and [event["__typename"] for event in (add, remove, close)]
                == ["AddedToMergeQueueEvent", "RemovedFromMergeQueueEvent", "ClosedEvent"]
                and add["createdAt"] <= remove["createdAt"] <= close["createdAt"] and remove["reason"] == "manual",
                f"native queue withdrawal for #{number} was not verified")
        return {"number": number, "events": [add, remove, close]}

    def audit_merge_graph(self, rows):
        for offset in range(0,len(rows),5):
            chunk=rows[offset:offset+5]
            queries=[]
            for row in chunk:
                n=row['number']
                fields='state mergedAt mergeQueueEntry{id} timelineItems(first:5,itemTypes:[MERGED_EVENT]){nodes{... on MergedEvent{id mergeRefName commit{oid}}}}'
                if row.get('merge_event_kind')=='queue_closure':
                    fields+=' closes:timelineItems(last:2,itemTypes:[CLOSED_EVENT]){nodes{... on ClosedEvent{id createdAt actor{__typename login} closer{__typename ... on Commit{oid}}}}}'
                    fields+=' removals:timelineItems(last:1,itemTypes:[ADDED_TO_MERGE_QUEUE_EVENT,REMOVED_FROM_MERGE_QUEUE_EVENT,MERGED_EVENT]){nodes{... on RemovedFromMergeQueueEvent{id reason createdAt actor{__typename login} beforeCommit{oid} pullRequest{number}}}}'
                queries.append(f'p{n}:pullRequest(number:{n})'+'{'+fields+'}')
                for role,sha in (('h',row['head']),('m',row['merge_commit'])):
                    assert re.fullmatch(r'[0-9a-f]{40}',sha)
                    queries.append(f'{role}{n}:object(expression:"{sha}")'+'{... on Commit{oid status{contexts{context}}}}')
            result=self.gql(' '.join(queries))
            for row in chunk:
                n=str(row['number'])
                pr=result['p'+n]
                assert pr['state']=='MERGED' and pr['mergeQueueEntry'] is None
                if row.get('merge_event_kind')=='queue_closure':
                    assert not pr['timelineItems']['nodes']
                    closure,=(e for e in pr['closes']['nodes'] if e['id']==row['merge_event_id'])
                    removal,=pr['removals']['nodes']
                    assert closure['actor']==removal['actor']=={'__typename':'Bot','login':'github-merge-queue'}
                    assert removal['createdAt']==pr['mergedAt']<=closure['createdAt']==row['queue_closure_at']
                    assert closure['closer']=={'__typename':'Commit','oid':row['queue_closure_commit']}
                    assert row['queue_closure_commit'] in (row['first_parent'],row['merge_commit'])
                    assert removal['id']==row['queue_removal_event_id'] and removal['reason']=='merged'
                    assert removal['beforeCommit']['oid']==row['merge_commit'] and removal['pullRequest']['number']==row['number']
                else:
                    event,=pr['timelineItems']['nodes']
                    assert event['id']==row['merge_event_id'] and event['commit']['oid']==row['merge_commit']
                    assert event['mergeRefName'] in ('main','refs/heads/main')
                for role,sha in (('h',row['head']),('m',row['merge_commit'])):
                    commit=result[role+n]
                    assert commit['oid']==sha and not (commit.get('status') or {}).get('contexts',[])
