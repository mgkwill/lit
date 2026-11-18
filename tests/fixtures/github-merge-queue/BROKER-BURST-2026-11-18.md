# Authenticated broker burst — 2026-11-18

Completed **30 distinct mTLS agent identities across three hosts**, producing
**34 real PRs: 30 merged, four replaced attempts retired**. Three queued attempts
were reassigned across hosts; a fourth reassignment explicitly resolved a held
shared-file conflict. Native Git verified all 30 task contributions in 29 final
files. No replaced head is an ancestor of final main.

The broker remains deployed and healthy at `https://192.0.2.10:19443` on host-a.
All 30 temporary identities were removed from its allowlist and their host-local
private keys deleted. The original broker configuration and main ruleset settings
are restored. The native queue is empty and no remote effects remain pending.

## Workload and scope

Ten synthetic clients ran concurrently on each of host-a (Linux x86_64), host-e
and host-f (Linux arm64). Each used a separately generated private key, a distinct
CA-signed certificate fingerprint, and a distinct logical owner. The broker App,
not an individual App per worker, authored all PRs. The workers never received a
GitHub write credential or signing key. All initial proposals were pinned to
`d021d9f8a86287da0a41ff230f96172a32a288d2` and submitted through `/propose`, then enrolled through
`/register` after the trusted scheduler froze their exact refs.

Twenty-eight tasks edited disjoint files; two added different contents at the
same shared-file path. The clients exercise broker contracts with tiny text
changes. This is not 30 independent LLM development sessions or a controlled
throughput benchmark. Profiles share the existing account on each host; distinct
certificates do not isolate hostile processes sharing that account. On host-a the
clients use a different UID from the broker service.

The recovery source started at `49834391be072651ae6ab4150c6d783e374ae1bb` on
`codex/mit-lit-recovery`. No post-MIT upstream Lit source was read or used for this
qualification. v1.8.0 remained black-box and was not invoked. Core broker and Rust
source did not change. Source fingerprints accompany the structured results;
this is not a complete provenance audit or final transcript archive.

## Live reassignment and conflict handling

All three old attempts were observed queued before reassignment. Advancing the
generation immediately rejected their real old capabilities for publication and
registration. Successors received no capability while `draining`. The broker
withdrew and closed each old PR, observed retirement, then activated its successor
on a different host. All successor PRs merged; all old heads remained excluded.

