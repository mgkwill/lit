"""Recovery tests for a whole Pi cohort after accepted external effects."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from pi_pilot import Cohort, binding
from pi_pilot_runner import Journal, PilotError


class Interrupted(RuntimeError):
    pass


class Lab:
    def __init__(self, predecessor):
        self.claims = [predecessor | {"phase": "active"}]
        self.assignments = []
        self.workers = {"host-a", "host-e"}
        self.removals = []
        self.cleanup_calls = []

    def status(self):
        return {"claims": self.claims}

    def assign(self, op, expected):
        self.assignments.append((op["request"], expected))
        self.claims[0] |= {"phase": "revoked"}
        claim = {field: op[field] for field in ("task", "generation", "owner", "base")}
        claim.update(claim="c" * 32, phase="draining", capability="private")
        self.claims.append(claim)
        return claim

    def restore_rules(self, final):
        self.cleanup_calls.append("rules")
        return True

    def restore_principals(self):
        self.cleanup_calls.append("principals")
        return True

    def reload_broker(self):
        self.cleanup_calls.append("reload")
        return True

    def denied(self, op):
        self.cleanup_calls.append(op["id"])
        return op["id"]

    def worker_exists(self, host):
        return host in self.workers

    def remove_worker(self, host):
        self.removals.append(host)
        self.workers.remove(host)

    def verify_cleanup(self, final, proof, denied, hosts):
        self.cleanup_calls.append("verify")
        return {"hosts": hosts, "denied": denied}


class CohortTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.old = {"task": "sample", "generation": 1, "claim": "a" * 32, "owner": "a", "base": "b" * 40}
        self.op = {"task": "sample", "generation": 2, "request": "sample-g2", "owner": "b",
                   "base": "d" * 40, "episode": "a-g2"}
        self.plan = {"agents": [{"host": "host-a", "id": "a"}, {"host": "host-e", "id": "b"}]}
        self.lab = Lab(self.old)

    def controller(self, interrupt=lambda *_: None):
        return Cohort(Journal(self.directory), self.plan, self.lab, interrupt=interrupt)

    def test_reassignment_restart_adopts_the_exact_generation_without_a_second_submission(self):
        def interrupt(stage, receipt):
            self.assertEqual(stage, "reassignment")
            self.assertEqual(receipt["claim"], "c" * 32)
            raise Interrupted

        with self.assertRaises(Interrupted):
            self.controller(interrupt).assign(self.op, self.old)
        self.assertNotIn("assignment", Journal(self.directory).read("a-g2"))
        self.lab.claims[-1]["phase"] = "active"
        resumed = self.controller()
        claim = resumed.assign(self.op, self.old)
        self.assertEqual(resumed.journal.read("a-g2")["adopted-assignment"], claim)
        self.assertEqual(claim, binding(self.lab.claims[-1]))
        self.assertEqual(self.lab.assignments, [("sample-g2", 1)])
        self.assertNotIn("private", (self.directory / "a-g2.json").read_text())

    def test_reassignment_refuses_a_successor_with_another_owner(self):
        self.lab.assign(self.op | {"owner": "someone-else"}, 1)
        with self.assertRaisesRegex(PilotError, "different assignment"):
            self.controller().assign(self.op, self.old)
        self.assertEqual(len(self.lab.assignments), 1)

    def test_reassignment_refuses_to_recreate_a_lost_recorded_assignment(self):
        self.controller().assign(self.op, self.old)
        self.lab.claims = [self.old | {"phase": "active"}]
        with self.assertRaisesRegex(PilotError, "lost a recorded assignment"):
            self.controller().assign(self.op, self.old)
        self.assertEqual(len(self.lab.assignments), 1)

    def test_cleanup_restart_observes_the_deleted_worker_and_preserves_denial_proof(self):
        def interrupt(stage, receipt):
            self.assertEqual((stage, receipt), ("worker-removal", {"host": "host-a"}))
            raise Interrupted

        with self.assertRaises(Interrupted):
            self.controller(interrupt).cleanup({}, {})
        self.assertNotIn("removed-host-a", Journal(self.directory).read("cohort"))
        resumed = self.controller()
        self.assertEqual(resumed.cleanup({}, {}), {"hosts": ["host-a", "host-e"], "denied": ["a", "b"]})
        self.assertTrue(resumed.journal.read("cohort")["removed-host-a"]["already_absent"])
        self.assertEqual(self.lab.removals, ["host-a", "host-e"])
        self.assertEqual(self.lab.cleanup_calls[:3], ["rules", "principals", "reload"])
        self.assertCountEqual(self.lab.cleanup_calls[3:], ["a", "b", "verify"])


if __name__ == "__main__":
    unittest.main()
