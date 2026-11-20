"""Run Pi against a local model and commit its tested edit in a pinned Lit workspace."""

import ast
from collections import Counter
import ctypes
import fcntl
import hashlib
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time


REQUIREMENTS = {
    "route": ("normalize_route", "Implement normalize_route(value: str) -> str. Split on slashes, "
              "trim and lowercase each segment, discard empty segments, and join them with one slash. "
              "Always include exactly one leading slash. An empty result is '/'."),
    "retry": ("retry_budget", "Implement retry_budget(attempt: int) -> int. Return an integer delay "
              "in milliseconds starting at 250 for attempt zero, double per attempt, cap at 4000, "
              "and treat negative attempts as zero. Bound the exponent before computing it."),
    "load": ("classify_load", "Implement classify_load(active: int, capacity: int) -> str. Return "
             "'invalid' when active is negative or capacity is nonpositive, 'idle' when active is zero, "
             "'saturated' when active meets or exceeds capacity, and 'busy' otherwise."),
}
CASES = {
    "route": [(('',), '/'), ((' /API// V1/ Foo  ',), '/api/v1/foo'),
              (('reports/Daily',), '/reports/daily'), (('/// /',), '/')],
    "retry": [((-4,), 250), ((0,), 250), ((1,), 500), ((2,), 1000),
              ((3,), 2000), ((4,), 4000), ((1000000,), 4000)],
    "load": [((0, 0), 'invalid'), ((-1, 3), 'invalid'), ((0, 4), 'idle'),
             ((1, 4), 'busy'), ((4, 4), 'saturated'), ((7, 4), 'saturated')],
}
PURE_BUILTINS = {"str": str, "int": int, "bool": bool, "len": len, "min": min, "max": max,
                 "filter": filter, "map": map}
METHODS = {"strip", "lstrip", "rstrip", "lower", "casefold", "split", "join", "replace", "append"}
SAFE_NODES = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Assign, ast.AnnAssign, ast.Return,
    ast.Expr, ast.Constant, ast.Name, ast.Load, ast.Store, ast.Call, ast.Attribute, ast.If, ast.For,
    ast.IfExp, ast.Compare, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.ListComp, ast.GeneratorExp,
    ast.comprehension, ast.List, ast.Tuple, ast.Slice, ast.Subscript, ast.Add, ast.Sub, ast.Mult,
    ast.Pow, ast.FloorDiv, ast.LShift, ast.USub, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def verify_edit(path, roles):
    if path.is_symlink() or path.stat().st_size > 6000:
        raise ValueError("unexpected Python fixture size or type")
    tree = ast.parse(path.read_bytes())
    names = {REQUIREMENTS[role][0] for role in roles}
    if {n.name for n in tree.body if isinstance(n, ast.FunctionDef)} != names:
        raise ValueError("fixture must contain exactly the requested functions")
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef)
                or isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            raise ValueError("unexpected top-level Python statement")
    for node in ast.walk(tree):
        if not isinstance(node, SAFE_NODES):
            raise ValueError(f"unexpected Python construct {type(node).__name__}")
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            raise ValueError("unexpected function decorator")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise ValueError("unexpected private Python name")
        if isinstance(node, ast.Attribute) and node.attr not in METHODS:
            raise ValueError("unexpected Python method")
        if isinstance(node, ast.For) and not (isinstance(node.iter, ast.Call)
                and isinstance(node.iter.func, ast.Attribute) and node.iter.func.attr == "split"):
            raise ValueError("fixture loops must iterate over split input")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Attribute)
                or isinstance(node.func, ast.Name) and node.func.id in PURE_BUILTINS):
            raise ValueError("unexpected Python call")
    sandbox = {"__builtins__": PURE_BUILTINS}
    exec(compile(tree, str(path), "exec"), sandbox)
    total = 0
    for role in roles:
        function = sandbox[REQUIREMENTS[role][0]]
        for args, expected in CASES[role]:
            got = function(*args)
            if type(got) is not type(expected) or got != expected:
                raise ValueError(f"{role} returned {got!r} for {args!r}, expected {expected!r}")
            total += 1
    return total


