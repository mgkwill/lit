#!/usr/bin/env python3
"""Replicate a Pi pilot checkpoint and activate its configured standby."""

import argparse
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import ssl
import sys
import tarfile
import tempfile

from pi_pilot_forge import require
from pi_pilot_lab import Lab, open_lab
from pi_pilot_runner import Journal, PilotError


COMPRESSED_LIMIT = 64 * 1024 * 1024
EXPANDED_LIMIT = 256 * 1024 * 1024
INPUTS = {"plan.json", "lab.json", "principals.json", "policy-before.json"}
EVIDENCE = re.compile(
    r"(?:runtime-readiness\.json|git-pin|implementation-commit|"
    r"(?:publication|stale|conflict)-[a-z0-9-]+\.json|"
    r"(?:window-[0-9]+-)?(?:initial|handoffs|overlaps)-verified-trees\.json|"
    r"w[0-9]+-g[0-9]+(?:-retry[0-9]+)?-(?:pi\.jsonl|pi\.stderr|prompt\.txt|error\.txt|"
    r"assignment-submissions\.json))"
)


def private_directory(path, *, create=False):
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(path.is_dir() and not path.is_symlink() and not path.stat().st_mode & 0o077,
            f"expected a private directory at {path}")
    return path


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_file(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            temp.replace(path)
            fsync_directory(path.parent)
        finally:
            temp.unlink(missing_ok=True)


def allowed(name):
    return name in INPUTS or EVIDENCE.fullmatch(name) or re.fullmatch(
        r"(?:journal|private)/[a-z0-9][a-z0-9-]{0,127}\.json", name)


def archive(state):
    journals = [state / name for name in ("journal", "private") if (state / name).exists()]
    candidates = list(state.iterdir())
    for path in journals:
        candidates.extend(private_directory(path).iterdir())
    files = sorted(path for path in candidates if allowed(path.relative_to(state).as_posix()))
    names = {path.relative_to(state).as_posix() for path in files}
    require(INPUTS | {"journal/input.json"} <= names, "checkpoint is missing its plan, policy or journal")
    total = 0
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.USTAR_FORMAT) as bundle:
        for path in files:
            require(path.is_file() and not path.is_symlink(), "checkpoint file must be regular")
            total += path.stat().st_size
            require(total <= EXPANDED_LIMIT, "checkpoint exceeds expanded size limit")
            data = path.read_bytes()
            entry = tarfile.TarInfo(path.relative_to(state).as_posix())
            entry.size, entry.mode = len(data), 0o600
            bundle.addfile(entry, BytesIO(data))
    require(buf.tell() <= COMPRESSED_LIMIT, "checkpoint exceeds compressed size limit")
    return buf.getvalue()


def extract(payload, destination=None):
    seen = set()
    total = 0
    with tarfile.open(fileobj=BytesIO(payload), mode="r|gz") as bundle:
        for item in bundle:
            require(item.isreg() and not item.issparse() and allowed(item.name) and item.name not in seen,
                    "checkpoint contains an unexpected, linked or duplicate member")
            total += item.size
            require(total <= EXPANDED_LIMIT, "checkpoint exceeds expanded size limit")
            block = bundle.extractfile(item).read()
            require(len(block) == item.size, "checkpoint member is incomplete")
            if destination is not None:
                path = destination / item.name
                path.parent.mkdir(mode=0o700, exist_ok=True)
                write_file(path, block)
            seen.add(item.name)
    require(INPUTS | {"journal/input.json"} <= seen, "checkpoint is incomplete")


def checksum(data):
    return sha256(data).hexdigest()


def receive(store, expected, payload):
    require(re.fullmatch(r"[0-9a-f]{64}", expected) and len(payload) <= COMPRESSED_LIMIT
            and checksum(payload) == expected, "checkpoint checksum or length differs")
    extract(payload)
    private_directory(store, create=True)
    with Journal(store / "control").controller():
        require(not (store / "takeover.json").exists(), "standby already selected a checkpoint")
        path = store / f"{expected}.tgz"
        write_file(path, payload)
        proof = {"sha256": expected, "bytes": len(payload)}
        write_file(store / "latest.json", json.dumps(proof, sort_keys=True).encode())
    return proof


def handoff_config(config):
    handoff = config.get("handoff")
    require(isinstance(handoff, dict) and set(handoff) == {"from", "to", "identities", "host", "checkout", "store"},
            "lab profile requires a handoff configuration")
    old, new = handoff["from"], handoff["to"]
    require(old != new and set(handoff["identities"]) == {old, new}
            and {old, new} <= set(config.get("controllers", {})), "handoff requires distinct controller profiles")
    fingerprints = list(handoff["identities"].values())
    require(len(set(fingerprints)) == 2 and all(
        re.fullmatch(r"[0-9a-f]{64}", key)
        and config.get("controller_principals", {}).get(key, {}).get("role") == "scheduler"
        for key in fingerprints), "handoff certificates must be distinct scoped scheduler principals")
    require(all(Path(handoff[name]).is_absolute() for name in ("checkout", "store")), "standby paths must be absolute")
    return handoff


def selected_certificate(lab, expected):
    certificate = Path(lab.config["scheduler_certificate"]).read_text()
    require(checksum(ssl.PEM_cert_to_DER_cert(certificate)) == expected,
            "selected controller certificate differs from the configured handoff identity")


