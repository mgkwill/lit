"""macOS and Linux confinement checks. Run with the absolute path to a Lit binary."""

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

LIT = Path(sys.argv[1]).resolve()
REFERENCE = "--reference" in sys.argv[2:]
MACOS = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")
PROBE = r'''
/bin/cat inside.txt || exit 21
printf 'inside write\n' > inside-output || exit 22
if /bin/cat "$1"; then echo OUTSIDE_READ_ALLOWED; else echo OUTSIDE_READ_BLOCKED; fi
if printf 'outside modified\n' > "$1"; then echo OUTSIDE_WRITE_ALLOWED; else echo OUTSIDE_WRITE_BLOCKED; fi
if /bin/cat "$2"; then echo SYMLINK_READ_ALLOWED; else echo SYMLINK_READ_BLOCKED; fi
if /bin/ln "$1" escape-link; then echo HARDLINK_ALLOWED; else echo HARDLINK_BLOCKED; fi
if /usr/bin/nc -z -w 1 127.0.0.1 "$3"; then echo LOOPBACK_ALLOWED; else echo LOOPBACK_BLOCKED; fi
if test -z "$LIT_TEST_SECRET"; then echo ENV_SCRUBBED; else echo ENV_LEAKED; fi
'''


def check(binary, fixture):
    repo = fixture / "repo"
    repo.mkdir()
    home = fixture / "home"
    home.mkdir()
    outside = fixture / "canary.txt"
    outside.write_text("outside canary\n")
    (repo / "inside.txt").write_text("inside content\n")
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "TMPDIR": str(fixture),
        "LIT_OUTPUT": "json",
        "LIT_TEST_SECRET": "synthetic-secret",
    }
    if os.name == "nt":
        for key in ("SYSTEMROOT", "USERPROFILE", "WINDIR"):
            if key in os.environ:
                environment[key] = os.environ[key]

    def run(*args, prefix=(), pass_fds=()):
        return subprocess.run(
            [*prefix, binary, *args], cwd=repo, env=environment,
            capture_output=True, text=True, timeout=10, pass_fds=pass_fds,
        )

    def success(*args):
        result = run(*args)
        assert result.returncode == 0, (args, result.stdout, result.stderr)
        return json.loads(result.stdout)

    success("init", ".")
    workspace = Path(success("sandbox", "init", "probe")["path"])
    if not (MACOS or LINUX):
        mark = workspace / "launched"
        result = run("sandbox", "run", "probe", "--strict", "--", sys.executable,
                     "-c", f"from pathlib import Path; Path({str(mark)!r}).touch()")
        assert result.returncode != 0 and not mark.exists()
        assert json.loads(result.stderr)["error"]["code"] == "SANDBOX_UNAVAILABLE"
        print("Strict mode refused on a platform without a backend")
        return

    link = workspace / "outside-symlink"
    link.symlink_to(outside)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = str(listener.getsockname()[1])
        probe = ("/bin/bash", "-c", PROBE, "probe", str(outside), link.name, port)
        strict = run("sandbox", "run", "probe", "--strict", "--", *probe)
        if REFERENCE:
            assert strict.returncode != 0 and not (workspace / "inside-output").exists()
            assert outside.read_text() == "outside canary\n"
            print("v1.8.0 strict refused before running the payload")
        else:
            assert strict.returncode == 0, (strict.stdout, strict.stderr)
            response = json.loads(strict.stdout)
            assert response["exit_code"] == 0
            assert set(response["controls_enforced"]) == {
                "environment-scrub", "filesystem-isolation", "network-deny",
            }
            assert "inside content" in response["output"]
            assert (workspace / "inside-output").read_text() == "inside write\n"
            assert outside.read_text() == "outside canary\n"
            for evidence in (
                "OUTSIDE_READ_BLOCKED", "OUTSIDE_WRITE_BLOCKED", "SYMLINK_READ_BLOCKED",
                "HARDLINK_BLOCKED", "LOOPBACK_BLOCKED", "ENV_SCRUBBED",
            ):
                assert evidence in response["output"], (evidence, response)
            assert "__lit_sandbox_ready__" not in response["output"]
            listener.settimeout(0.2)
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                pass
            else:
                conn.close()
                raise AssertionError("A strict payload reached the loopback listener")
            print("Strict isolation: inside I/O allowed; outside read/write, symlink, hardlink and loopback blocked")

        control = success("sandbox", "run", "probe", "--", *probe)
        for evidence in ("OUTSIDE_READ_ALLOWED", "OUTSIDE_WRITE_ALLOWED",
                         "SYMLINK_READ_ALLOWED", "LOOPBACK_ALLOWED", "ENV_SCRUBBED"):
            assert evidence in control["output"], (evidence, control)
        conn, _ = listener.accept()
        conn.close()
        assert outside.read_text() == "outside modified\n"
        unenforced = control.get("controls_unenforced", [])
        assert any(control.startswith("network-isolation") for control in unenforced)
        if not REFERENCE:
            assert "filesystem-isolation" in unenforced
        print("Default control: outside I/O and loopback allowed; environment scrubbed")

    if REFERENCE:
        return

    launch = ("sandbox", "run", "probe", "--strict", "--",
              "/bin/bash", "-c", "echo reached > launch-marker")
    # Default execution created a hardlink, which strict mode must reject.
    assert os.path.samefile(workspace / "escape-link", outside)
    blocked = run(*launch)
    assert blocked.returncode != 0
    assert json.loads(blocked.stderr)["error"]["code"] == "SANDBOX_UNAVAILABLE"
    assert not (workspace / "launch-marker").exists()
    (workspace / "escape-link").unlink()
    print("Preexisting hardlink refused before launch")

    alias = workspace.parent / "alias"
    alias.symlink_to(workspace, target_is_directory=True)
    result = run("sandbox", "run", "alias", *launch[3:])
    assert result.returncode != 0
    assert json.loads(result.stderr)["error"]["code"] == "SANDBOX_UNAVAILABLE"
    assert not (workspace / "launch-marker").exists()
    print("Symlinked sandbox root refused before launch")

    # Block only the system launcher in an outer profile. Lit and the
    # requested program would otherwise be allowed to start.
    if MACOS:
        prefix = (
            "/usr/bin/sandbox-exec", "-p",
            '(version 1)(allow default)(deny process-exec (literal "/usr/bin/sandbox-exec"))',
        )
    else:
        prefix = (
            "/usr/bin/bwrap", "--unshare-user", "--disable-userns",
            "--ro-bind", "/", "/", "--bind", str(fixture), str(fixture), "--",
        )
    result = run(*launch, prefix=prefix)
    assert result.returncode != 0, (result.stdout, result.stderr)
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "SANDBOX_UNAVAILABLE"
    assert not (workspace / "launch-marker").exists()
    print("Unavailable backend returned SANDBOX_UNAVAILABLE and left no launch marker")

    if LINUX:
        # Block new user namespaces without remapping ownership of bwrap,
        # so this case reaches the trusted launcher and fails at setup.
        deny_namespaces = """
import ctypes as c, errno, os, sys
class Argument(c.Structure):
    _fields_ = [('index', c.c_uint), ('op', c.c_uint),
                ('mask', c.c_uint64), ('value', c.c_uint64)]

seccomp = c.CDLL('libseccomp.so.2')
seccomp.seccomp_init.argtypes = [c.c_uint32]
seccomp.seccomp_init.restype = c.c_void_p
seccomp.seccomp_rule_add_array.argtypes = [
    c.c_void_p, c.c_uint32, c.c_int, c.c_uint, c.POINTER(Argument),
]
seccomp.seccomp_syscall_resolve_name.argtypes = [c.c_char_p]
seccomp.seccomp_load.argtypes = [c.c_void_p]
context = seccomp.seccomp_init(0x7fff0000)
assert context
for syscall, failure, argument in [
    (b'clone', errno.EPERM, Argument(0, 7, 0x10000000, 0x10000000)),
    (b'clone3', errno.ENOSYS, None),
    (b'unshare', errno.EPERM, None),
]:
    nr = seccomp.seccomp_syscall_resolve_name(syscall)
    assert nr >= 0
    assert seccomp.seccomp_rule_add_array(
        context, 0x00050000 | failure, nr, int(argument is not None),
        c.byref(argument) if argument is not None else None,
    ) == 0
assert seccomp.seccomp_load(context) == 0
os.execv(sys.argv[1], sys.argv[1:])
"""
        result = run(*launch, prefix=(sys.executable, "-c", deny_namespaces))
        assert result.returncode != 0
        error = json.loads(result.stderr)["error"]
        assert error["code"] == "SANDBOX_UNAVAILABLE", error
        assert "Linux Bubblewrap did not start" in error["message"], error
        assert "namespace" in error["message"].lower(), error
        assert not (workspace / "launch-marker").exists()
        print("Trusted launcher with namespaces disabled refused before launch")

    with outside.open("rb") as outside_file, socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        with socket.create_connection(server.getsockname()) as socket_fd:
            peer, _ = server.accept()
            with peer:
                file_descriptor = outside_file.fileno()
                socket_descriptor = socket_fd.fileno()
                check_fds = (
                    f'/bin/cat <&{file_descriptor}; '
                    f'printf "escape" >&{socket_descriptor}; echo FD_PROBE_FINISHED'
                )
                result = run("sandbox", "run", "probe", "--strict", "--",
                             "/bin/bash", "-c", check_fds,
                             pass_fds=(file_descriptor, socket_descriptor))
                assert result.returncode == 0, (result.stdout, result.stderr)
                output = json.loads(result.stdout)["output"]
                assert "FD_PROBE_FINISHED" in output and "outside modified" not in output
                assert output.count("Bad file descriptor") == 2, output
                peer.settimeout(0.2)
                try:
                    data = peer.recv(32)
                except TimeoutError:
                    data = b""
                assert not data, f"Strict payload wrote to inherited socket: {data!r}"
    print("Strict launcher closed inherited external file and connected socket descriptors")

    if LINUX:
        mounted_source = fixture / "mount-source"
        mounted_target = workspace / "host-mount"
        mounted_source.mkdir()
        mounted_target.mkdir()
        (mounted_source / "data").write_text("outside mount data")
        mount_prefix = (
            "/usr/bin/bwrap", "--unshare-user", "--ro-bind", "/", "/",
            "--bind", str(fixture), str(fixture),
            "--bind", str(mounted_source), str(mounted_target), "--",
        )
        result = run(*launch, prefix=mount_prefix)
        assert result.returncode != 0
        error = json.loads(result.stderr)["error"]
        assert error["code"] == "SANDBOX_UNAVAILABLE" and "host mount" in error["message"], error
        assert not (workspace / "launch-marker").exists()
        print("Nested host bind mount refused before launch")

        sockets = success(
            "sandbox", "run", "probe", "--strict", "--", "/usr/bin/python3", "-c",
            "import errno, socket\n"
            "for family, kind in [(socket.AF_INET, socket.SOCK_DGRAM), "
            "(socket.AF_INET6, socket.SOCK_STREAM), (socket.AF_UNIX, socket.SOCK_STREAM)]:\n"
            "    try: socket.socket(family, kind)\n"
            "    except OSError as error: assert error.errno == errno.EPERM, error\n"
            "    else: raise AssertionError(f'socket opened: {family}')\n"
            "print('UDP, IPv6 and Unix socket creation denied')",
        )
        assert sockets["exit_code"] == 0, sockets
        print(sockets["output"].strip())

    payload = success("sandbox", "run", "probe", "--strict", "--",
                      "/bin/bash", "-c", "printf 'started\n'; exit 17")
    assert payload["output"] == "started\n" and payload["exit_code"] == 17
    print("Confined child retained its own output and exit status")


with tempfile.TemporaryDirectory() as directory:
    check(LIT, Path(directory).resolve())
