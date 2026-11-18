# Durable, fair broker admission — 2026-11-18

Deployed broker API version 2 on host-a at `https://192.0.2.10:19443`.
The source now durably admits publication requests before GitHub work, serves
logical owners in round-robin order, and distinguishes accepted, capacity,
permanent rejection and unknown-outcome responses. The updated CLI polls accepted
work and retries only explicitly transient responses. Core Rust is unchanged.

Implementation inputs were the independently authored recovery source at
`f1b5c8551bafc6dcaa03ae07e0c42f45ec7377a7` on `codex/mit-lit-recovery`, rooted in MIT baseline
`98c59bb2dbebcf7f575993a4ad89e1300a578c83`. No post-MIT upstream Lit source was
read or used; v1.8.0 remained black-box and was not invoked. This qualification
is not a complete provenance audit or final session-record archive.

## Contract and safety

- `POST /propose` returns HTTP 202 after committing a job and its owner, claim,
  digest and payload to the private authority database. Capabilities are checked
  at intake and are not stored with queued payloads. Duplicate identical requests
  join the same operation; a changed request cannot replace it.
- Poll the owner-scoped `/publication?claim=ID` URL. HTTP 200 is a published PR
  receipt. A 202 is accepted/pending, with a poll delay; it is not permission to
  repeat a mutation. Wrong-owner and revoked attempts are rejected.
- Defaults bound queued/running work to 128 jobs globally and four per logical
  owner. Capacity rejection is `429 admission_full`, with `retryable:true` and
  Retry-After. Lock contention is explicitly `503 authority_busy`. Permanent
  request/capability errors and unknown effects have `retryable:false`.
- Owners take round-robin turns, with the cursor persisted across restarts.
  Each owner's oldest queued job remains first even during temporary precondition
  backoff. Other owners can proceed. Native queue-controller turns examine at
  most four eligible proposals before yielding; provider call duration is not
  bounded by this scheduling limit.
- Execution rechecks the live assignment under the task fence and the existing
  GitHub policy/credential guards. Revoked queued work cannot reach GitHub.
  Temporary failure before publication intent leaves work queued with a visible
  blocked reason. Once a remote intent exists, uncertain writes are never replayed.
- Restart preserves queued work. A job interrupted while running resolves to its
  matching durable published receipt or becomes `publication_outcome_unknown`.
  Even an interruption before the first provider call is conservatively uncertain.
  Polling never starts execution. Schema recovery takes the authority writer lock.
- Ready-file publication is atomic. Scheduler status exposes job phases and safe
  error codes, without queued files or capabilities. API `/health` reports version 2.

The HTTP behavior is a versioned change: raw clients must handle 202 instead of
assuming every successful POST synchronously returns a PR. The CLI waits by
default; `--no-wait` returns an admission receipt, and `--claim ID publication`
resumes polling. Wait timeouts preserve accepted work. Drain/reconcile jobs before
rolling back to a pre-v2 broker, which does not process the new admission table.
See [the deployment contract](../../../tools/deployment/README.md).

## Native regression qualification

| Platform | Coordinator | Broker | Result |
|---|---:|---:|---|
| Local macOS arm64 | 41 | 32 | Passed |
| host-a Linux x86_64 | 41 | 32 | Passed |
| host-e Linux arm64 | 41 | 32 | Passed |
| host-f Linux arm64 | 41 | 32 | Passed |

Tests use the authenticated HTTP/CLI interfaces, a stateful external forge, real
SQLite authority and OS locks. They cover acceptance while a provider write is
blocked, duplicate publication, global/per-owner capacity, owner fairness,
precondition backoff/FIFO, scoped status, queued revocation, CLI resume and unknown
errors. SIGKILL after an external mutation leaves that job uncertain while safely
queued work resumes, without repeating the interrupted mutation. Existing
retirement, competing-claim, read-only credential and Actions-only regressions pass.

A load case admits 30 jobs through two authenticated owners while the first write
is blocked, without retrying their POSTs on the coordinator writer lock. It then
publishes all 30. This is a native external-forge test, not a 30-identity live
GitHub burst or a throughput benchmark. Rust and release binaries did not change;
the full Rust suite and binary smoke tests were not rerun.

## Live deployment and host pilot

Qualified source hashes matched deployed server code and CLI/fixture copies on
all three lab hosts. The existing broker App and mTLS identities were reused;
no new GitHub write credential, signing key or worker private key was distributed.
The existing authority was preserved during the service upgrade.

Each host received its 202 receipt before publication completed. The updated
worker fixture polled/joined the operation and checked identical completed replay.
Exactly one broker-authored PR per host was created. After exact-ref freezing and
registration, the scheduler reassigned each task to another host. Old proposals
were closed/retired before successor capabilities became available; replay with
the real old capability returned permanent `assignment_revoked` on all hosts.

| Host | Initial admission state | Proposal | Final state |
|---|---|---|---|
| host-a | queued | [#144](https://github.com/100latent/lit-merge-queue-sandbox/pull/144) | Closed/retired |
| host-e | queued | [#145](https://github.com/100latent/lit-merge-queue-sandbox/pull/145) | Closed/retired |
| host-f | queued | [#146](https://github.com/100latent/lit-merge-queue-sandbox/pull/146) | Closed/retired |

These pilot proposals were deliberately retired, not merged. Their now-unneeded
native preflight runs were canceled after closure. No manual statuses were created,
no workflow dispatch was used and no policy was weakened. Main stayed at
`47754eff72b37d84b15810dfa34929f6933b36fc` and the original Actions-only rule's writable security
fields were unchanged. Freeze rules retain the exact refs for audit. Admission
jobs are done, queue effects are retired, and the upgraded broker is left running
and healthy. The live test does not claim CI success or exact-merge qualification
for these deliberately closed candidates.

## Adverse evidence and limits

The synchronous implementation failed the first blocked-write acceptance test.
An early executor loop also held the writer lock while idle, starving enrollment;
that was corrected before deployment. host-e caught a partially written ready file,
fixed by atomic publication at the producer rather than by suppressing JSON errors.
The FIFO regression showed a blocked job could be overtaken by the same owner's
later work; head-only eligibility now preserves order without blocking peers.
Failed-run logs and earlier source archives remain outside the repository alongside
final receipts. Final four-platform runs cover the exact deployed source hashes.

The previous 30-identity real GitHub burst qualified API v1. This change has a
30-job native forge load test and a three-host real GitHub pilot; a larger live
v2 cohort and 100-agent workload are still open. No controlled comparison to the
previous 1,415-retry result is claimed. Profiles share trusted host accounts;
hostile clients sharing those accounts are not isolated. Privileged host and
organization operators remain trusted.

Next priorities are a larger live v2 burst, full Git-object/binary/delete/mode
publication, scheduler dependency/overlap hints, and availability/credential
renewal. Provider throttling/latency limits, orphan cleanup for genuinely uncertain
unregistered publications and broader deferred product parity remain open.

[Structured results](admission-results-2026-11-18.json) pin source and evidence
hashes. Raw API responses, logs, source archives and the deployment/test scripts
are retained at `<private-path>`. No keys, tokens or claim capabilities are committed.
