"""Exercise Git transports with disposable TLS and OpenSSH servers."""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

from remote_regression import Fixture, exchange, seed_git, smart_http


def declined(fixture, cwd, *args, message):
    result = fixture.assert_failure(cwd, *args)
    payload = result.stderr[result.stderr.index("{"):]
    assert message in json.loads(payload)["error"]["message"], (args, result.stderr)
    return result


def airgap(fixture, url, local):
    for cwd, command in (
        (fixture.root, ("clone", url, "airgap-clone")),
        (local, ("remote", "add", "airgap", url)),
        (local, ("fetch", "origin")),
        (local, ("pull", "origin", "release/trunk")),
        (local, ("push", "origin", "release/trunk")),
    ):
        declined(fixture, cwd, "--airgapped", *command, message="AIRGAP")
    assert not (fixture.root / "airgap-clone").exists()


def https(fixture):
    origin, source = seed_git(fixture)
    cert = fixture.root / "tls-cert"
    key = fixture.root / "tls-key"
    fixture.run(
        fixture.root, "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-days", "1", "-subj", "/CN=Lit Git fixture", "-addext",
        "subjectAltName=IP:127.0.0.1", "-keyout", key, "-out", cert,
    )
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    helper = fixture.root / "askpass"
    calls = fixture.root / "askpass-calls"
    secret = "fixture-https-credential"
    helper.write_text(
        '#!/bin/sh\n'
        'case "$1" in *"/origin.git"*) ;; *) exit 1 ;; esac\n'
        'case "$1" in\n'
        '  Username*) printf "username\\n" >> "$LIT_FIXTURE_CALLS"; printf "fixture\\n" ;;\n'
        '  Password*) printf "password\\n" >> "$LIT_FIXTURE_CALLS"; printf "%s\\n" "$LIT_FIXTURE_PASSWORD" ;;\n'
        '  *) exit 1 ;;\n'
        'esac\n'
    )
    helper.chmod(0o700)
    fixture.env.update(
        LIT_GIT_ASKPASS=str(helper), LIT_FIXTURE_CALLS=str(calls),
        LIT_FIXTURE_PASSWORD=secret, GIT_SSL_NO_VERIFY="1",
    )
    authorization = "Basic " + base64.b64encode(f"fixture:{secret}".encode()).decode()
    with smart_http(fixture, tls=tls, authorization=authorization) as (remote, requests):
        url = "git+" + remote
        declined(fixture, fixture.root, "clone", url, "untrusted-cert", message="Git clone failed")
        assert not calls.exists() and not requests
        fixture.env["GIT_SSL_CAINFO"] = str(cert)
        declined(fixture, fixture.root, "clone", url.replace("127.0.0.1", "localhost"),
                 "wrong-hostname", message="Git clone failed")
        assert not calls.exists() and not requests

        del fixture.env["LIT_GIT_ASKPASS"]
        fixture.env["GIT_ASKPASS"] = str(helper)
        declined(fixture, fixture.root, "clone", url, "ambient-helper", message="Git clone failed")
        assert not calls.exists() and [method for method, _, _ in requests] == ["401"]
        del fixture.env["GIT_ASKPASS"]
        fixture.env["LIT_GIT_ASKPASS"] = str(helper)
        fixture.env["LIT_FIXTURE_PASSWORD"] = "fixture-wrong-password"
        declined(fixture, fixture.root, "clone", url, "wrong-credential", message="Git clone failed")
        assert all(method == "401" for method, _, _ in requests)
        assert not (fixture.root / "wrong-credential").exists()
        fixture.env["LIT_FIXTURE_PASSWORD"] = secret

        # Plain Git authenticates against the same service as a positive control.
        fixture.env["GIT_ASKPASS"] = str(helper)
        fixture.env.pop("GIT_SSL_NO_VERIFY")
        refs = fixture.git(fixture.root, "-c", "credential.useHttpPath=true", "ls-remote", remote, "HEAD")
        assert refs.split()[0] == fixture.git(origin, "rev-parse", "HEAD")
        del fixture.env["GIT_ASKPASS"]

        exchange(fixture, origin, source, url)
        client = fixture.root / "client"
        old_requests = list(requests)
        old_calls = calls.read_bytes()
        airgap(fixture, url, client)
        assert requests == old_requests and calls.read_bytes() == old_calls

        denied_url = url.replace("https://", f"https://fixture:{secret}@")
        for cwd, command in (
            (fixture.root, ("clone", denied_url, "inline-secret")),
            (client, ("remote", "add", "inline-secret", denied_url)),
        ):
            result = declined(fixture, cwd, *command, message="Invalid Git remote URL")
            assert secret not in result.stdout + result.stderr
        declined(fixture, client, "remote", "add", "cleartext", url.replace("https", "http"),
                 message="Explicit Git remotes use git+https:// or git+ssh://")
        config = (client / ".lit/remotes").read_text()
        assert secret not in config and url in config
        assert requests == old_requests and calls.read_bytes() == old_calls
        counts = {endpoint: sum(method == "POST" and path.endswith(endpoint) for method, path, _ in requests)
                  for endpoint in ("git-upload-pack", "git-receive-pack")}
        assert all(counts.values()), counts
        return {"rejected_tls_peer_and_name": True, "wrong_credential_refused": True, "askpass_received_repository_path": True,
                "airgap_zero_network_and_helper_calls": True, "git_posts": counts}


