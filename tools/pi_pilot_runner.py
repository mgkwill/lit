"""Durable orchestration for a pinned Pi pilot using a trusted broker adapter."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
import time


class PilotError(RuntimeError):
    pass


class Journal:
    """Store receipts atomically and exclude concurrent controllers."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.lock = RLock()

    @contextmanager
    def controller(self):
        with (self.directory / ".controller.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise PilotError("the journal has another controller") from error
            yield

    def _path(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", key):
            raise PilotError("invalid journal key")
        return self.directory / (key + ".json")

    def read(self, key):
        try:
            return json.loads(self._path(key).read_bytes())
        except FileNotFoundError:
            return {}

    def record(self, key, field, value):
        with self.lock:
            data = self.read(key)
            if field in data:
                if data[field] != value:
                    raise PilotError(f"journal entry changed for {key}/{field}")
                return value
            data[field] = value
            name = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as stream:
                    name = stream.name
                    stream.write((json.dumps(data, sort_keys=True) + "\n").encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self._path(key))
                directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if name is not None:
                    Path(name).unlink(missing_ok=True)
            return value


class Runner:
    """Use a prepared-worker adapter and the broker's scoped status endpoints.

    The adapter is responsible for transport, TLS credentials, scheduling leases,
    branch rules, and testing. It returns broker CLI results as (exit_code, body).
    Journal only the public assignment binding and matched receipts.
    """

    def __init__(self, directory, adapter, *, retry_delay=2, deadline=300):
        self.journal = Journal(directory)
        self.adapter = adapter
        self.retry_delay = retry_delay
        self.deadline = deadline

    def bind(self, operation, claim):
        key = operation["episode"]
        binding = {name: claim[name] for name in ("task", "generation", "claim", "owner", "base")}
        if (any(binding[name] != operation[name] for name in ("task", "generation", "owner", "base"))
                or not re.fullmatch(r"[0-9a-f]{32}", binding["claim"])
                or not re.fullmatch(r"[0-9a-f]{40}", binding["base"])):
            raise PilotError(f"assignment binding does not match {key}")
        self.journal.record(key, "operation", operation)
        return self.journal.record(key, "binding", binding)

    def code(self, group, operations, *, lead_seconds=3):
        """Prepare all pins before persisting the shared release timestamp."""
        keys = [op["episode"] for op in operations]
        if not keys or len(keys) != len(set(keys)):
            raise PilotError("a worker group needs unique attempts")
        group_key = "group-" + group
        self.journal.record(group_key, "attempts", keys)

        def prepare(op):
            key = op["episode"]
            state = self.journal.read(key)
            if state.get("operation") != op or "binding" not in state:
                raise PilotError(f"unbound attempt {key}")
            if "prepared" not in state:
                result = self.adapter.prepare(op)
                if result.get("git_base") != op["base"]:
                    raise PilotError(f"prepared workspace used a different pin for {key}")
                self.journal.record(key, "prepared", result)

        def generate(op):
            key = op["episode"]
            state = self.journal.read(key)
            if "coding" in state:
                return state["coding"]
            result = self.adapter.generate(op, release)
            if (result.get("episode") != key or result.get("task") != op["task"]
                    or result.get("workspace", {}).get("git_base") != op["base"]):
                raise PilotError(f"coding receipt does not match {key}")
            return self.journal.record(key, "coding", result)

        with ThreadPoolExecutor(max_workers=min(len(operations), 24)) as pool:
            list(pool.map(prepare, operations))
        group_state = self.journal.read(group_key)
        release = group_state.get("release_ms")
        if release is None:
            release = self.journal.record(group_key, "release_ms", int((time.time() + lead_seconds) * 1000))
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(generate, operations))

    def _settle(self, op, kind, submit, validate):
        key = op["episode"]
        state = self.journal.read(key)
        if kind in state:
            return state[kind]
        claim = state["binding"]["claim"]
        expected = f"/{kind}?claim={claim}"
        self.journal.record(key, kind + "-intent", op["request"])
        deadline = time.monotonic() + self.deadline
        submitted = False
        while True:
            status, body = self.adapter.broker(op, kind, claim=claim)
            if body.get("error") == kind + "_not_found":
                if submitted or kind + "-accepted" in self.journal.read(key):
                    raise PilotError(f"broker lost the accepted {kind} for {key}")
                status, body = submit()
                submitted = True
            if status == 0 and body.get("status_path") == expected:
                self.journal.record(key, kind + "-accepted", {"claim": claim, "status_path": expected})
            elif status == 0:
                validate(body)
                return self.journal.record(key, kind, body)
            elif not body.get("retryable"):
                raise PilotError(f"broker {kind} failed for {key}: {body.get('error', 'invalid response')}")
            if time.monotonic() >= deadline:
                raise PilotError(f"broker {kind} is pending for {key}")
            time.sleep(self.retry_delay)

    def publish(self, op):
        state = self.journal.read(op["episode"])
        if state.get("operation") != op or "coding" not in state:
            raise PilotError("publication requires a matching coding receipt")
        binding = state["binding"]

        def verify(body):
            if (any(body.get(field) != binding[field] for field in ("task", "generation", "claim", "base"))
                    or body.get("agent") != binding["owner"]
                    or not isinstance(body.get("number"), int) or body["number"] <= 0
                    or not re.fullmatch(r"[0-9a-f]{40}", body.get("head", ""))):
                raise PilotError("publication receipt does not match the claim")

        return self._settle(op, "publication", lambda: self.adapter.broker(op, "propose"), verify)

    def register(self, op):
        state = self.journal.read(op["episode"])
        if state.get("operation") != op or "publication" not in state:
            raise PilotError("registration requires a matching publication receipt")
        receipt = state["publication"]

        def verify(body):
            if body.get("registered") != receipt["number"]:
                raise PilotError("registration receipt names a different PR")

        return self._settle(op, "registration", lambda: self.adapter.broker(op, "register", receipt=receipt), verify)

    def integration(self, op, record):
        state = self.journal.read(op["episode"])
        receipt = record.get("receipt", {})
        if (state.get("operation") != op or "registration" not in state or record.get("state") != "verified"
                or receipt.get("proposal") != state["publication"]
                or not re.fullmatch(r"[0-9a-f]{40}", receipt.get("merge_commit", ""))
                or receipt.get("actions", {}).get("app_id") != 15368
                or receipt.get("actions", {}).get("event") != "merge_group"):
            raise PilotError("integration receipt is unverified or names a different proposal")
        return self.journal.record(op["episode"], "integration", record)
