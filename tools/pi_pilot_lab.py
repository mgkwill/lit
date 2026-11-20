"""Configurable SSH, broker, and GitHub adapter for the pinned Pi pilot."""

import ast
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.client import HTTPException
import io
import json
import os
from pathlib import Path
import re
import shlex
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

from github_queue_coordinator import NoRedirect
from pi_pilot_forge import Forge, GIT_AUTH, command, require
from pi_pilot_runner import Journal, PilotError


REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests/fixtures/github-merge-queue"
CLIENT = r"""
import json, subprocess, sys
result = subprocess.run(sys.argv[1:], input=sys.stdin.buffer.read(), capture_output=True)
try:
    value = json.loads(result.stdout or result.stderr)
except ValueError:
    value = {'error': 'invalid_cli_response'}
print(json.dumps([result.returncode, value]))
"""
UPDATE_PRINCIPALS = r"""
import json, os, sys, tempfile
from pathlib import Path
path = Path(sys.argv[1])
info = path.stat()
change = json.load(sys.stdin)
data = json.loads(path.read_bytes())
principals = data['principals']
for key, value in change['remove'].items():
    if key in principals:
        if principals[key] != value:
            raise ValueError('refusing to remove a changed principal')
        del principals[key]
for key, value in change['add'].items():
    if key in principals and principals[key] != value:
        raise ValueError('refusing to replace a principal')
    principals[key] = value
with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
    os.fchmod(stream.fileno(), info.st_mode & 0o777)
    os.fchown(stream.fileno(), info.st_uid, info.st_gid)
    json.dump(data, stream, indent=2)
    stream.write('\n')
    stream.flush()
    os.fsync(stream.fileno())
os.replace(stream.name, path)
"""
INSTALL_SEED = r"""
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from task_broker_workspace import command, install_source
base = sys.argv[2]
dest = root / 'source' / base
pin = dest / '.lit/broker-base.json'
if not pin.exists():
    bundle = root / (base + '.bundle')
    bundle.write_bytes(sys.stdin.buffer.read())
    git = dest.with_suffix('.git')
    if not git.exists():
        command(('git', 'clone', '--bare', str(bundle), str(git)), root)
    command(('git', 'symbolic-ref', 'HEAD', 'refs/heads/main'), git)
    profile = install_source(root / 'lit', git, dest, base)
    pending = pin.with_suffix('.pending')
    pending.write_text(json.dumps(profile))
    pending.replace(pin)
print(pin.read_text())
"""
REMOVE_WORKER = r"""
from pathlib import Path
import re, shutil, sys
root = Path(sys.argv[1])
run = sys.argv[2]
if root.is_symlink() or root.parent != Path('/tmp') or root.name != 'lit-live-' + run or not re.fullmatch(r'pinned-pi-[a-z0-9]+', run):
    raise ValueError('invalid temporary worker root')
shutil.rmtree(root)
"""


def data_json(value):
    return json.dumps(value).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def payload_archive(paths):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, path in paths.items():
            data = path.read_bytes()
            entry = tarfile.TarInfo(name)
            entry.mode = 0o600
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return buffer.getvalue()