@contextmanager
def git_ssh_server(fixture, origin):
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    ssh_git = shutil.which("git")
    username = pwd.getpwuid(os.getuid()).pw_name
    ssh_dir = fixture.make("ssh-material")
    host_key, identity, wrong = (ssh_dir / name for name in ("host", "client's identity", "wrong"))
    for key in (host_key, identity, wrong):
        fixture.run(fixture.root, "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key)
    authorized_keys = ssh_dir / "authorized_keys"
    authorized_keys.write_text(identity.with_name(identity.name + ".pub").read_text())
    known_hosts = ssh_dir / "known host's keys"
    key_parts = host_key.with_suffix(".pub").read_text().split()[:2]
    command_log = ssh_dir / "git-commands"
    events = ssh_dir / "sshd.log"
    handler = ssh_dir / "git-handler.py"
    handler.write_text(
        "import os, shlex, sys\n"
        "git, repository, log = sys.argv[1:4]\n"
        "args = shlex.split(os.environ.get('SSH_ORIGINAL_COMMAND', ''))\n"
        "if len(args) != 2 or args[1] != repository or args[0] not in ('git-upload-pack', 'git-receive-pack'):\n"
        "    sys.exit(1)\n"
        "with open(log, 'a') as file:\n"
        "    file.write(args[0] + '\\n')\n"
        "os.environ.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)\n"
        "os.execv(git, [git, args[0].removeprefix('git-'), repository])\n"
    )
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    address = f"[127.0.0.1]:{port}"
    known_hosts.write_text(" ".join([address, *key_parts]) + "\n")
    config = ssh_dir / "sshd_config"
    force = shlex.join([sys.executable, str(handler), ssh_git, str(origin), str(command_log)])
    config.write_text(
        f'ListenAddress 127.0.0.1\nPort {port}\nHostKey "{host_key}"\n'
        f'PidFile "{ssh_dir / "pid"}"\nAuthorizedKeysFile "{authorized_keys}"\n'
        f"AllowUsers {username}\nForceCommand {force}\n"
        "StrictModes no\nPubkeyAuthentication yes\nPasswordAuthentication no\n"
        "KbdInteractiveAuthentication no\nAuthenticationMethods publickey\n"
        "PermitRootLogin yes\nUsePAM yes\nDisableForwarding yes\nPermitTTY no\nLogLevel VERBOSE\n"
    )
    with events.open("w+b") as log:
        daemon = subprocess.Popen(
            [sshd, "-D", "-e", "-f", config], env=fixture.env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        )
        try:
            for _ in range(100):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.1):
                        break
                except OSError:
                    if daemon.poll() is not None:
                        break
                    time.sleep(.03)
            else:
                raise AssertionError("local sshd did not start")
            if daemon.poll() is not None:
                log.seek(0)
                raise AssertionError(log.read().decode())
            url = f"ssh://{username}@127.0.0.1:{port}{origin}"
            yield url, known_hosts, identity, wrong, command_log, events
        except BaseException:
            log.seek(0)
            print(log.read().decode(), file=sys.stderr)
            raise
        finally:
            daemon.terminate()
            daemon.wait(timeout=5)


