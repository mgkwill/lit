# Live v2 broker burst — 2026-11-18

Completed **60 distinct authenticated v2 clients across host-a, host-e and host-f**.
The workload created **68 real PRs: 60 merged and eight replaced attempts retired**.
Six queued tasks transferred across hosts; two held add/add conflicts were resolved
explicitly in fresh fenced attempts. Git verified all 60 task contributions in
58 final files, and every landed commit matched its own successful native Actions
merge-group check. No replaced head is an ancestor of final main.

## Workload and source boundary

Each host ran 20 concurrent synthetic client threads with distinct owner IDs,
certificate fingerprints and host-generated private keys. They published small
UTF-8 text edits through `/propose`, received durable admission receipts, polled
status and checked identical completed replay. The broker App authored all PRs;
logical workers never received GitHub write tokens or signing keys. Profiles share
trusted per-host OS accounts; this is not hostile-process isolation or 60 LLM
coding sessions.

All initial attempts were pinned to `47754eff72b37d84b15810dfa34929f6933b36fc`. The tested broker/core
source is `f74b9401071f0d730bf119687abf0558f73ab4d5` on `codex/mit-lit-recovery`, descended
from MIT baseline `98c59bb2dbebcf7f575993a4ad89e1300a578c83` through the independently
authored recovery work. No post-MIT upstream Lit implementation was read or used;
v1.8.0 remained black-box and was not invoked. Production source is unchanged by
this qualification. The client fixture adds configurable concurrency, admission/
poll/transport counters and bounded identical-request transport retry; unknown
provider outcomes remain permanent errors. This is not a full provenance audit.

## Admission and remaining contention

All 60 clients received admission receipts in approximately
0.68 seconds on synchronized
client wall clocks. Publication completed in approximately
393.8 seconds. These are
operational observations, not a controlled performance comparison.

The initial publication phase recorded zero retryable HTTP responses and zero
transport retries. Its 120
POSTs were exactly one initial submission and one completed replay per client;
there were no duplicate PRs. It also generated **11,937 status
polls**. Initial registration generated **531
explicitly retryable busy responses**. Durable publication admission therefore
does not remove contention from the older registration path or the cost of polling.
Additional phase-specific counters are retained in the raw client receipts.

A representative identity on each host was denied scheduler routes, peer
registration, direct GitHub ref creation and GraphQL enqueue. Broker-issued read
tokens could read the private repository. The legacy writer installations remained
suspended. All client claims were checked for owner scoping.

## Queued reassignment

Each old proposal was independently observed in GitHub's native queue before the
scheduler advanced its generation. The initial response was `draining`, without a
successor capability. All six real old capabilities then received permanent
`assignment_revoked` responses while their old PRs were still open. Native reads
after those probes confirmed the old PRs had not merged.

The broker withdrew and closed all six, then activated their successors. Both
old publication and registration remained rejected. Only after all six were retired
did the test increase build concurrency. This sequence qualifies the observed
handoffs under the original one-build setting; it is not a guarantee that a native
candidate already finishing can always be canceled. The broker's contract withholds
the successor while draining and reconciles a completed old merge if it wins that
race; reassignment does not undo an already merged commit. Observed receipt-to-retirement times were approximately 7–10 minutes, based on
synchronized observer/server journal clocks. These are operational timing
observations, not an SLA benchmark. Handoff scheduling and latency under load
still need work.

