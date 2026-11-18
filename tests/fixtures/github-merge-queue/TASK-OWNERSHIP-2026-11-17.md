# Task ownership and reassignment fencing, 2026-11-17

Implemented independently on `codex/mit-lit-recovery` after `cfdf7e6`.
The source lineage remains the MIT baseline `98c59bb`. Neither v1.8.0 nor
other post-MIT upstream implementation was fetched or inspected for this work.
The new scheduler contract uses the existing recovered coordinator and
GitHub's documented public API.

## Behavior

- A trusted scheduler claims or reassigns a task using an expected generation,
  owner App bot, pinned base and unique idempotency request. Each successful
  assignment advances the generation. Exact retries return the same receipt;
  stale or changed requests cannot revive ownership.
- A worker's attempt capability binds exactly one immutable PR. Registration
  checks task, generation, owner, assigned base and capability atomically with
  the binding. Unowned registration cannot bypass enabled task authority.
- A per-task lock spans authority checks, durable operation intent and remote
  publication. Same-task reassignment reports busy while a call is in flight;
  claims for another task remain possible. SQLite serializes competing claims.
- Reassignment revokes the old generation. If its claim was bound, the next
  owner receives no capability until the old PR has been withdrawn and then
  observed closed or merged with no queue entry. The controller closes even an
  unqueued old PR, preventing a late unknown enqueue from surviving handoff.
- Killed processes and lost replies preserve intent. Closure fences unresolved
  actions without relabeling them as observed. An unobservable close remains
  draining rather than granting authority or blindly retrying. A merge that
  completed before retirement is recorded, not rolled back.

Legacy fixed-attempt states remain usable. They require a fresh authority state
to enable task ownership. Capabilities never appear in status or persisted as
raw tokens. The private authority database contains a capability derivation key;
access to it is administrative access. The assigned base is matched as metadata,
not verified as a Git ancestor.

## Verification

| Environment | Architecture | Coordinator tests |
|---|---|---|
| Local macOS | arm64 | 40 passed |
| host-a Linux | x86_64 | 40 passed |
| host-e Linux | arm64 | 40 passed |
| host-f Linux | arm64 | 40 passed |

The full local Rust suite also passed: `cargo test --locked -- --test-threads=1`,
486 tests across library, binary, integration and documentation targets. No Rust
source changed. This broader Rust run was local macOS only.

The coordinator suite contains the prior 23 regressions and 17 ownership tests.
Each platform runs native independent CLI processes against a stateful external
HTTP forge and real Git fixtures. The claim race starts 12 processes for an
initial assignment, then 12 for reassignment; each generation has one winner.
An eight-trial registration/reassignment race verifies that a bound stale PR
keeps its successor draining. Other cases cover A-to-B-to-A ownership, forged
(including non-ASCII input) and revoked capabilities,
immutable bindings, failed multi-proposal registration rollback, policy/head
changes, revoked credentials, in-flight publication killed after remote
acceptance, lost dequeue/close replies, merged attempts and genuinely unknown
close outcomes. No fake manual status is created.

Source fingerprints, versions and transcripts are preserved in
[structured results](task-ownership-results-2026-11-17.json). Exact host scratch
source files were removed after successful tests. No App key or token was
created and no live GitHub policy was changed for this qualification.

## Enforcement boundary and next work

This is a single-machine authority core owned by a trusted scheduler, with
capabilities delivered through a future authenticated broker. Workers must not
have the authority filesystem, administrative CLI access or the broker's GitHub
write token. Deploying copies of the database on separate hosts does not form a
distributed authority. These runs qualify the implementation on each platform,
not live cross-host authenticated publication.

The sandbox Apps can still make direct GitHub writes outside this controller.
Broker deployment and forge enforcement against direct bypass, followed by a
live reassignment pilot under the unchanged Actions-only rule, are the next
high-value step. Keep GitHub's original Actions-only rule; task authority must
complement native integration checks rather than replace them.

Dependency-aware scheduling, overlap hints and operator-supported resolution of
unobservable RPCs remain. Broader history, hosting, identity and migration
parity is still deferred under the agent-workflow priorities. This change does
not make the older full-parity rebuild plan complete.