def ssh(fixture):
    origin, source = seed_git(fixture)
    with git_ssh_server(fixture, origin) as (remote, known_hosts, identity, wrong, commands, events):
        url = "git+" + remote
        original_keys = known_hosts.read_text()
        fixture.env.update(
            LIT_GIT_SSH_KNOWN_HOSTS=str(known_hosts),
            LIT_GIT_SSH_IDENTITY=str(identity),
        )
        # Ordinary Git must reach the same public-key-only server first.
        fixture.env["GIT_SSH_COMMAND"] = shlex.join([
            "ssh", "-F", "none", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes",
            "-oGlobalKnownHostsFile=none", "-o", f'UserKnownHostsFile="{known_hosts}"',
            "-i", str(identity),
        ])
        refs = fixture.git(fixture.root, "ls-remote", remote, "HEAD")
        assert refs.split()[0] == fixture.git(origin, "rev-parse", "HEAD")
        assert commands.read_text() == "git-upload-pack\n"
        before = commands.read_bytes()

        # A permissive inherited command must never bypass pinned host keys.
        fixture.env["GIT_SSH_COMMAND"] = "ssh -oStrictHostKeyChecking=no -oUserKnownHostsFile=/dev/null"
        known_hosts.write_text("")
        declined(fixture, fixture.root, "clone", url, "unknown-host", message="Git clone failed")
        assert known_hosts.read_text() == ""
        wrong_host = original_keys.split()[0] + " " + " ".join(wrong.with_suffix(".pub").read_text().split()[:2]) + "\n"
        known_hosts.write_text(wrong_host)
        declined(fixture, fixture.root, "clone", url, "changed-host", message="Git clone failed")
        assert known_hosts.read_text() == wrong_host
        known_hosts.write_text(original_keys)
        fixture.env["LIT_GIT_SSH_IDENTITY"] = str(wrong)
        declined(fixture, fixture.root, "clone", url, "wrong-key", message="Git clone failed")
        assert commands.read_bytes() == before
        fixture.env["LIT_GIT_SSH_IDENTITY"] = str(identity)

        exchange(fixture, origin, source, url)
        after = commands.read_bytes()
        connections = events.read_text().count("Connection from ")
        assert connections > 0
        airgap(fixture, url, fixture.root / "client")
        assert commands.read_bytes() == after and known_hosts.read_text() == original_keys
        assert events.read_text().count("Connection from ") == connections
        served = after.decode().splitlines()
        assert "git-receive-pack" in served
        return {"untrusted_and_changed_host_key_refused": True, "wrong_user_key_refused": True,
                "airgap_zero_ssh_connections": True, "git_sessions": {verb: served.count(verb) for verb in set(served)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--transport", choices=("https", "ssh", "both"), default="both")
    args = parser.parse_args()
    binary = args.candidate.resolve()
    with binary.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    result = {"os": platform.system(), "arch": platform.machine(), "candidate_sha256": digest}
    for transport in (https, ssh):
        if args.transport in ("both", transport.__name__):
            with tempfile.TemporaryDirectory(prefix="lit-git-auth-") as directory:
                result[transport.__name__] = transport(Fixture(Path(directory).resolve(), binary))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