| Transfer | Retired queued PR | Merged successor |
|---|---|---|
| host-a → host-e | [#153](https://github.com/100latent/lit-merge-queue-sandbox/pull/153) | [#209](https://github.com/100latent/lit-merge-queue-sandbox/pull/209) |
| host-a → host-e | [#163](https://github.com/100latent/lit-merge-queue-sandbox/pull/163) | [#210](https://github.com/100latent/lit-merge-queue-sandbox/pull/210) |
| host-e → host-f | [#173](https://github.com/100latent/lit-merge-queue-sandbox/pull/173) | [#211](https://github.com/100latent/lit-merge-queue-sandbox/pull/211) |
| host-e → host-f | [#183](https://github.com/100latent/lit-merge-queue-sandbox/pull/183) | [#212](https://github.com/100latent/lit-merge-queue-sandbox/pull/212) |
| host-f → host-a | [#193](https://github.com/100latent/lit-merge-queue-sandbox/pull/193) | [#207](https://github.com/100latent/lit-merge-queue-sandbox/pull/207) |
| host-f → host-a | [#203](https://github.com/100latent/lit-merge-queue-sandbox/pull/203) | [#208](https://github.com/100latent/lit-merge-queue-sandbox/pull/208) |

## Two shared-file conflicts

Each pair proposed different additions to the same path. Exactly one original
proposal in each pair merged. The broker held the other; independent native
`git merge-tree --write-tree` returned an add/add conflict against the recorded
main. The scheduler reassigned the losing task to another host. The old capability
stayed rejected, and the successor remained fenced until closure/retirement.
Each fresh resolution preserved both original contributions. This was an explicit
test resolution, not an automatic semantic merge feature.

| Pair | Original winner | Retired loser | Merged resolution |
|---|---|---|---|
| one | #164 | #184 | [#213](https://github.com/100latent/lit-merge-queue-sandbox/pull/213) |
| two | #165 | #205 | [#214](https://github.com/100latent/lit-merge-queue-sandbox/pull/214) |

## Actions-only and Git verification

Required native Actions App 15368 provenance, the PR requirement, native merge
queue, ALLGREEN, one entry per merge and no bypass actors stayed enforced. Queue
build capacity alone changed temporarily from 1 to 30, after the six handoffs,
and returned to 1 before final verification. Final writable main-rule security
fields match the original. Exact frozen refs remain for audit; no manual statuses
or workflow dispatches were used.

For all 68 original heads, the latest relevant automatic PR run succeeded and its
check suite matched App 15368. Every one of the 60 landed commits matched a native
`merge_group` run and check suite at that exact SHA. Logs checked out that candidate
and passed six batch regressions plus the separate 10/50/100-worker fixtures.
Those internal fixtures are not additional live authenticated broker identities.
All proposal/candidate manual-status lists were empty.

Git cloned/fetched using a broker-issued read-only token. Symbolic HEAD was main,
HEAD and main matched `4621d34e231581da205fbd381f084d7699e745e6`, and full strict fsck passed. Each
merged proposal is reachable; all eight replaced heads are excluded. Each merge
changed only its assigned path and preserved exact expected bytes. Final file
contents, including both shared-file contributions, match the fixture contract.

## External forge-summary inconsistency

The initial verifier stopped on PR #210. Its REST `merge_commit_sha` and GraphQL
`mergeCommit` both named `8f4ffecfdfe88f3af31f9019c3c19b0ae6ae71b8`, the merge
of #209. Git's first-parent history instead contains #210's own merge,
`69c8d7af4818a7655702c20198de96a18429ca93`, with frozen proposal head
`c9a11d34d6cb0a39a19ebc70fa57aa2db4230ce8` as its second parent. The PR's
[MergedEvent](https://docs.github.com/en/graphql/reference/pulls#mergedevent)
and successful native [Actions run 44349176187](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44349176187)
agree with that Git commit. Repeated/no-cache API reads reproduced the mismatch;
adjacent PRs #209, #211 and #212 matched their own Git commits. GitHub's internal
cause is unknown; this is an observed external metadata inconsistency.

The failed assertion and responses are retained. Verification was rerun uniformly
for every merged PR using its unique first-parent Git merge, matching merge event,
exact native Actions SHA and the original parent/diff/byte criteria. No PR was
omitted and no SHA was selected merely because it had a passing run. API summary
comparisons remain a separate consistency check; that check failed for #210 and
is not relabeled green. The broker does not consume these summary SHA fields, so
production code was unchanged. Verified integration receipts should use stronger
Git/event evidence before future schedulers depend on the result.

## Native regression tests and cleanup

| Platform | Coordinator | Broker | Result |
|---|---:|---:|---|
| macOS arm64 | 41 | 32 | Passed |
| host-a Linux x86_64 | 41 | 32 | Passed |
| host-e Linux arm64 | 41 | 32 | Passed |
| host-f Linux arm64 | 41 | 32 | Passed |

These native suites include competing claims, unknown-effect/crash recovery,
queued revocation, capacity and owner fairness. They are separate from sandbox
Actions, which ran its existing Rust/workflow fixtures. Core Rust/binaries did not
change, so full Rust and binary smoke suites were not repeated. The recovery
branch's current Python source was not qualified by hosted CI in this run.

All 60 temporary principals were removed; a removed certificate on each host was
actually denied before its private keys were deleted. The original broker config
was restored. The v2 service remains enabled and healthy, with an empty native
queue and no pending/acknowledged remote effects. All admitted cohort jobs are
done. No operator forced a merge or posted a success status. Private initial claim
receipts were replaced by redacted receipts with original-record hashes. Session
record export/archival remains pending under AGENTS.md, separate from the retained
implementation qualification records.

## What remains

Prioritize reliable Git/event-verified integration receipts, fair/queued
registration, less expensive status delivery, and prompt
retirement scheduling before scaling to 100 clients. This run has 60 identities;
100-identity live operation, provider-throttling/fault scenarios at that scale,
and reassignment while many native builds are already completing remain open.
Then extend proposals to full Git objects, binaries, deletions and modes, followed
by scheduler dependencies/overlap hints and availability/credential renewal.
Broader low-priority product parity remains deferred.

[Structured results](broker-v2-burst-results-2026-11-18.json) include per-PR run URLs,
source hashes, identities, denial receipts, counters and evidence hashes. The
reusable client is [broker-burst-worker.py](broker-burst-worker.py). Raw positive
and adverse evidence, API responses, CI logs and controller scripts remain at
`<private-path>`. No private keys, tokens or claim capabilities are committed.