def confine_pi(runtime, packages, config, cwd, port):
    if platform.machine() not in ("x86_64", "aarch64") or sys.platform != "linux":
        raise RuntimeError("Pi sandbox requires Linux x86-64 or arm64")
    libc = ctypes.CDLL(None, use_errno=True)

    def check(result):
        if result < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        return result

    class Rules(ctypes.Structure):
        _fields_ = [("files", ctypes.c_uint64), ("network", ctypes.c_uint64), ("scope", ctypes.c_uint64)]

    class PathRule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("access", ctypes.c_uint64), ("parent", ctypes.c_int32)]

    class PortRule(ctypes.Structure):
        _fields_ = [("access", ctypes.c_uint64), ("port", ctypes.c_uint64)]

    abi = check(libc.syscall(444, None, 0, 1))
    if abi < 6:
        raise RuntimeError("Pi sandbox requires Landlock ABI 6")
    read_file, read_dir, execute = 1 << 2, 1 << 3, 1
    write_file, truncate = 1 << 1, 1 << 14
    read = read_file | read_dir
    write = write_file | truncate | sum(1 << bit for bit in (4, 5, 7, 8, 13))
    tcp_connect = 1 << 1
    attributes = Rules((1 << 16) - 1, 3, 3)
    ruleset = check(libc.syscall(444, ctypes.byref(attributes), ctypes.sizeof(attributes), 0))
    try:
        access = [(runtime, read | execute), (packages, read), (config, read | write),
                  (cwd, read | write), (Path("/usr/lib"), read | execute),
                  (Path("/lib64"), read | execute), (Path("/lib"), read | execute),
                  (Path("/etc/ld.so.cache"), read_file),
                  (Path("/usr/share/zoneinfo"), read), (Path("/dev/null"), read_file | write_file)]
        for path, rights in access:
            if not path.exists():
                continue
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(rights, fd)
                check(libc.syscall(445, ruleset, 1, ctypes.byref(rule), 0))
            finally:
                os.close(fd)
        network = PortRule(tcp_connect, port)
        check(libc.syscall(445, ruleset, 2, ctypes.byref(network), 0))
        check(libc.prctl(38, 1, 0, 0, 0))  # PR_SET_NO_NEW_PRIVS
        check(libc.syscall(446, ruleset, 0))
    finally:
        os.close(ruleset)


