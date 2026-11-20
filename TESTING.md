# Test OpenLit

Run tests from the MIT recovery checkout. Do not fetch post-MIT upstream source
or tests as implementation inputs. Historical references can be exercised only
as black-box executables. See [AGENTS.md](AGENTS.md).

## Local Rust tests

```sh
cargo fmt -- --check
cargo test --locked -- --test-threads=1
cargo build --locked --release
cargo doc --locked --no-deps
```

Use one Rust test thread: some older command tests change the process working
directory or environment. Keep the test repositories disposable. A benchmark
is not a concurrency correctness test.

## Binary behavior

```sh
python3 -B tests/release_smoke.py target/release/lit
cargo test --locked --test git_interop_regressions -- --test-threads=1
python3 -B tests/remote_regression.py target/release/lit
python3 -B tests/authenticated_git_remote_regression.py target/release/lit
python3 -B tests/sandbox_regression.py target/release/lit
python3 -B tests/workspace_regression.py target/release/lit
```

The authenticated remote fixture uses local HTTPS and SSH services. Read its
requirements before running it; results are distinct from live GitHub access.
Workspace tests require macOS or Linux. Windows strict sandbox tests require
refusal before payload launch. Migration tests also take a separate reference
binary; use the intended observed version and record both hashes.

## Broker and controller

```sh
python3 -B tests/github_queue_coordinator_regression.py
python3 -B tests/task_broker_regression.py
python3 -B tests/github_app_credentials_regression.py
python3 -B tests/task_broker_failover_regression.py
python3 -B tests/task_broker_replica_regression.py
python3 -B tests/task_broker_quorum_regression.py
python3 -B tests/pi_pilot_runner_regression.py
python3 -B tests/pi_pilot_recovery_regression.py
python3 -B tests/pi_pilot_lab_regression.py
python3 -B tests/pi_pilot_control_regression.py
```

These use disposable fixtures, including loopback forges and simulated workers.
The 100-attempt controller test does not launch 100 language-model sessions.
Live tests use the private 100latent sandbox, the Actions-only rule, and bounded
Pi sessions with inference on host-a. They need their own authorization and
credentials; do not run a live burst as an ordinary unit test.

## Release archives

The release qualification workflow uses five native runners: Linux x86-64 and
arm64 on Ubuntu 22.04, macOS Intel and Apple Silicon on macOS 15, and Windows
x86-64 on Server 2022. It runs `tests/qualify_release_archive.py` against the
extracted archive. Linux checks require glibc 2.35 and compatible symbol versions.

Archive tests verify identity, version, license, batch rollback, MCP over stdio
and HTTP, native and Git remotes, and Windows sandbox refusal. The workflow has
no publication step. Signed release bytes need another qualification after signing.

Use host-a for Linux x86-64 and host-e/host-f for arm64 follow-up tests. Do not skip
x86-64 because arm64 passed. Record the exact candidate hash on every host.

## Record results

Save source commit, binary hashes, commands, platforms, exit codes, failures,
and limits. Keep historical results under `tests/fixtures/` unchanged. Distinguish
local success, hosted Actions, lab execution, live authorization, and release
signing. [Earlier five-platform proof](tests/fixtures/release-qualification/2026-11-20-compatibility.md)

Active docs and examples can be checked with `python3 -B tests/docs_smoke.py target/release/lit` once the binary has been built.
