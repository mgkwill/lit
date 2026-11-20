"""Focused recovery checks for the Pi pilot journal and broker handoff."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from pi_pilot_runner import PilotError, Runner


class Broker:
    def __init__(self):
        self.prepared = set()
        self.releases = []
        self.requests = []
        self.publications = {}
        self.registrations = {}
        self.drop = None
        self.expected = set()
        self.fail_status = None

    def prepare(self, op):
        self.prepared.add(op["episode"])
        return {"git_base": op["base"]}

    def generate(self, op, release):
        if self.prepared != self.expected:
            raise AssertionError("the group was released before all pins were prepared")
        self.releases.append(release)
        return {"episode": op["episode"], "task": op["task"], "workspace": {"git_base": op["base"]}}

    def broker(self, op, route, *, claim=None, receipt=None):
        self.requests.append((op["episode"], route))
        key = op["episode"]
        if route in ("publication", "registration"):
            if self.fail_status:
                return 1, {"error": self.fail_status}
            saved = self.publications if route == "publication" else self.registrations
            if key in saved:
                return 0, saved[key]
            return 1, {"error": route + "_not_found"}
        if route == "propose":
            result = {**{f: op[f] for f in ("task", "generation", "base")}, "claim": "d" * 32,
                      "agent": op["owner"], "head": "e" * 40, "number": 9}
            self.publications[key] = result
        elif route == "register":
            result = self.registrations[key] = {"registered": receipt["number"]}
        else:
            raise AssertionError(route)
        if route == self.drop:
            raise ConnectionResetError("acceptance reply was lost")
        return 0, result


class PilotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.backend = Broker()
        self.op = {"episode": "a-1", "task": "a", "generation": 1,
                   "owner": "a", "base": "b" * 40, "request": "a-1"}
        self.claim = {**self.op, "claim": "d" * 32, "capability": "private"}
        self.backend.expected = {"a-1"}
        self.runner = self.open()

    def open(self):
        return Runner(self.directory, self.backend, retry_delay=0)

    def prepare(self):
        self.runner.bind(self.op, self.claim)
        self.runner.code("initial", [self.op], lead_seconds=0)

    def test_all_pins_precede_release_and_restart_reuses_result(self):
        other = {**self.op, "episode": "b-1", "task": "b", "request": "b-1"}
        self.backend.expected.add(other["episode"])
        self.runner.bind(self.op, self.claim)
        self.runner.bind(other, {**other, "claim": "c" * 32})
        result = self.runner.code("initial", [self.op, other], lead_seconds=0)
        self.assertEqual(len(set(self.backend.releases)), 1)
        self.assertEqual(self.open().code("initial", [self.op, other]), result)
        self.assertEqual(len(self.backend.releases), 2)
        self.assertNotIn("private", (Path(self.directory) / "a-1.json").read_text())

    def test_publication_resume_uses_existing_receipt_after_lost_reply(self):
        self.prepare()
        self.backend.drop = "propose"
        with self.assertRaises(ConnectionResetError):
            self.runner.publish(self.op)
        self.assertEqual(self.open().publish(self.op)["number"], 9)
        self.assertEqual([route for _, route in self.backend.requests], ["publication", "propose", "publication"])

    def test_registration_resume_uses_existing_receipt_after_lost_reply(self):
        self.prepare()
        self.runner.publish(self.op)
        self.backend.drop = "register"
        with self.assertRaises(ConnectionResetError):
            self.runner.register(self.op)
        self.assertEqual(self.open().register(self.op), {"registered": 9})
        self.assertEqual(sum(route == "register" for _, route in self.backend.requests), 1)

    def test_unknown_status_does_not_submit(self):
        self.prepare()
        self.backend.fail_status = "publication_outcome_unknown"
        with self.assertRaisesRegex(PilotError, "outcome_unknown"):
            self.runner.publish(self.op)
        self.assertEqual(self.backend.requests, [("a-1", "publication")])

    def test_assignment_cannot_be_replaced_in_the_journal(self):
        self.runner.bind(self.op, self.claim)
        with self.assertRaisesRegex(PilotError, "entry changed"):
            self.open().bind({**self.op, "request": "different"}, self.claim)

    def test_integration_requires_the_same_proposal_and_actions_provider(self):
        self.prepare()
        proposal = self.runner.publish(self.op)
        self.runner.register(self.op)
        proof = {"state": "verified", "receipt": {"proposal": proposal, "merge_commit": "f" * 40,
                 "actions": {"app_id": 15368, "event": "merge_group"}}}
        with self.assertRaisesRegex(PilotError, "unverified"):
            self.runner.integration(self.op, {**proof, "state": "pending"})
        self.assertEqual(self.open().integration(self.op, proof), proof)

    def test_journal_rejects_a_second_controller(self):
        with self.runner.journal.controller():
            with self.assertRaisesRegex(PilotError, "another controller"), self.open().journal.controller():
                self.fail("took a second controller lock")


if __name__ == "__main__":
    unittest.main()
