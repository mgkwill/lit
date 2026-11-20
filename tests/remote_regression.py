"""Local remote integration regression. Run with the absolute path to a Lit binary."""

import json
import hmac
import os
import socket
import time
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

LIT = None
REFERENCE = False


class Fixture:
    def __init__(self, root, binary=None):
        self.root = root
        self.binary = binary or LIT
        self.home = root / "home"
        self.home.mkdir()
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "LIT_OUTPUT": "json",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.test",
            "GIT_COMMITTER_NAME": "Fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.test",
        }
        for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
            if name in os.environ:
                self.env[name] = os.environ[name]

    def run(self, cwd, binary, *args, success=True):
        result = subprocess.run(
            [binary, *args], cwd=cwd, env=self.env, capture_output=True,
            text=True, timeout=25,
        )
        if success is not None:
            assert (result.returncode == 0) == success, (args, result.stdout, result.stderr)
        return result

    def git(self, cwd, *args):
        return self.run(cwd, "git", *args).stdout.strip()

    def lit(self, cwd, *args):
        return json.loads(self.run(cwd, self.binary, *args).stdout)

    def assert_failure(self, cwd, *args):
        return self.run(cwd, self.binary, *args, success=False)

    def make(self, name):
        path = self.root / name
        path.mkdir()
        return path


