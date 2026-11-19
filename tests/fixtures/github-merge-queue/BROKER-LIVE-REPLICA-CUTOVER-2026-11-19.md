# Live 100latent broker replica cutover

On November 19, 2026 (Pacific time), host-c became the sandbox's independent
authority and sole deployed signing host. host-a and host-d run broker processes
with distinct replica identities. The old host-a service stopped and its GitHub
App key was removed. The replica restored the pre-cutover claims, proposals,
action history and queued-operation records. A root-owned service lock initially
prevented startup on host-a. Correcting its owner and mode restored service with
the same authority.

The watcher detected the paused host-a broker and promoted host-d in
21.70 seconds using the replicated state. The watcher had no
source-side SSH path. The returning broker answered HTTP 503, and its
old replica identity was refused at both tested epochs. The shared host
stayed powered on during the exercise.

host-d published and registered [PR #326](https://github.com/100latent/lit-merge-queue-sandbox/pull/326).
host-a recovered that receipt after failback. GitHub Actions passed the
[pull request check](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44554064268) on the proposal head
\`caaf9548f5ae814abe26c5ecef8a28ca17dff1d4\` and the [merge-group check](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44554920786)
on the exact merged Git commit \`03d9140f8a706e3dfc2648c43ed32a9bc5c8b249\`.
A Git fetch verified both merge parents and the
published file's bytes. The Actions-only main rule and broker namespace rule
matched their original projections. The freeze rule gained the proposal's exact
ref and both checked commits had zero manual statuses.

The repeated handoff isolated the watcher's network path to host-a while
keeping the old process alive. The same cached standby restored the latest
replica checkpoint in 8.29 seconds. The client recovered the
existing publication receipt through host-d after host-a answered HTTP 503.
The verified integration record survived the second failback. A separate
host-c service outage refused a new assignment with HTTP 503. On restart,
the replica returned the identical authority and the primary resumed its
controller cycle.

Two repository-scoped writer canaries returned 403 during App suspension and
200 after resumption. Each was explicitly revoked and then returned 401.
A separate token expiry control was scheduled for 2026-11-19T16:25:15.050872+00:00.

All 121 Python regressions and the 100-owner provider stress test passed on
macOS, host-a, host-e and host-f. The macOS Rust suite, native and Git remote probes,
and strict sandbox checks also passed. All implementation and regression source
came from the MIT-derived branch. The v1.8.0 release remained a black box.

[Structured results](broker-live-replica-cutover-2026-11-19.json) retain source hashes, the Actions run
identities and the verified transition records.
