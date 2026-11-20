"""Checkpoint transport, recovery fencing, and independent admission limits."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from threading import Lock
import time
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from pi_pilot import Cohort
from pi_pilot_control import activate, archive, checksum, receive, restore
from pi_pilot_lab import Lab
from pi_pilot_runner import Journal, PilotError, Runner


class StoreTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.state = self.root / "outgoing"
        self.state.mkdir(mode=0o700)
        for name in ("plan", "lab", "principals", "policy-before"):
            (self.state / (name + ".json")).write_text("{}\n")
        self.journal = Journal(self.state / "journal")
        self.journal.record("input", "plan", {})
        self.private = Journal(self.state / "private")
        self.private.record("a-g1", "capability", "needed-for-scoped-recovery")

    def test_round_trip_keeps_journal_and_scoped_capability_inside_private_state(self):
        (self.state / "scheduler.key").write_text("do not copy")
        (self.state / "unrelated.json").write_text("do not copy")
        (self.state / "runtime-readiness.json").write_text("{}")
        (self.journal.directory / ".controller.lock").touch()
        store, state = self.root / "standby", self.root / "incoming"
        payload = archive(self.state)
        receipt = receive(store, checksum(payload), payload)
        restored = restore(store, state)
        self.assertEqual(restored, receipt["sha256"])
        self.assertEqual(restore(store, state), receipt["sha256"])
        self.assertEqual(Journal(state / "private").read("a-g1"), self.private.read("a-g1"))
        self.assertTrue((state / "runtime-readiness.json").is_file())
        for name in ("scheduler.key", "unrelated.json", "journal/.controller.lock"):
            self.assertFalse((state / name).exists())
        self.assertFalse((state / "private/a-g1.json").stat().st_mode & 0o077)
        with self.assertRaisesRegex(PilotError, "different"):
            restore(store, self.root / "another")
        with self.assertRaisesRegex(PilotError, "already selected"):
            receive(store, checksum(payload), payload)

    def test_rejects_bad_digest_corruption_and_unrelated_target(self):
        store = self.root / "standby"
        payload = archive(self.state)
        with self.assertRaisesRegex(PilotError, "checksum"):
            receive(store, "0" * 64, payload)
        expected = checksum(payload)
        receive(store, expected, payload)
        with self.assertRaisesRegex(PilotError, "unrelated"):
            restore(store, self.state)
        (store / f"{expected}.tgz").write_bytes(b"bad")
        with self.assertRaisesRegex(PilotError, "checksum"):
            restore(store, self.root / "destination")

    def test_rejects_archive_paths_and_links(self):
        store = self.root / "standby"
        for name, linked in (("../secret.json", False), ("journal/input.json", True)):
            with self.subTest(name=name):
                blob = BytesIO()
                with tarfile.open(fileobj=blob, mode="w:gz") as bundle:
                    member = tarfile.TarInfo(name)
                    if linked:
                        member.type = tarfile.SYMTYPE
                        member.linkname = "/etc/passwd"
                    bundle.addfile(member)
                data = blob.getvalue()
                with self.assertRaisesRegex(PilotError, "unexpected"):
                    receive(store, checksum(data), data)


class TransitionTests(unittest.TestCase):
    def test_activation_retries_partial_switch_without_touching_other_principals(self):
        old, new = "a" * 64, "b" * 64
        role = {"role": "scheduler", "owner": "pilot"}
        handoff = {"from": "mac", "to": "linux", "identities": {"mac": old, "linux": new}}

        class Broker:
            config = {"broker_hosts": ["replica", "leader"], "controller_principals": {old: role, new: role},
                      "broker_config": "/fake", "broker_unit": "pilot.service"}
            plan = {"active_host": "leader"}

            def __init__(self):
                self.state = {host: {"principals": {old: role, "standing": {"role": "scheduler"}}, "plain": True}
                              for host in self.config["broker_hosts"]}
                self.restart = 0
                self.disconnect = True

            def ssh(self, host, args):
                if "restart" in args:
                    self.restart += 1
                    return b""
                return json.dumps(self.state[host]).encode()

            def update_principals(self, host, *, add, remove):
                if self.disconnect and host == "leader":
                    raise ConnectionError("interrupted transfer")
                principals = self.state[host]["principals"]
                for key in remove:
                    principals.pop(key, None)
                principals.update(add)

            def healthy(self):
                return True

            def admin(self, route, *, seconds):
                return {}

        broker = Broker()
        with patch("pi_pilot_control.selected_certificate"):
            with self.assertRaises(ConnectionError):
                activate(broker, handoff)
            self.assertEqual(broker.restart, 0)
            broker.disconnect = False
            self.assertEqual(activate(broker, handoff), {"source_revoked": old, "standby_active": new})
        self.assertEqual(broker.restart, 1)
        for state in broker.state.values():
            self.assertNotIn(old, state["principals"])
            self.assertEqual(state["principals"]["standing"], {"role": "scheduler"})
            self.assertTrue(state["plain"])

    def test_durable_intent_recovered_from_checkpoint_taken_before_assignment(self):
        old = {"task": "sample", "generation": 1, "claim": "a" * 32, "owner": "worker", "base": "b" * 40}
        successor = {"task": "sample", "generation": 2, "episode": "w01-g2", "request": "w01-g2",
                     "owner": "new", "base": "d" * 40}
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "one", Path(folder) / "two"
            source.mkdir(mode=0o700)
            for name in ("plan", "lab", "principals", "policy-before"):
                (source / (name + ".json")).write_text("{}")
            journal = Journal(source / "journal")
            journal.record("input", "plan", {})

            class Adapter:
                posts = 0
                claims = [old | {"phase": "active"}]

                def status(self):
                    return {"claims": self.claims}

                def checkpoint_before_reassignment(self):
                    self.test.assertEqual(self.posts, 0)
                    data = archive(source)
                    return receive(Path(folder) / "store", checksum(data), data)

                def assign(self, op, expected):
                    self.posts += 1
                    claim = op | {"claim": "c" * 32, "phase": "draining"}
                    self.claims = [old | {"phase": "revoked"}, claim]
                    return claim

            adapter = Adapter()
            adapter.test = self
            interrupt = lambda *_: (_ for _ in ()).throw(ConnectionResetError("controller stopped"))
            with self.assertRaises(ConnectionResetError):
                Cohort(journal, {"agents": []}, adapter, interrupt=interrupt).assign(successor, old)
            restore(Path(folder) / "store", target)
            checkpoint = Journal(target / "journal")
            self.assertIn("assignment-intent", checkpoint.read("w01-g2"))
            self.assertNotIn("assignment", checkpoint.read("w01-g2"))
            result = Cohort(checkpoint, {"agents": []}, adapter).assign(successor, old)
            self.assertEqual(result["claim"], "c" * 32)
            self.assertEqual(adapter.posts, 1)


class LimitsTests(unittest.TestCase):
    def test_ssh_cap_is_per_host(self):
        config = {"hosts": {"a": {"destination": "a"}, "b": {"destination": "b"}},
                  "worker_root": "/tmp/lit-live-pinned-pi-example", "broker_hosts": ["a"],
                  "rules": {"main": 1, "freeze": 2, "namespace": 3}, "workflow": "x", "required_check": "x",
                  "limits": {"ssh_per_host": 2, "inference": 3}}
        op = {"id": "w1", "run": "pinned-pi-example", "base": "b" * 40, "host": "a", "work_unit": "one",
              "task": "pinned-pi-example-w1"}
        plan = {"run": op["run"], "base": op["base"], "agents": [op], "active_host": "a",
                "repo": "owner/repo", "endpoint": "https://example.test", "build_capacity": 2}
        running, peak = Counter(), Counter()
        lock = Lock()

        def fake_command(args, data, timeout):
            host = args[-2]
            with lock:
                running[host] += 1
                peak[host] = max(peak[host], running[host])
            time.sleep(0.025)
            with lock:
                running[host] -= 1
            return b"ok"

        with tempfile.TemporaryDirectory() as folder, patch("pi_pilot_lab.command", fake_command):
            lab = Lab(Path(folder), plan, config)
            with ThreadPoolExecutor(max_workers=12) as pool:
                self.assertEqual(list(pool.map(lambda host: lab.ssh(host, ["true"]), "ab" * 6)), [b"ok"] * 12)
            self.assertEqual(lab.inference_limit, 3)
        self.assertEqual(peak, {"a": 2, "b": 2})

    def test_inference_admits_in_waves_without_changing_prepared_pins(self):
        class Adapter:
            inference_limit = 3

            def __init__(self):
                self.prepared = set()
                self.in_flight = self.peak = 0
                self.lock = Lock()

            def prepare(self, op):
                self.prepared.add(op["episode"])
                return {"git_base": op["base"]}

            def generate(self, op, release):
                with self.lock:
                    if len(self.prepared) != 10:
                        raise AssertionError("started before all pins were prepared")
                    self.in_flight += 1
                    self.peak = max(self.in_flight, self.peak)
                time.sleep(0.025)
                with self.lock:
                    self.in_flight -= 1
                return {"episode": op["episode"], "task": op["task"], "workspace": {"git_base": op["base"]}}

        with tempfile.TemporaryDirectory() as root:
            adapter = Adapter()
            runner = Runner(root, adapter)
            ops = [{"episode": f"w{index}", "task": f"w{index}", "base": "b" * 40,
                    "owner": f"w{index}", "generation": 1} for index in range(10)]
            for op in ops:
                runner.bind(op, op | {"claim": "a" * 32})
            self.assertEqual(len(runner.code("initial", ops, lead_seconds=0)), 10)
            self.assertEqual(adapter.peak, 3)


if __name__ == "__main__":
    unittest.main()