class Lab:
    def __init__(self, state, plan, config):
        self.state = state
        self.plan = plan
        self.config = config
        self.run = plan["run"]
        self.endpoint = plan["endpoint"].rstrip("/")
        self.folder = config["worker_root"]
        require(re.fullmatch(r"pinned-pi-[a-z0-9]+", self.run)
                and self.folder == f"/tmp/lit-live-{self.run}", "invalid run or temporary worker directory")
        require(self.endpoint.startswith("https://")
                and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", plan["repo"]),
                "expected an authenticated TLS broker and a GitHub owner/repository")
        require(plan["active_host"] in config["broker_hosts"]
                and set(config["rules"]) == {"main", "freeze", "namespace"}
                and all(type(number) is int and number > 0 for number in config["rules"].values()),
                "invalid broker placement or sandbox ruleset IDs")
        agents = plan["agents"]
        require(bool(agents) and len({op["id"] for op in agents}) == len(agents)
                and all(op["run"] == self.run and op["base"] == plan["base"]
                        and op["task"].startswith(self.run + "-")
                        and re.fullmatch(r"[a-z0-9-]+", op["id"])
                        and re.fullmatch(r"[a-z0-9-]+", op["work_unit"]) for op in agents),
                "invalid initial cohort bindings")
        self.hosts = sorted({op["host"] for op in agents})
        require(set(self.hosts + config["broker_hosts"]) <= set(config["hosts"]),
                "every broker and worker needs a configured SSH destination")
        self.forge = Forge(state, plan["repo"], config)
        self.private = Journal(state / "private")
        self.main = self.forge.main
        self.capacity = plan["build_capacity"]
        self.controller = None

    def authorize_controller(self):
        if self.controller is not None:
            self.admin("status", seconds=0)
            self.journal.record("controllers", self.controller, {"host": socket.gethostname(),
                "certificate": digest(ssl.PEM_cert_to_DER_cert(Path(self.config["scheduler_certificate"]).read_text()))})

    def load(self, name):
        return json.loads((self.state / f"{name}.json").read_bytes())

    def save(self, name, value):
        with tempfile.NamedTemporaryFile(dir=self.state, delete=False) as stream:
            stream.write(data_json(value))
            stream.flush()
            os.fsync(stream.fileno())
        Path(stream.name).replace(self.state / f"{name}.json")
        return value

    def ssh(self, host, args, data=None, *, worker=False, timeout=180):
        profile = self.config["hosts"][host]
        options = ["-oBatchMode=yes", "-oStrictHostKeyChecking=yes", "-oConnectTimeout=8"]
        for key, option in (("hostname", "HostName"), ("host_key_alias", "HostKeyAlias"),
                            ("known_hosts", "UserKnownHostsFile"), ("identity_file", "IdentityFile")):
            if key in profile:
                options.extend(["-o", f"{option}={profile[key]}"])
        if "identity_file" in profile:
            options.append("-oIdentitiesOnly=yes")
        if worker and profile.get("worker_user"):
            args = ["sudo", "-n", "-u", profile["worker_user"], *args]
        if profile.get("local"):
            return command(args, data, timeout)
        return command(["ssh", *options, "--", profile["destination"], shlex.join(args)], data, timeout)

    def admin(self, route, body=None, seconds=300):
        context = ssl.create_default_context(cafile=self.config["server_ca"])
        context.load_cert_chain(self.config["scheduler_certificate"], self.config["scheduler_key"])
        opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
        data = data_json(body) if body is not None else None
        deadline = time.monotonic() + seconds
        request = Request(f"{self.endpoint}/{route}", data, {"Content-Type": "application/json"})
        while True:
            try:
                with opener.open(request, timeout=45) as response:
                    return json.load(response)
            except HTTPError as error:
                with error:
                    value = json.load(error)
                if error.code not in (429, 503) or value.get("retryable") is not True or time.monotonic() >= deadline:
                    raise PilotError(f"{route} returned HTTP {error.code} ({value.get('error')})") from None
            except (URLError, TimeoutError, ConnectionError, HTTPException) as error:
                if (isinstance(getattr(error, "reason", error), ssl.SSLCertVerificationError)
                        or time.monotonic() >= deadline):
                    raise
            time.sleep(1)

    def status(self):
        return self.admin("status")

    def proposals(self, state):
        return {row["number"]: row for row in state["proposals"] if (row.get("task") or "").startswith(self.run + "-")}

    def assign(self, op, expected):
        entry = op["episode"] + "-assignment-submissions"
        self.save(entry, (self.load(entry) if (self.state / f"{entry}.json").exists() else [])
                  + [{"request": op["request"], "expect": expected}])
        return self.admin("assign", {"task": op["task"], "expect": expected, "owner": op["owner"],
                                     "base": op["base"], "request": op["request"] + "-assignment"})

    def agent(self, op, route, *, body=None, claim=None, workspace=False):
        profile = f"{self.folder}/{op['id']}"
        remote = ["python3", "-B", f"{self.folder}/tools/task_broker_client.py", "--url", self.endpoint,
                  "--cert", profile + "/client.crt", "--key", profile + "/client.key",
                  "--ca", self.folder + "/ca.crt", "--no-wait"]
        if claim is not None:
            remote += ["--claim", claim]
        if workspace:
            remote += ["--workspace", f"{self.folder}/attempts/{op['episode']}/workspace",
                       "--lit", self.folder + "/lit"]
        return json.loads(self.ssh(op["host"], ["python3", "-B", "-c", CLIENT, *remote, route],
                                   data_json(body) if body is not None else None, worker=True, timeout=310))

    def attach(self, op, chosen):
        status, data = self.agent(op, "claims")
        require(status == 0, "worker could not retrieve its scoped assignment")
        rows = [row for row in data["claims"] if row["claim"] == chosen["claim"]]
        require(len(rows) == 1 and all(rows[0][key] == value for key, value in chosen.items())
                and rows[0]["phase"] == "active" and rows[0].get("capability"), "worker assignment differs")
        self.private.record(op["episode"], "capability", rows[0]["capability"])

    def broker(self, op, route, *, claim=None, receipt=None, physical=None):
        if route not in ("propose", "register"):
            return self.agent(op, route, claim=claim)
        capability = self.private.read(op["episode"])["capability"]
        if route == "register":
            return self.agent(op, route, body={"proposal": receipt, "capability": capability})
        attempt = physical or self.journal.read(op["episode"])["coding"]["worker_episode"]
        assignment = self.journal.read(op["episode"])["binding"]
        body = {key: assignment[key] for key in ("task", "claim", "base", "generation")}
        body.update(capability=capability, request=op["request"])
        return self.agent(op | {"episode": attempt}, route, body=body, workspace=True)

    def stale(self, op, physical, row):
        publish = self.broker(op, "propose", physical=physical)
        register = self.broker(op, "register", receipt=row["receipt"])
        require(all(code == 1 and result.get("error") == "assignment_revoked" for code, result in (publish, register)),
                "the stale owner was not rejected by both publication and registration")
        return self.save("stale-" + op["episode"], {"pr": row["receipt"]["number"],
                         "publication": publish[1], "registration": register[1]})

    def worker(self, op, action):
        return json.loads(self.ssh(op["host"], ["python3", "-B", self.folder + "/pi-worker.py", self.folder, action],
                                   data_json(op), worker=True, timeout=720))

    def prepare(self, op):
        return self.worker(op, "prepare")

    def generate(self, op, release):
        for index in range(1, 4):
            episode = op["episode"] if index == 1 else op["episode"] + f"-retry{index}"
            selected = op | {"episode": episode, "release_ms": release}
            if index > 1:
                selected["feedback"] = "Follow all function contracts and keep the requested file changes scoped."
            try:
                result = self.worker(selected, "code")
            except (PilotError, subprocess.TimeoutExpired) as error:
                (self.state / f"{episode}-error.txt").write_text(str(error) + "\n")
                continue
            finally:
                for name in ("pi.jsonl", "pi.stderr", "prompt.txt"):
                    remote = f"{self.folder}/attempts/{episode}/{name}"
                    try:
                        trace = self.ssh(op["host"], ["cat", "--", remote], worker=True, timeout=25)
                    except (PilotError, subprocess.TimeoutExpired):
                        continue
                    (self.state / f"{episode}-{name}").write_bytes(trace)
            execution = result["execution"]
            require(execution["inference_host"] == op["inference_host"]
                    and execution["inference_address"] == op["inference_address"],
                    "worker used a different inference endpoint")
            print(f"{op['episode']} on {op['host']} passed {result['local_cases']} checks", flush=True)
            return result | {"episode": op["episode"], "worker_episode": episode}
        raise PilotError(f"coding attempts exhausted for {op['episode']}")

    def record_publication(self, row):
        self.save("publication-" + row["episode"], row)
        print(f"{row['episode']} opened or recovered PR #{row['receipt']['number']}", flush=True)

    def broker_projection(self, config):
        controllers = self.config.get("controller_principals", {})
        for fingerprint in set(controllers) & config["principals"].keys():
            require(config["principals"][fingerprint] == controllers[fingerprint], "controller principal changed")
            del config["principals"][fingerprint]
        return digest(json.dumps(config, sort_keys=True).encode())

    def broker_config_digest(self, host):
        response = self.ssh(host, ["sudo", "-n", "cat", "--", self.config["broker_config"]])
        return self.broker_projection(json.loads(response))

    def update_principals(self, host, *, add=None, remove=None):
        self.ssh(host, ["sudo", "-n", "python3", "-B", "-c", UPDATE_PRINCIPALS, self.config["broker_config"]],
                 data_json({"add": add or {}, "remove": remove or {}}))

    def enroll(self):
        self.forge.original_restored(self.forge.current(self.capacity))
        require(self.forge.empty(), "native queue must be empty before enrollment")
        principals = self.load("principals")
        require(len(principals) == len(self.plan["agents"])
                and {data["owner"] for data in principals.values()} == {op["owner"] for op in self.plan["agents"]},
                "temporary principal identities differ from the run plan")
        for host in self.config["broker_hosts"]:
            marker = "broker-config-" + host
            if not self.private.read(marker):
                current = self.ssh(host, ["sudo", "-n", "cat", "--", self.config["broker_config"]])
                config = json.loads(current)
                require(not (set(config["principals"]) & set(principals)), "pilot certificate was already enrolled")
                self.private.record(marker, "original-sha256", self.broker_projection(config))
            self.update_principals(host, add=principals)
        return self.reload_broker()

    def healthy(self):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                result = self.admin("health", seconds=0)
                if result["last_error"] is None and time.time() - result.get("last_cycle", 0) < 30:
                    return True
            except (PilotError, URLError, TimeoutError):
                pass
            time.sleep(2)
        raise PilotError("broker health did not recover")

    def reload_broker(self):
        self.ssh(self.plan["active_host"], ["sudo", "-n", "systemctl", "restart", self.config["broker_unit"]])
        return self.healthy()

    def stage_workers(self):
        files = {"pi-worker.py": FIXTURES / "pi-workspace-worker.py"}
        files.update({name: FIXTURES / name for name in ("pi_lit_contracts.py", "pi_source_cases.py", "pi_rust_workload.py")})
        archive = payload_archive(files)
        for host in self.hosts:
            self.ssh(host, ["tar", "-xf", "-", "-C", self.folder], archive, worker=True)
        return True

    def seed_git(self, base):
        require(re.fullmatch(r"[0-9a-f]{40}", base), "invalid Git pin")
        path = self.state / f"seed-{base[:12]}.git"
        if not path.exists():
            command(["git", "init", "--bare", "--template=", "--object-format=sha1", "-b", "main", str(path)])
        git = ["git", "-C", str(path)]
        status = subprocess.run(git + ["rev-parse", "--verify", "refs/heads/main"],
                                capture_output=True, timeout=20)
        if status.returncode or status.stdout.strip().decode() != base:
            command(git + GIT_AUTH + ["fetch", "--no-tags", self.forge.origin, base])
            command(git + ["update-ref", "refs/heads/main", base])
        return git

    def stage_seed(self, host, base):
        bundle = command(self.seed_git(base) + ["bundle", "create", "-", "refs/heads/main"])
        profile = json.loads(self.ssh(host, ["python3", "-B", "-c", INSTALL_SEED, self.folder, base],
                                      bundle, worker=True, timeout=300))
        require(profile["git_base"] == base, "worker seed differs from the requested Git commit")
        return profile

    def queued(self, number):
        row = self.forge.pr(number)
        require(row["state"] == "OPEN", f"PR #{number} closed before the planned handoff")
        return row["mergeQueueEntry"]

    def closed(self, number):
        row = self.forge.pr(number)
        require(row["state"] == "CLOSED" and not row["mergeQueueEntry"], f"PR #{number} was not retired")
        return row

    def set_build_capacity(self, capacity):
        result = self.forge.set_capacity(capacity, self.capacity)
        print(f"Verified native build capacity {result} with the Actions-only gate", flush=True)
        return result

    def restore_capacity(self):
        return self.set_build_capacity(self.forge.queue(self.forge.original()["main"])["max_entries_to_build"])

    def freeze(self, rows, label):
        proof = self.forge.verify_heads(rows)
        policy = self.forge.current(self.capacity)["freeze"]
        refs = self.forge.refs(policy)
        for row in rows:
            ref = "refs/heads/" + row["receipt"]["branch"]
            require(re.fullmatch(r"refs/heads/agents/broker/attempt/[0-9a-f]{32}", ref), "invalid proposal ref")
            if ref not in refs:
                refs.append(ref)
        self.forge.replace_rule("freeze", policy)
        require(set(self.forge.refs(self.forge.current(self.capacity)["freeze"])) == set(refs),
                "proposals were not frozen before registration")
        self.save(label + "-verified-trees", proof)
        return [item["pr"] for item in proof]

    def release_freezes(self, rows):
        refs = set()
        for row in rows:
            receipt = row["receipt"]
            ref = "refs/heads/" + receipt["branch"]
            require(re.fullmatch(r"refs/heads/agents/broker/attempt/[0-9a-f]{32}", ref), "invalid proposal ref")
            pr = self.forge.pr(receipt["number"])
            require(pr["state"] in ("CLOSED", "MERGED") and pr["mergeQueueEntry"] is None,
                    f"PR #{receipt['number']} must be terminal before unfreezing")
            refs.add(ref)
        policy = self.forge.current(self.capacity)["freeze"]
        original = set(self.forge.refs(self.forge.original()["freeze"]))
        require(not (refs & original), "refusing to release a preexisting freeze")
        desired = [ref for ref in self.forge.refs(policy) if ref not in refs]
        if self.forge.refs(policy) != desired:
            self.forge.refs(policy)[:] = desired
            self.forge.replace_rule("freeze", policy)
        require(set(self.forge.refs(self.forge.current(self.capacity)["freeze"])) == set(desired),
                "terminal proposal freezes did not release")
        return sorted(refs)

    def item_paths(self, operation):
        unit = operation["work_unit"]
        if operation.get("workload") == "rust-regression":
            return [operation["rust"]["file"]]
        if operation.get("workload") == "source":
            return [f"qualification/{self.run}/{unit}/policy.py"]
        return [f"tests/agent-contracts/{self.run}/{unit}/{name}" for name in ("README.md", "cases.json")]

    def conflict(self, op, row, base):
        head = row["receipt"]["head"]
        git = self.seed_git(self.plan["base"])
        command(git + GIT_AUTH + ["fetch", "--no-tags", self.forge.origin, base, head])
        result = subprocess.run(git + ["merge-tree", "--write-tree", base, head], capture_output=True, timeout=75)
        paths = self.item_paths(op)
        require(result.returncode == 1 and b"CONFLICT" in result.stdout
                and any(path.encode() in result.stdout for path in paths), "Git did not reproduce the expected conflict")
        return self.save("conflict-" + op["work_unit"], {"number": row["receipt"]["number"], "basis": base,
                         "proposal_head": head, "paths": paths, "merge_tree_sha256": digest(result.stdout)})

    def verify_tree(self, git, main, overlaps):
        sys.path.insert(0, str(FIXTURES))
        from pi_lit_contracts import verify as verify_contract
        from pi_source_cases import function_map, verify as verify_source
        from pi_rust_workload import projected

        operations, cases = {}, defaultdict(list)
        for op in self.plan["agents"]:
            unit = op["work_unit"]
            operations[unit] = op
            cases[unit].extend(op["contract_cases"])
        base = self.plan["base"]
        expected = {path for op in operations.values() for path in self.item_paths(op)}
        actual = command(git + ["diff", "--name-only", base, main]).decode().splitlines()
        require(set(actual) == expected, "final Git tree has unexpected changed paths")
        proof, prior_preserved = [], {}
        rust = {}
        for unit, selected in sorted(cases.items()):
            op = operations[unit]
            if op.get("workload") == "rust-regression":
                target = op["rust"]
                path = target["file"]
                original = rust.get(path)
                if original is None:
                    original = command(git + ["show", f"{base}:{path}"])
                if unit in overlaps:
                    fresh = command(git + ["show", f"{overlaps[unit]['fresh_base']}:{path}"])
                    present = [case for case in selected if case.encode() in fresh]
                    absent = [case for case in selected if case.encode() not in fresh]
                    require(len(present) == len(absent) == 1, "Rust repair pin did not contain exactly its peer's test")
                    selected = present + absent
                    prior_preserved[unit] = True
                rust[path] = projected(original, target, selected)
                proof.append({"unit": unit, "cases": selected, "assertions": len(selected), "path": path})
                continue
            scope = self.state / "final-tree" / unit
            scope.mkdir(parents=True, exist_ok=True)
            files = {}
            paths = self.item_paths(op)
            for path in paths:
                checkout = scope / Path(path).name
                checkout.write_bytes(command(git + ["show", f"{main}:{path}"]))
                files[path] = digest(checkout.read_bytes())
            if op.get("workload") == "source":
                source = scope / "policy.py"
                count = verify_source(source, selected)
                if unit in overlaps:
                    previous = function_map(command(git + ["show", f"{overlaps[unit]['fresh_base']}:{paths[0]}"]))
                    current = function_map(source.read_bytes())
                    require(len(previous) == 1 and set(previous) < set(current)
                            and all(ast.dump(func) == ast.dump(current[name]) for name, func in previous.items()),
                            "conflict repair did not preserve the peer's source function")
                    prior_preserved[unit] = True
            else:
                count = verify_contract(Path(self.config["lit_binary"]), scope, selected)
                if unit in overlaps:
                    path = next(path for path in paths if path.endswith("cases.json"))
                    previous = json.loads(command(git + ["show", f"{overlaps[unit]['fresh_base']}:{path}"]))["cases"]
                    current = json.loads((scope / "cases.json").read_bytes())["cases"]
                    require(len(previous) == 1 and all(case in current for case in previous),
                            "conflict repair did not preserve the peer's contract case")
                    prior_preserved[unit] = True
            proof.append({"unit": unit, "cases": selected, "assertions": count, "files": files})
        for path, expected in rust.items():
            actual = command(git + ["show", f"{main}:{path}"])
            require(actual == expected, f"final Rust module {path} differs from its reviewed insertions")
        for item in proof:
            if "path" in item:
                item["files"] = {item["path"]: digest(rust[item["path"]])}
        return proof, prior_preserved

    def audit(self, final, steps):
        main, base = self.main(), self.plan["base"]
        git = self.seed_git(base)
        command(git + GIT_AUTH + ["fetch", "--no-tags", self.forge.origin, main])
        command(git + ["fsck", "--full", "--strict"])
        history = set(command(git + ["rev-list", "--first-parent", f"{base}..{main}"]).decode().splitlines())
        merged, retired = [], []
        fields = ("merge_event_id", "merge_event_kind", "queue_closure_commit", "queue_closure_at",
                  "queue_removal_event_id")
        rows = {int(number): row for number, row in final.items()}
        for number, row in rows.items():
            pr = self.forge.pr(number)
            require(pr["mergeQueueEntry"] is None, f"PR #{number} remains queued")
            if row["phase"] == "ownership_retired":
                require(pr["state"] == "CLOSED", f"PR #{number} was not closed")
                retired.append(number)
                continue
            record = row["integration"]
            receipt = record["receipt"]
            merge = receipt["merge_commit"]
            parents = command(git + ["rev-list", "--parents", "-n", "1", merge]).decode().split()
            require(pr["state"] == "MERGED" and row["phase"] == "merged" and record["state"] == "verified"
                    and receipt["proposal"]["number"] == number
                    and parents == [merge, receipt["first_parent"], row["head"]], f"unexpected Git parents for #{number}")
            merged.append({"number": number, "head": row["head"], "merge_commit": merge,
                           "first_parent": parents[1], "actions": receipt["actions"]}
                          | {key: receipt[key] for key in fields if key in receipt})
        require({row["merge_commit"] for row in merged} == history and len(history) == len(self.plan["agents"]),
                "Git first-parent history differs from the verified cohort")
        self.forge.audit_merge_graph(merged)
        with ThreadPoolExecutor(max_workers=4) as workers:
            checks = list(workers.map(self.forge.audit_provider, merged))
        require(len({item["run"] for item in checks}) == len(merged), "merge receipts reused an Actions run")
        proof, preserved = self.verify_tree(git, main, steps["overlaps"])
        result = self.save("integration-proof", {"main": main, "merged": merged, "retired": retired,
                           "verified_units": proof, "preserved_peers": preserved, "actions_audits": checks,
                           "preflight_actions": self.forge.preflight(self.plan["repo"], rows),
                           "withdrawals": {key: self.forge.withdrawal(row["old_pr"])
                                           for key, row in steps["handoffs"].items()},
                           "manual_statuses": 0, "git_fsck": True})
        print(f"Verified {len(merged)} exact merges and {sum(unit['assertions'] for unit in proof)} final-tree checks",
              flush=True)
        return result

    def restore_rules(self, final):
        current = self.forge.current(self.capacity)
        target = self.forge.original()["freeze"]
        found = set(self.forge.refs(current["freeze"]))
        original = set(self.forge.refs(target))
        allowed = {"refs/heads/" + row["branch"] for row in final.values()}
        require(original <= found <= original | allowed, "unrelated branch freeze would be lost")
        if found != original:
            self.forge.replace_rule("freeze", target)
        self.forge.original_restored(self.forge.current(self.capacity))
        return True

    def restore_principals(self):
        principals = self.load("principals")
        for host in self.config["broker_hosts"]:
            self.update_principals(host, remove=principals)
            require(self.broker_config_digest(host) == self.private.read(f"broker-config-{host}")["original-sha256"],
                    f"broker configuration on {host} did not return to its original state")
        return True

    def denied(self, op):
        status, data = self.agent(op, "claims")
        require(status == 1 and data.get("error") == "forbidden", "temporary identity was not denied")
        return {"id": op["id"], "host": op["host"], "denied": True}

    def worker_exists(self, host):
        script = "from pathlib import Path; import sys; print(Path(sys.argv[1]).exists() or Path(sys.argv[1]).is_symlink())"
        result = self.ssh(host, ["python3", "-B", "-c", script, self.folder], worker=True).strip()
        require(result in (b"True", b"False"), "worker directory existence was not verified")
        return result == b"True"

    def remove_worker(self, host):
        self.ssh(host, ["python3", "-B", "-c", REMOVE_WORKER, self.folder, self.run], worker=True)

    def verify_cleanup(self, final, proof, denied, hosts):
        self.forge.original_restored(self.forge.current(self.capacity))
        self.healthy()
        require({str(number): row for number, row in self.proposals(self.status()).items()} == final
                and self.main() == proof["main"] and self.forge.empty()
                and all(not self.worker_exists(host) for host in hosts), "pilot cleanup did not reconcile")
        return self.save("cleanup", {"original_policy_restored": True, "test_certificates": denied,
                         "worker_directories_removed": hosts, "healthy_after_restart": True,
                         "native_queue_empty": True, "receipt_records_unchanged": True, "main_matches_the_audit": True})