def run_pi(root, cwd, attempt, operation):
    runtime = root / "node"
    packages = root / "npm"
    config = attempt / "agent"
    config.mkdir(mode=0o700)
    (config / "models.json").write_text(json.dumps({"providers": {"pilot-local": {
        "baseUrl": f"http://{operation['inference_address']}:{operation['port']}/v1",
        "api": "openai-completions",
        "apiKey": "local-only", "compat": {"supportsDeveloperRole": False,
            "supportsReasoningEffort": False, "maxTokensField": "max_tokens"},
        "models": [{"id": operation["model"], "contextWindow": 32768, "maxTokens": 4096}],
    }}}))
    env = {"HOME": str(config), "XDG_CONFIG_HOME": str(config), "PI_CODING_AGENT_DIR": str(config),
           "PATH": "/usr/bin:/bin", "NO_PROXY": "*", "TERM": "dumb", "OPENSSL_CONF": "/dev/null"}
    node = [str(runtime / "bin/node"), "--permission", "--allow-fs-read=*", "--allow-fs-write=*"]
    def confine():
        confine_pi(runtime, packages, config, cwd, operation["port"])

    probe = """
const fs = require('node:fs');
const cp = require('node:child_process');
const net = require('node:net');
const points = [()=>fs.readFileSync('/etc/passwd'), ()=>fs.readdirSync(process.argv[1]),
                ()=>cp.execFileSync('/usr/bin/true')];
for (const operation of points) {
  try { operation(); throw Error('permission probe passed unexpectedly'); }
  catch (error) { if (!['EACCES', 'EPERM', 'ERR_ACCESS_DENIED'].includes(error.code)) throw error; }
}
net.connect({host:'127.0.0.1',port:443}).on('connect',()=>{ throw Error('unexpected TCP access'); })
    .on('error',error=>{if (!['EACCES', 'EPERM'].includes(error.code)) throw error;});
"""
    subprocess.run(node + ["-e", probe, str(root)], cwd=cwd, env=env, preexec_fn=confine,
                   check=True, timeout=20)
    if operation.get("workload") == "source":
        from pi_source_cases import prompt as source_prompt
        prompt = source_prompt(operation["contract_cases"])
    elif "contract_cases" in operation:
        from pi_lit_contracts import prompt as contract_prompt
        prompt = contract_prompt(operation["contract_cases"])
    else:
        prompt = ("You are working on a small Python routing helper. Use your file tools to write "
                  f"{operation['file']} in the current directory. "
                  "If the file already exists, read it first and preserve its existing functions. "
                  "Use plain Python functions, with no imports, no external I/O, and no new files. "
                  "Finish after saving the file. " + REQUIREMENTS[operation["role"]][1])
    if operation.get("feedback"):
        prompt += " An earlier attempt was rejected by the verifier. " + operation["feedback"]
    (attempt / "prompt.txt").write_text(prompt + "\n")
    pi = packages / "node_modules/.bin/pi"
    args = node + [str(pi), "--mode", "json", "--print", "--no-session", "--no-approve",
                   "--no-context-files", "--no-extensions", "--no-mcp", "--no-skills",
                   "--no-prompt-templates", "--no-themes", "--tools", "read,edit,write",
                   "--provider", "pilot-local", "--model", operation["model"], "--thinking", "off", prompt]
    release = operation.get("release_ms", 0)
    if release:
        time.sleep(max(0, (release - time.time_ns() // 1_000_000) / 1000))
    launch_ms = time.time_ns() // 1_000_000
    start = time.monotonic()
    transcript = attempt / "pi.jsonl"
    with transcript.open("wb") as stdout, (attempt / "pi.stderr").open("wb") as stderr:
        result = subprocess.run(args, cwd=cwd, env=env, preexec_fn=confine, stdin=subprocess.DEVNULL,
                                stdout=stdout, stderr=stderr, timeout=600, check=False)
    if result.returncode:
        raise RuntimeError(f"Pi exited {result.returncode}; see its private transcript")
    events = [json.loads(line) for line in transcript.read_bytes().splitlines() if line.startswith(b"{")]
    messages = [event.get("message", {}) for event in events if event.get("type") == "message_end"]
    assistant = [message for message in messages if message.get("role") == "assistant"
                 and message.get("provider") == "pilot-local" and message.get("model") == operation["model"]]
    calls = Counter(event.get("toolName") for event in events
                    if event.get("type") == "tool_execution_end" and not event.get("isError"))
    prompt_ms = min(message["timestamp"] for message in messages if message.get("role") == "user")
    if not assistant or not calls["write"] + calls["edit"]:
        raise RuntimeError("Pi transcript lacks a matching model response and a successful file edit")
    return {"model": operation["model"], "provider": "pilot-local", "inference_host": operation["inference_host"],
            "inference_address": operation["inference_address"],
            "launch_ms": launch_ms, "prompt_ms": prompt_ms,
            "filesystem_child_process_and_other_tcp_ports_denied": True, "tool_calls": dict(calls),
            "assistant_turns": len(assistant), "duration_seconds": round(time.monotonic() - start, 2),
            "transcript_sha256": hashlib.sha256(transcript.read_bytes()).hexdigest()}


def persist(path, document):
    pending = path.with_suffix(".pending")
    with pending.open("w") as stream:
        stream.write(json.dumps(document, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)
    return document


def execute(root, attempt, operation, action):
    from task_broker_workspace import proposal_from_workspace, run_lit

    binary = root / "lit"
    source = root / "source" / operation["base"]
    pin = json.loads((source / ".lit/broker-base.json").read_text())
    if pin["git_base"] != operation["base"]:
        raise ValueError("incorrect Git seed")
    workspace = attempt / "workspace"
    prepared = attempt / "prepared.json"
    if not prepared.exists():
        if workspace.exists():
            raise ValueError("workspace preparation was interrupted")
        run_lit(binary, source, "workspace", "create", str(workspace), "--base", pin["lit_base"])
        persist(prepared, pin)
    if json.loads(prepared.read_text()) != pin:
        raise ValueError("prepared workspace has a different Git seed")
    if action == "prepare":
        return pin
    result_path = attempt / "result.json"
    if result_path.exists():
        return json.loads(result_path.read_text())
    if (attempt / "agent").exists():
        raise ValueError("coding attempt has no validated result")
    run, unit = operation["run"], operation.get("work_unit")
    if operation.get("workload") == "source":
        from pi_source_cases import function_map, verify
        scope = workspace / "qualification" / run / unit
        scope.mkdir(parents=True, exist_ok=True)
        source = scope / "policy.py"
        before = function_map(source.read_bytes()) if source.exists() else {}
        model = run_pi(root, scope, attempt, operation)
        if list(scope.iterdir()) != [source]:
            raise ValueError("source workload must edit only policy.py")
        tested = verify(source, operation["contract_cases"])
        after = function_map(source.read_bytes())
        if any(ast.dump(before[name]) != ast.dump(after[name]) for name in before):
            raise ValueError("source workload changed a preexisting function")
        targets = [source]
    elif "contract_cases" in operation:
        from pi_lit_contracts import verify
        scope = workspace / "tests" / "agent-contracts" / run / unit
        scope.mkdir(parents=True, exist_ok=True)
        targets = [scope / name for name in ("README.md", "cases.json")]
        model = run_pi(root, scope, attempt, operation)
        tested = verify(binary, scope, operation["contract_cases"])
    else:
        scope = workspace / "qualification" / run
        if unit is not None:
            scope /= unit
        scope.mkdir(parents=True, exist_ok=True)
        expected = scope / operation["file"]
        preserved = {child: child.read_bytes() for child in scope.iterdir() if child != expected}
        model = run_pi(root, scope, attempt, operation)
        if set(scope.iterdir()) != {*preserved, expected} or any(
                path.is_symlink() or path.read_bytes() != contents for path, contents in preserved.items()):
            raise ValueError("unexpected file in the pilot scope")
        tested = verify_edit(expected, operation["expected_roles"])
        targets = [expected]
    relative = [str(path.relative_to(workspace)) for path in targets]
    run_lit(binary, workspace, "add", *relative)
    run_lit(binary, workspace, "commit", "-m", f"Pi pilot {operation['role']}")
    proposal = proposal_from_workspace(binary, workspace, operation["base"])
    if {file["path"] for file in proposal.pop("files")} != set(relative):
        raise ValueError("proposal must edit every assigned file")
    record = {"episode": operation["episode"], "task": operation["task"], "host": operation["host"],
              "generation": operation["generation"], "workspace": proposal, "local_cases": tested,
              "file_sha256": hashlib.sha256(targets[0].read_bytes()).hexdigest(), "execution": model}
    return persist(result_path, record)


def main():
    root = Path(sys.argv[1]).resolve(strict=True)
    sys.path.insert(0, str(root))
    action = sys.argv[2] if len(sys.argv) > 2 else "code"
    if action not in ("prepare", "code"):
        raise ValueError("invalid worker action")
    operation = json.load(sys.stdin)
    run, episode = operation["run"], operation["episode"]
    if not re.fullmatch(r"pinned-pi-[a-z0-9]+", run) or not re.fullmatch(r"[a-z0-9-]+", episode):
        raise ValueError("invalid pilot name or episode")
    unit = operation.get("work_unit")
    if unit is not None and not re.fullmatch(r"[a-z0-9-]+", unit):
        raise ValueError("invalid pilot work unit")
    endpoint = IPv4Address(operation["inference_address"])
    if (operation["port"] != 8002
            or operation["inference_host"] != "host-a" or not (endpoint.is_private or endpoint.is_loopback)):
        raise ValueError("invalid pilot file or endpoint")
    if "contract_cases" in operation:
        if not unit or not operation["contract_cases"]:
            raise ValueError("contract work requires a work unit and assigned cases")
    elif operation["file"] not in ("routing.py", "capacity.py"):
        raise ValueError("invalid pilot file")
    attempt = root / "attempts" / episode
    attempt.mkdir(parents=True, mode=0o700, exist_ok=True)
    with (attempt / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(json.dumps(execute(root, attempt, operation, action)))


if __name__ == "__main__":
    main()
