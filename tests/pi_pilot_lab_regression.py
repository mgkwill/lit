"""Checks for cohort windows and validation of Pi-authored source."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tests/fixtures/github-merge-queue")]
from pi_pilot import Cohort
from pi_pilot_lab import open_lab
from pi_pilot_runner import Journal, PilotError
from pi_source_cases import verify


class SourceTests(unittest.TestCase):
    def test_source_functions_pass_the_bounded_behavior_verifier(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "policy.py"
            path.write_text("def retry_delay(failures, limit):\n    return min(max(0, limit), 40 + 25 * max(0, failures))\n"
                            "def range_width(left, right):\n    return max(0, right - left + 1)\n")
            self.assertEqual(verify(path, ["retry-delay", "range-width"]), 338)

    def test_bad_implementations_and_top_level_execution_are_rejected(self):
        for code in ("def range_width(left, right):\n    return 0\n",
                     "import pathlib\ndef range_width(left, right):\n    return max(0, right - left + 1)\n",
                     "def range_width(left, right):\n    max = 0\n    return max(0, right - left + 1)\n"):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as root:
                path = Path(root) / "policy.py"
                path.write_text(code)
                with self.assertRaises(ValueError):
                    verify(path, ["range-width"])


class LabTests(unittest.TestCase):
    def test_window_keeps_both_sides_of_an_exercise_together(self):
        class Adapter:
            pass

        with tempfile.TemporaryDirectory() as root:
            plan = {"agents": [{"id": key} for key in "abcd"], "publication_window": 2,
                    "handoffs": {}, "overlaps": [["a", "b"], ["c", "d"]]}
            cohort = Cohort(Journal(root), plan, Adapter())
            self.assertEqual(cohort.windows(), [[{"id": "a"}, {"id": "b"}], [{"id": "c"}, {"id": "d"}]])
            plan["handoffs"]["a"] = "c"
            with self.assertRaisesRegex(PilotError, "stay in one"):
                cohort.windows()

    def test_checked_in_adapter_resumes_a_saved_completion_from_another_cwd(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            run = "pinned-pi-example"
            plan = {"run": run, "base": "d" * 40, "active_host": "worker", "build_capacity": 2,
                    "endpoint": "https://broker.example.test", "repo": "owner/repo",
                    "agents": [{"id": "a", "run": run, "task": run + "-a", "base": "d" * 40,
                                "host": "worker", "work_unit": "source-a"}]}
            config = {"hosts": {"worker": {"destination": "unused"}}, "broker_hosts": ["worker"],
                      "rules": {"main": 1, "freeze": 2, "namespace": 3}, "worker_root": "/tmp/lit-live-" + run,
                      "workflow": ".github/workflows/check.yml", "required_check": "test"}
            journal = Journal(state / "journal")
            (state / "plan.json").write_text(json.dumps(plan))
            (state / "lab.json").write_text(json.dumps(config))
            open_lab(state, plan, journal)
            journal.record("input", "plan", plan)
            receipt = {"native_queue_empty": True, "test_certificates": []}
            for name, result in {"terminal": {}, "audit": {}, "capacity-restored": 1, "rules-restored": True,
                                 "principals-restored": True, "broker-reloaded": True,
                                 "certificates-denied": [], "removed-worker": True, "cleanup": receipt}.items():
                journal.record("cohort", name, result)
            command = [sys.executable, "-B", str(ROOT / "tools/pi_pilot.py"), "--state", str(state)]
            result = subprocess.run(command, cwd="/", check=True, capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout), receipt)
            (state / "lab.json").write_text(json.dumps(config | {"required_check": "changed"}))
            changed = subprocess.run(command, cwd="/", capture_output=True, text=True)
            self.assertNotEqual(changed.returncode, 0)
            self.assertIn("journal entry changed", changed.stderr)
            (state / "lab.json").unlink()
            (state / "adapter.py").write_text("raise RuntimeError('adapter should never be imported')\n")
            switched = subprocess.run(command, cwd="/", capture_output=True, text=True)
            self.assertNotEqual(switched.returncode, 0)
            self.assertIn("cannot change the adapter type", switched.stderr)
            self.assertNotIn("adapter should never be imported", switched.stderr)


if __name__ == "__main__":
    unittest.main()