@contextmanager
def smart_http(fixture, *, tls=None, authorization=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1" if tls else "HTTP/1.0"

        def serve_git(self):
            url = urlsplit(self.path)
            if authorization and not hmac.compare_digest(self.headers.get("Authorization", ""), authorization):
                requests.append(("401", url.path, url.query))
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Lit fixture"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            requests.append((self.command, url.path, url.query))
            environment = dict(
                fixture.env, GIT_PROJECT_ROOT=str(fixture.root),
                GIT_HTTP_EXPORT_ALL="1", PATH_INFO=url.path, QUERY_STRING=url.query,
                REQUEST_METHOD=self.command, CONTENT_TYPE=self.headers.get("Content-Type", ""),
                HTTP_GIT_PROTOCOL=self.headers.get("Git-Protocol", ""),
            )
            if authorization:
                environment["REMOTE_USER"] = "fixture"
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            environment["CONTENT_LENGTH"] = str(len(body))
            output = subprocess.run(
                ["git", "http-backend"], input=body, env=environment, capture_output=True,
                check=True, timeout=10,
            ).stdout
            head, payload = output.split(b"\r\n\r\n", 1)
            headers = [line.decode().split(": ", 1) for line in head.split(b"\r\n")]
            status = next((int(value.split()[0]) for key, value in headers if key == "Status"), 200)
            self.send_response(status)
            for key, value in headers:
                if key.lower() not in ("status", "content-length"):
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = serve_git

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        if tls:
            server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            scheme = "https" if tls else "http"
            yield f"{scheme}://127.0.0.1:{server.server_port}/origin.git", requests
        finally:
            server.shutdown()
            thread.join()


@contextmanager
def native_daemon(fixture, bare):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    with tempfile.TemporaryFile() as log:
        daemon = subprocess.Popen(
            [LIT, "serve", "--daemon", "--port", str(port)], cwd=bare,
            env=fixture.env, stdout=log, stderr=log,
        )
        try:
            for _ in range(100):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    assert daemon.poll() is None, "native daemon exited"
                    time.sleep(0.03)
            else:
                raise AssertionError("native daemon did not start")
            yield f"lit://127.0.0.1:{port}/native-bare"
        finally:
            daemon.terminate()
            daemon.wait(timeout=5)


def native_over_daemon(f):
    bare = f.make("native-bare")
    source = f.make("native-source")
    f.lit(bare, "init", "--bare", ".")
    f.lit(source, "init", ".")
    (source / "a").write_text("served")
    f.lit(source, "add", "a")
    head = f.lit(source, "commit", "-m", "served commit")["hash"]
    with native_daemon(f, bare) as url:
        (f.home / ".litconfig").write_text(
            f'allowed_hosts = ["{urlsplit(url).netloc}"]\nallowed_networks = []\naudit_log = false\n'
        )
        f.lit(source, "remote", "add", "origin", url)
        f.lit(source, "push", "origin", "main")
        client = f.root / "daemon-clone"
        f.lit(f.root, "clone", url, str(client))
        assert (client / "a").read_text() == "served"
        assert f.lit(client, "status")["head"] == head
        assert f.lit(client, "status")["clean"]
    print("lit://: native first push and bare clone")


def native(f):
    bare = f.make("native-bare")
    source = f.make("native-source")
    f.lit(bare, "init", "--bare", ".")
    f.lit(source, "init", ".")
    (source / "story").write_text("base\n")
    f.lit(source, "add", "story")
    base = f.lit(source, "commit", "-m", "base")["hash"]
    branch = "main" if REFERENCE else "release/trunk"
    if not REFERENCE:
        f.lit(source, "checkout", "-b", branch)
    f.lit(source, "remote", "add", "origin", bare.as_uri())
    f.lit(source, "push", "origin", branch)
    args = ("clone", bare.as_uri(), str(f.root / "bare-probe"))
    if REFERENCE:
        failed = f.assert_failure(f.root, *args)
        print("v1.8.0 bare clone exit:", failed.returncode, failed.stderr.strip())
        (bare / ".lit/HEAD").write_text("ref: refs/heads/main\n")
    else:
        f.lit(f.root, *args)
        assert (bare / ".lit/HEAD").read_text().strip() == f"ref: refs/heads/{branch}"
        assert f.lit(f.root / "bare-probe", "status")["clean"]
        (bare / ".lit/HEAD").unlink()  # simulate a legacy bare repository
    client = f.root / "native-client"
    f.lit(f.root, "clone", bare.as_uri(), str(client))
    assert (client / "story").read_text() == "base\n"
    assert f.lit(client, "status")["head"] == base
    (source / "story").write_text("remote change\n")
    f.lit(source, "add", "story")
    tip = f.lit(source, "commit", "-m", "advance")["hash"]
    f.lit(source, "push", "origin", branch)
    if not REFERENCE:
        (client / "story").write_text("keep local edit\n")
        f.assert_failure(client, "pull", "origin", branch)
        assert (client / "story").read_text() == "keep local edit\n"
        assert f.lit(client, "status")["head"] == base
        (client / "story").write_text("base\n")
    result = f.lit(client, "pull", "origin", branch)
    assert result["fast_forward"]
    assert f.lit(client, "status")["head"] == tip
    if REFERENCE:
        assert (client / "story").read_text() == "base\n"
        print("v1.8.0 pull advanced HEAD and left stale working bytes")
    else:
        assert (client / "story").read_text() == "remote change\n"
        assert f.lit(client, "status")["clean"]
        print("Native: first push, legacy bare clone, dirty refusal, fast-forward, clean index")


def seed_git(f):
    origin = f.make("origin.git")
    source = f.make("source")
    f.git(origin, "init", "--bare", "--object-format=sha1", "-b", "release/trunk")
    f.git(origin, "config", "http.receivepack", "true")
    f.git(source, "init", "--object-format=sha1", "-b", "release/trunk")
    (source / "story").write_text("base\n")
    (source / "obsolete").write_text("delete me\n")
    if os.name == "posix":
        (source / "link").symlink_to("story")
    f.git(source, "add", ".")
    f.git(source, "commit", "-m", "seed")
    f.git(source, "tag", "-am", "tag", "v1")
    f.git(source, "branch", "topic/other")
    f.git(source, "push", origin.as_uri(), "--all")
    f.git(source, "push", origin.as_uri(), "--tags")
    f.git(origin, "gc", "--prune=now")
    return origin, source


def git_flow(f, http):
    origin, source = seed_git(f)
    if http:
        with smart_http(f) as (url, requests):
            if REFERENCE:
                reference_git(f, url)
                return
            f.assert_failure(f.root, "--airgapped", "clone", url, "blocked")
            assert not requests, "airgap check must precede endpoint discovery"
            exchange(f, origin, source, url)
            assert ("POST", "/origin.git/git-upload-pack", "") in requests
            assert ("POST", "/origin.git/git-receive-pack", "") in requests
            print("Smart HTTP upload-pack and receive-pack requests observed:",
                  sum(path.endswith("git-upload-pack") for _, path, _ in requests),
                  sum(path.endswith("git-receive-pack") for _, path, _ in requests))
    elif REFERENCE:
        reference_git(f, origin.as_uri())
    else:
        exchange(f, origin, source, origin.as_uri())


def reference_git(f, url):
    client = f.make("reference-git-client")
    f.lit(client, "init", ".")
    (client / "a").write_text("native seed")
    f.lit(client, "add", "a")
    f.lit(client, "commit", "-m", "seed")
    f.lit(client, "checkout", "-b", "release/trunk")
    f.lit(client, "remote", "add", "origin", url)
    results = [
        f.assert_failure(f.root, "clone", url, "reference-git-clone").returncode,
        *(f.assert_failure(client, verb, "origin", "release/trunk").returncode
          for verb in ("fetch", "pull", "push")),
    ]
    print(f"v1.8.0 Git {url.split(':')[0]} clone/fetch/pull/push exits: {results}")


def exchange(f, origin, source, url):
    client = f.root / "client"
    branch = "release/trunk"
    cloned = f.lit(f.root, "clone", url, str(client))
    assert set(cloned["branches_cloned"]) == {"release/trunk", "topic/other"}
    status = f.lit(client, "status")
    assert status["branch"] == branch and status["clean"]
    base = status["head"]
    assert (client / "story").read_text() == "base\n"
    if os.name == "posix":
        assert os.readlink(client / "link") == "story"
    exported = f.root / "clone-check.git"
    f.lit(client, "export-git", str(exported))
    assert f.git(exported, "rev-parse", "HEAD") == f.git(origin, "rev-parse", "HEAD")
    assert f.git(exported, "rev-parse", "refs/tags/v1") == f.git(origin, "rev-parse", "refs/tags/v1")

    (source / "story").write_text("Git change\n")
    f.git(source, "rm", "obsolete")
    (source / "incoming").write_text("new remote file\n")
    f.git(source, "add", "story", "incoming")
    f.git(source, "commit", "-m", "Git update")
    f.git(source, "push", origin.as_uri(), branch)
    fetch = f.lit(client, "fetch", "origin")
    assert len(fetch["branches_updated"]) == 1
    assert f.lit(client, "status")["head"] == base
    assert (client / "story").read_text() == "base\n"
    (client / "incoming").write_text("local untracked file\n")
    f.assert_failure(client, "pull", "origin", branch)
    assert f.lit(client, "status")["head"] == base
    assert (client / "incoming").read_text() == "local untracked file\n"
    (client / "incoming").unlink()
    pulled = f.lit(client, "pull", "origin", branch)
    assert pulled["fast_forward"]
    assert (client / "story").read_text() == "Git change\n"
    assert not (client / "obsolete").exists()
    assert f.lit(client, "status")["clean"]

    previous = f.git(origin, "rev-parse", "HEAD")
    (client / "story").write_text("Lit change\n")
    f.lit(client, "add", "story")
    f.lit(client, "commit", "-m", "Lit update")
    assert f.lit(client, "push", "origin", branch)["updated"]
    assert not f.lit(client, "push", "origin", branch)["updated"]
    assert f.git(origin, "show", "HEAD:story") == "Lit change"
    assert f.git(origin, "rev-parse", "HEAD^") == previous
    f.git(origin, "fsck", "--full", "--strict")
    f.git(source, "pull", "--ff-only", origin.as_uri(), branch)
    (source / "story").write_text("Git after Lit push\n")
    f.git(source, "commit", "-am", "descendant of native Lit commit")
    f.git(source, "push", origin.as_uri(), branch)
    assert f.lit(client, "pull", "origin", branch)["fast_forward"]
    assert (client / "story").read_text() == "Git after Lit push\n"
    assert f.lit(client, "status")["clean"]

    (client / "story").write_text("local divergence\n")
    f.lit(client, "add", "story")
    local = f.lit(client, "commit", "-m", "diverge in Lit")["hash"]
    (source / "story").write_text("remote divergence\n")
    f.git(source, "commit", "-am", "diverge in Git")
    f.git(source, "push", origin.as_uri(), branch)
    remote_tip = f.git(origin, "rev-parse", "HEAD")
    f.assert_failure(client, "push", "origin", branch)
    f.assert_failure(client, "pull", "origin", branch)
    assert f.git(origin, "rev-parse", "HEAD") == remote_tip
    assert f.lit(client, "status")["head"] == local
    assert (client / "story").read_text() == "local divergence\n"
    assert f.lit(client, "push", "--force", "origin", branch)["updated"]
    assert f.git(origin, "show", "HEAD:story") == "local divergence"
    print(f"{url.split(':')[0]}: clone, fetch, pull, push, no-op push, cross-format ancestry, divergence refusal")


def main():
    global LIT, REFERENCE
    LIT = Path(sys.argv[1]).resolve()
    REFERENCE = "--reference" in sys.argv[2:]
    with tempfile.TemporaryDirectory() as root:
        native(Fixture(Path(root)))
    if not REFERENCE:
        with tempfile.TemporaryDirectory() as root:
            native_over_daemon(Fixture(Path(root)))
    for http in (False, True):
        with tempfile.TemporaryDirectory() as root:
            git_flow(Fixture(Path(root)), http)


if __name__ == "__main__":
    main()
