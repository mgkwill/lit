#!/usr/bin/env python3
"""Run or resume a pinned Pi cohort with a trusted lab adapter."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import time

from pi_pilot_runner import Journal, PilotError, Runner


CLAIM_FIELDS = ("task", "generation", "owner", "base", "claim")


def binding(claim):
    return {field: claim[field] for field in CLAIM_FIELDS}


class Cohort:
    def __init__(self, journal, plan, lab, *, interrupt=lambda *_: None):
        self.journal = journal
        self.plan = plan
        self.lab = lab
        self.interrupt = interrupt
        self.runner = Runner(journal.directory, lab, deadline=1200)
        self.lab.journal = journal
        self.steps = "cohort"
        self.agents = {op["id"]: op for op in plan["agents"]}

    def step(self, name, action):
        state = self.journal.read(self.steps)
        if name not in state:
            return self.journal.record(self.steps, name, action())
        return state[name]

    def wait(self, name, probe, seconds=2700):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = probe()
            if result is not None:
                return result
            time.sleep(2)
        raise PilotError(f"timed out waiting for {name}")

    def current_claim(self, op, state):
        history = [row for row in state["claims"] if row["task"] == op["task"]]
        current = max(history, key=lambda row: row["generation"], default=None)
        if current is None or current["generation"] == op["generation"] - 1:
            return None
        if any(current[key] != op[key] for key in CLAIM_FIELDS[:-1]):
            raise PilotError(f"the broker has a different assignment for {op['task']}")
        if current["phase"] not in ("active", "draining"):
            raise PilotError(f"the intended assignment was revoked for {op['task']}")
        return current

    def assign(self, op, previous=None):
        """Adopt the same broker generation after an interrupted assignment."""
        episode = op["episode"]
        intent = self.journal.record(episode, "assignment-intent", {
            "request": op["request"], **{key: op[key] for key in CLAIM_FIELDS[:-1]}, "previous": previous,
        })
        state = self.lab.status()
        claim = self.current_claim(op, state)
        if previous is not None:
            old = [row for row in state["claims"] if row["claim"] == intent["previous"]["claim"]]
            if (len(old) != 1 or binding(old[0]) != previous
                    or old[0]["phase"] not in (("revoked", "retired") if claim else ("active",))):
                raise PilotError(f"the broker has a different predecessor for {op['task']}")
        recorded = self.journal.read(episode).get("assignment")
        if claim is None:
            if recorded:
                raise PilotError(f"the broker lost a recorded assignment for {op['task']}")
            claim = self.lab.assign(op, op["generation"] - 1)
            if previous is not None:
                self.interrupt("reassignment", binding(claim))
        elif recorded is None and previous is not None:
            self.journal.record(episode, "adopted-assignment", binding(claim))
        if any(claim[key] != intent[key] for key in CLAIM_FIELDS[:-1]):
            raise PilotError(f"assignment response differs for {op['task']}")
        return self.journal.record(episode, "assignment", binding(claim))

    def attach(self, op, claim):
        self.lab.attach(op, claim)
        self.runner.bind(op, claim)

    def reassign(self, old, peer, pin, cases, row):
        op = old | {name: peer[name] for name in ("id", "owner", "host", "inference_address")}
        generation = old["generation"] + 1
        op |= {"base": pin, "generation": generation, "episode": f"{old['id']}-g{generation}",
               "request": f"{old['task']}-g{generation}", "contract_cases": cases}
        before = self.journal.read(old["episode"])
        self.journal.record(op["episode"], "replacement-operation", op)
        chosen = self.assign(op, before["binding"])
        physical = before["coding"]["worker_episode"]
        self.step(op["episode"] + "-stale", lambda: self.lab.stale(old, physical, row))
        number = row["receipt"]["number"]

        def ready():
            state = self.lab.status()
            claim = self.current_claim(op, state)
            old_row = self.lab.proposals(state).get(number, {})
            if claim and claim["phase"] == "active" and old_row.get("phase") == "ownership_retired":
                if binding(claim) != chosen:
                    raise PilotError("the active successor differs from the selected assignment")
                return self.lab.closed(number)
            return None

        self.step(op["episode"] + "-retired", lambda: self.wait(f"retirement of #{number}", ready))
        self.attach(op, chosen)
        return op

    def parallel(self, action, items, width=6):
        with ThreadPoolExecutor(max_workers=width) as pool:
            return list(pool.map(action, items))

    def publish(self, op):
        receipt = self.runner.publish(op)
        row = self.journal.read(op["episode"])["coding"] | {"receipt": receipt}
        self.lab.record_publication(row)
        return row

    def stage(self, group, operations):
        self.runner.code(group, operations, lead_seconds=15)
        rows = self.parallel(self.publish, operations)
        self.step(group + "-frozen", lambda: self.lab.freeze(rows, group))
        return rows

    def initial(self):
        self.step("enrolled", self.lab.enroll)
        self.step("workers-staged", self.lab.stage_workers)
        for op in self.agents.values():
            self.attach(op, self.assign(op))
        return dict(zip(self.agents, self.stage("initial", list(self.agents.values())), strict=True))

    def native_handoffs(self, initial):
        targets = self.plan["handoffs"]
        for key in targets:
            self.runner.register(self.agents[key])
        queued = {}
        replacements = {}
        def handoff_one():
            for key, target in targets.items():
                if key in replacements:
                    continue
                marker = key + "-queued"
                entry = self.journal.read(self.steps).get(marker)
                if entry is None:
                    entry = self.lab.queued(initial[key]["receipt"]["number"])
                    if entry is None:
                        continue
                    self.journal.record(self.steps, marker, entry)
                queued[key] = entry
                old = self.agents[key]
                replacements[key] = self.reassign(old, self.agents[target], old["base"],
                                                  old["contract_cases"], initial[key])
            return True if len(replacements) == len(targets) else None

        self.wait("native queue handoffs", handoff_one)
        return replacements, queued

    def conflicts(self, initial):
        work = {}
        for left, right in self.plan["overlaps"]:
            pair = {initial[key]["receipt"]["number"]: key for key in (left, right)}
            unit = self.agents[left]["work_unit"]

            def observe():
                rows = self.lab.proposals(self.lab.status())
                result = {rows[num]["phase"]: key for num, key in pair.items() if num in rows}
                if set(result) == {"merged", "held_conflict"}:
                    return {"won": result["merged"], "held": result["held_conflict"], "base": self.lab.main()}
                return None

            found = self.step("pair-" + unit, lambda: self.wait(unit + " conflict", observe))
            winner, loser = (self.agents[found[key]] for key in ("won", "held"))
            row = initial[found["held"]]
            self.step("conflict-" + unit, lambda: self.lab.conflict(loser, row, found["base"]))
            self.step("seed-" + unit, lambda: self.lab.stage_seed(loser["host"], found["base"]))
            replacement = self.reassign(loser, loser, found["base"],
                                        winner["contract_cases"] + loser["contract_cases"], row)
            work[unit] = (found, replacement)
        return work

    def integrate(self, initial):
        replacements, queued = self.native_handoffs(initial)
        self.lab.set_build_capacity(self.plan["build_capacity"])
        self.parallel(self.runner.register, [op for key, op in self.agents.items() if key not in replacements])
        self.step("handoff-freeze-released", lambda: self.lab.release_freezes([initial[key] for key in replacements]))
        handoff_rows = self.stage("handoffs", list(replacements.values()))
        self.parallel(self.runner.register, replacements.values())
        handoffs = {key: {"old_pr": initial[key]["receipt"]["number"], "new_pr": row["receipt"]["number"],
                          "from": self.agents[key]["host"], "to": op["host"], "queued_entry": queued[key]}
                    for (key, op), row in zip(replacements.items(), handoff_rows, strict=True)}
        self.step("handoffs", lambda: handoffs)
        conflicts = self.conflicts(initial)
        self.step("conflict-freeze-released", lambda: self.lab.release_freezes([
            initial[found["held"]] for found, _ in conflicts.values()]))
        fixes = [op for _, op in conflicts.values()]
        resolutions = self.stage("overlaps", fixes)
        self.parallel(self.runner.register, fixes)
        overlaps = {unit: {"merged_pr": initial[found["won"]]["receipt"]["number"],
                           "held_pr": initial[found["held"]]["receipt"]["number"],
                           "resolution_pr": row["receipt"]["number"], "fresh_base": found["base"],
                           "resolution_host": op["host"]}
                    for (unit, (found, op)), row in zip(conflicts.items(), resolutions, strict=True)}
        self.step("overlaps", lambda: overlaps)
        retirements = len(replacements) + len(fixes)

        def settled():
            rows = self.lab.proposals(self.lab.status())
            expected = {"merged": len(initial), "ownership_retired": retirements}
            if Counter(row["phase"] for row in rows.values()) == expected and all(
                    row.get("integration", {}).get("state") == "verified"
                    for row in rows.values() if row["phase"] == "merged"):
                return {str(number): row for number, row in rows.items()}
            return None

        final = self.wait("verified integrations", settled, seconds=7200)
        all_ops = [*self.agents.values(), *replacements.values(), *fixes]
        by_claim = {(op["task"], op["generation"]): op for op in all_ops}
        for row in final.values():
            if row["phase"] == "merged":
                record = row["integration"]
                spec = record["receipt"]["proposal"]
                self.runner.integration(by_claim[spec["task"], spec["generation"]], record)
        return final

    def cleanup(self, final, proof):
        self.step("rules-restored", lambda: self.lab.restore_rules(final))
        self.step("principals-restored", self.lab.restore_principals)
        self.step("broker-reloaded", self.lab.reload_broker)
        denied = self.step("certificates-denied", lambda: self.parallel(self.lab.denied, self.agents.values()))
        hosts = sorted({op["host"] for op in self.agents.values()})
        for host in hosts:
            def remove():
                found = self.lab.worker_exists(host)
                if found:
                    self.lab.remove_worker(host)
                    self.interrupt("worker-removal", {"host": host})
                if self.lab.worker_exists(host):
                    raise PilotError(f"worker directory remains on {host}")
                return {"removed": True, "already_absent": not found}

            self.step("removed-" + host, remove)
        return self.step("cleanup", lambda: self.lab.verify_cleanup(final, proof, denied, hosts))

    def run(self):
        state = self.journal.read(self.steps)
        if "terminal" not in state:
            initial = self.step("initial", self.initial)
            final = self.step("terminal", lambda: self.integrate(initial))
        else:
            final = state["terminal"]
        self.step("capacity-restored", self.lab.restore_capacity)
        proof = self.step("audit", lambda: self.lab.audit(final, self.journal.read(self.steps)))
        return self.cleanup(final, proof)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path, help="private directory containing plan.json and adapter.py")
    parser.add_argument("--interrupt-after", action="append", default=[], choices=("reassignment", "worker-removal"),
                        help="terminate once after this remote change and before its receipt is recorded")
    args = parser.parse_args()
    state = args.state.resolve(strict=True)
    if state.stat().st_mode & 0o077:
        parser.error("the run directory must be private (chmod 700)")
    journal = Journal(state / "journal")
    with journal.controller():
        plan_path, adapter_path = state / "plan.json", state / "adapter.py"
        plan = json.loads(plan_path.read_bytes())
        journal.record("input", "plan", plan)
        journal.record("input", "adapter-sha256", sha256(adapter_path.read_bytes()).hexdigest())
        spec = importlib.util.spec_from_file_location("pi_pilot_lab", adapter_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def interrupt(key, detail):
            if key in args.interrupt_after and key not in journal.read("interruptions"):
                journal.record("interruptions", key, detail)
                print(f"Injected controller exit after {key}, before recording completion", flush=True)
                os._exit(74)

        result = Cohort(journal, plan, module.open_lab(state, plan), interrupt=interrupt).run()
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