| Transfer | Retired queued PR | Merged successor |
|---|---|---|
| host-a → host-e | [#114](https://github.com/100latent/lit-merge-queue-sandbox/pull/114) | [#141](https://github.com/100latent/lit-merge-queue-sandbox/pull/141) |
| host-e → host-f | [#129](https://github.com/100latent/lit-merge-queue-sandbox/pull/129) | [#142](https://github.com/100latent/lit-merge-queue-sandbox/pull/142) |
| host-f → host-a | [#134](https://github.com/100latent/lit-merge-queue-sandbox/pull/134) | [#140](https://github.com/100latent/lit-merge-queue-sandbox/pull/140) |

Shared-file PR [#131](https://github.com/100latent/lit-merge-queue-sandbox/pull/131) won. The broker held
[#135](https://github.com/100latent/lit-merge-queue-sandbox/pull/135); native `git merge-tree --write-tree`
independently returned an add/add conflict against current main. The scheduler
reassigned that task from host-f to host-a. The old capability remained rejected,
and the successor stayed fenced until the old PR was closed and retired.

The explicit resolution, [#143](https://github.com/100latent/lit-merge-queue-sandbox/pull/143), used a fresh
base and preserved both original contributions. It merged as
`47754eff72b37d84b15810dfa34929f6933b36fc` through
[its native merge-group run](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44277807064). This was an explicit test
resolution, not an automatic conflict-resolution feature. No worker changed a
frozen proposal head or directly published to main.

## Actions-only enforcement

Main retained its required PR, native merge queue, ALLGREEN policy, one entry per
merge, required `Agent workflow candidate` check bound to Actions App 15368, and
no bypass actors. No manual statuses or workflow dispatches were used.

The initial plan kept one build slot. After observing approximately five minutes
per native candidate, a recorded operational amendment temporarily changed only
`max_entries_to_build` from 1 to 30, after the three stale attempts retired. Native
builds then ran concurrently. This setting controls CI dispatch
([GitHub documentation](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue)).
All other main-rule security fields stayed unchanged. Once the first 29 merges
completed and the queue was empty, capacity returned to 1 before the resolution
entered the queue. Final writable security fields match the original rule exactly.
Freeze rules retain the exact tested refs for audit.

A representative new identity on each host was denied scheduler routes, peer
registration, direct GitHub ref creation and GraphQL enqueue. Its broker-issued
GitHub token could read the private repository and had only Contents and Pull
requests read permissions. All three legacy worker App installations remain
suspended. Privileged organization and host operators remain trusted.

## Verification

- All 34 proposal identities and original heads were checked. Each of the 30
  landed commits had its own successful native `merge_group` run and App 15368
  check suite at that exact SHA. Logs checked the candidate SHA, six batch
  regressions, and the 10/50/100-worker native fixtures. Those fixtures are
  separate from the 30 live authenticated clients in this run.
- Zero manual statuses were present on all proposal heads and landed candidates.
- Native Git fetched with a broker-issued read-only token. Strict full fsck
  passed. Every merged head is an ancestor of main; the four replaced heads are
  not. Each merge changed only its assigned path, with exact expected bytes.
  All 29 final files match, including both shared-file contributions.
- Completed publication retries returned identical receipts. There are exactly
  34 distinct PR receipts, no duplicates, no pending/acknowledged remote effects,
  and no remaining queue entries.
- All 30 temporary principals were revoked. A representative removed certificate
  on each host received `forbidden` before its keys were deleted. Broker restart
  preserved terminal state and resumed a successful authenticated controller cycle.

| Native platform | Coordinator regressions | Broker regressions | Result |
|---|---:|---:|---|
| macOS arm64 | 41 | 21 | Passed |
| host-a Linux x86_64 | 41 | 21 | Passed |
| host-e Linux arm64 | 41 | 21 | Passed |
| host-f Linux arm64 | 41 | 21 | Passed |

The new Python suites ran natively on these hosts. Sandbox Actions ran its existing
Rust/workflow fixtures, not the recovery branch's new Python broker source. The
full Rust suite and binary smoke tests were not repeated in this task; core Rust
and binaries are unchanged.

## Adverse evidence and scaling limits

The initial permission probe mistakenly used the broker's private CA trust store
for GitHub and failed TLS validation. The harness now uses independent trust
stores. All three probes were rerun and passed; failed logs and the earlier
source reconstruction are retained. The 30 publications were not repeated. The first cleanup revocation probe also
arrived before the restarted listener was ready. The cleanup helper now waits
through transport startup failures and still fails if a removed identity is
accepted; actual forbidden responses were then verified on all three hosts.
The initial cleanup failure and helper revision are preserved.

The initial publication phase spanned approximately
204.4 seconds
on synchronized client clocks, with 1,415
HTTP 409 retries. The broker serializes publication under its writer lock. Its
`rejected_or_busy` response does not identify every rejection cause, so these
retries cannot all be attributed conclusively to lock contention. Client retry
counts differed substantially by host. Controller logs also contained transient
RuntimeError/ApiError classifications; the final health cleared and all effects
were reconciled. Raw logs remain available rather than being replaced by the
successful final result.

Before scaling to 100 clients, prioritize fair bounded admission and structured
retry/unknown-outcome responses instead of client spinning. Then extend regular
UTF-8 add/update proposals to full Git objects, binaries, deletions and modes if
needed. Task dependency/overlap hints, isolated untrusted workers, availability,
certificate renewal and removal of the Mac login-session observer dependency
remain open. Broader low-priority product parity remains deferred; this workload
does not qualify every missing v1.8.0 feature.

The reusable client is [broker-burst-worker.py](broker-burst-worker.py).
[Structured results](broker-burst-results-2026-11-18.json) contain per-PR run URLs,
source hashes, identities, denial receipts and cleanup evidence. Raw positive and
adverse evidence, controller scripts, API responses and logs remain at
`<private-path>`. Original private claim receipts were replaced by redacted receipts with
their original-record SHA256; no keys, tokens or capabilities are committed.