def open_lab(state, plan, journal, controller=None):
    require(__debug__, "run without Python optimization to keep all verification checks enabled")
    config = (state / "lab.json").read_bytes()
    tools = Path(__file__).parent
    files = [tools / name for name in ("pi_pilot_lab.py", "pi_pilot_forge.py", "pi_pilot.py", "pi_pilot_runner.py",
                                      "github_queue_coordinator.py", "task_broker_client.py", "task_broker_workspace.py",
                                      "task_ownership.py")]
    files += [FIXTURES / name for name in ("pi-workspace-worker.py", "pi_lit_contracts.py", "pi_source_cases.py",
                                          "pi_rust_workload.py")]
    journal.record("input", "lab-config-sha256", digest(config))
    journal.record("input", "lab-source-sha256", {path.name: digest(path.read_bytes()) for path in files})
    data = json.loads(config)
    profiles = data.get("controllers")
    if profiles is not None:
        require(controller in profiles, "select a configured controller profile")
        profile = profiles[controller]
        require(set(profile) <= {"hosts", "scheduler_certificate", "scheduler_key", "server_ca", "lit_binary"},
                "controller profile may only change local transport and credentials")
        data |= profile
    elif controller is not None:
        raise PilotError("--controller requires named profiles")
    lab = Lab(state, plan, data)
    lab.controller = controller
    return lab