def replicate(lab):
    handoff = handoff_config(lab.config)
    require(lab.controller == handoff["from"], "only the outgoing controller can replicate to this standby")
    selected_certificate(lab, handoff["identities"][lab.controller])
    lab.require_live_controller()
    payload = archive(lab.state)
    expected = checksum(payload)
    script = str(Path(handoff["checkout"]) / "tools/pi_pilot_control.py")
    response = lab.ssh(handoff["host"], ["python3", "-B", script, "receive",
                       "--store", handoff["store"], "--sha256", expected], payload)
    receipt = json.loads(response)
    require(receipt == {"sha256": expected, "bytes": len(payload)}, "standby did not verify the checkpoint")
    return receipt


def restore(store, state, expected=None):
    def read(path):
        require(path.is_file() and not path.is_symlink(), "checkpoint store member must be regular")
        return path.read_bytes()

    pin = store / "takeover.json"
    if pin.exists():
        selected = json.loads(read(pin))
        require(str(state) == selected["state"] and expected in (None, selected["sha256"]),
                "standby already selected a different checkpoint or destination")
        expected = selected["sha256"]
    else:
        expected = expected or json.loads(read(store / "latest.json"))["sha256"]
    require(re.fullmatch(r"[0-9a-f]{64}", expected), "invalid checkpoint identifier")
    payload = read(store / f"{expected}.tgz")
    require(len(payload) <= COMPRESSED_LIMIT and checksum(payload) == expected, "stored checkpoint checksum differs")
    selected = {"state": str(state), "sha256": expected}
    if state.exists():
        private_directory(state)
        require((state / "checkpoint-restored.json").is_file()
                and json.loads(read(state / "checkpoint-restored.json")) == selected,
                "will not replace or adopt an unrelated state directory")
    if not pin.exists():
        write_file(pin, json.dumps(selected).encode())
    if not state.exists():
        with tempfile.TemporaryDirectory(dir=state.parent) as scratch:
            staging = Path(scratch) / "state"
            staging.mkdir(mode=0o700)
            extract(payload, staging)
            write_file(staging / "checkpoint-restored.json", json.dumps(selected).encode())
            staging.rename(state)
            fsync_directory(state.parent)
    return expected


def activate(lab, handoff):
    old = handoff["identities"][handoff["from"]]
    new = handoff["identities"][handoff["to"]]
    selected_certificate(lab, new)
    principals = lab.config["controller_principals"]
    for host in lab.config["broker_hosts"]:
        path = lab.config["broker_config"]
        snapshot = json.loads(lab.ssh(host, ["sudo", "-n", "cat", "--", path]))
        listed = snapshot["principals"]
        require(old in listed or new in listed, f"neither handoff principal is enrolled on {host}")
        require(all(listed[key] == principals[key] for key in (old, new) if key in listed),
                "a controller principal has a different role or owner")
        lab.update_principals(host, add={new: principals[new]}, remove={old: principals[old]})
        observed = json.loads(lab.ssh(host, ["sudo", "-n", "cat", "--", path]))
        expected = snapshot["principals"]
        expected.pop(old, None)
        expected[new] = principals[new]
        require(observed == snapshot, f"controller transition changed other broker settings on {host}")
    lab.ssh(lab.plan["active_host"], ["sudo", "-n", "systemctl", "restart", lab.config["broker_unit"]])
    lab.healthy()
    lab.admin("status", seconds=0)
    return {"source_revoked": old, "standby_active": new}


def takeover(store, state, expected=None):
    private_directory(store)
    with Journal(store / "control").controller():
        snapshot = restore(store, state, expected)
        journal = Journal(state / "journal")
        with journal.controller():
            plan = json.loads((state / "plan.json").read_bytes())
            config = json.loads((state / "lab.json").read_bytes())
            handoff = handoff_config(config)
            require(Path(handoff["store"]).resolve() == store, "checkpoint belongs to a different standby store")
            require(journal.read("input").get("plan") == plan and journal.read("controllers").get(handoff["from"]),
                    "checkpoint is missing the outgoing controller and pinned plan")
            lab = open_lab(state, plan, journal, handoff["to"])
            prior = journal.read("takeover")
            if prior:
                lab.admin("status", seconds=0)
                return prior["receipt"]
            result = activate(lab, handoff) | {"checkpoint_sha256": snapshot, "controller": handoff["to"]}
            return journal.record("takeover", "receipt", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    sender = commands.add_parser("checkpoint", help="replicate a stopped controller's journal")
    sender.add_argument("--state", required=True, type=Path)
    sender.add_argument("--controller", required=True)
    receiver = commands.add_parser("receive", help="store a checksum-verified checkpoint from stdin")
    receiver.add_argument("--store", required=True, type=Path)
    receiver.add_argument("--sha256", required=True)
    standby = commands.add_parser("takeover", help="restore a checkpoint and switch scoped broker principals")
    standby.add_argument("--store", required=True, type=Path)
    standby.add_argument("--state", required=True, type=Path)
    standby.add_argument("--sha256")
    args = parser.parse_args()
    try:
        if args.action == "receive":
            result = receive(args.store.absolute(), args.sha256, sys.stdin.buffer.read(COMPRESSED_LIMIT + 1))
        elif args.action == "takeover":
            result = takeover(args.store.resolve(strict=True), args.state.absolute(), args.sha256)
        else:
            state = private_directory(args.state.resolve(strict=True))
            journal = Journal(state / "journal")
            with journal.controller():
                plan = json.loads((state / "plan.json").read_bytes())
                result = replicate(open_lab(state, plan, journal, args.controller))
    except (PilotError, OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
