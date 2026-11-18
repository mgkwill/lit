# Durable hosted queue coordinator, 2026-11-17

Implemented [the coordinator](../../../tools/github_queue_coordinator.py) and
[its operating contract](../../../tools/README.md) on `codex/mit-lit-recovery`.
The final qualification used unchanged coordinator source, three publisher
Apps on host-a/host-e/host-f, and 30 real PRs (#72–#101) in
`100latent/lit-merge-queue-sandbox`.

## Final qualification

All 27 disjoint proposals and one shared-file proposal landed. Two competing
shared-file proposals remain open and intact with native Git conflicts.
All 30 automatic PR checks and 28 successful merge-group checks passed.
Every landed proposal and final file was independently verified with Git;
each integration commit matched its successful Actions check and checkout SHA.
No manual statuses, workflow dispatches, or queue cleanup were used.

Observed peak queue depth was 30, with 28 simultaneous merge-group runs.
The supervisor killed the coordinator with all 30 PRs queued, repeated the
same registration and restarted against the same journal. It then revoked
the App writer token. GitHub returned HTTP 401 and the coordinator stopped;
the observer credential did not replace the writer. A fresh writer resumed
against the same state. Exactly 30 admission intents remained throughout.

Each successful PR check ran the existing six Rust batch regressions and
10-worker fixture. Each successful queue check ran those regressions and the
10/50/100-worker fixtures. The coordinator's own 20 public-CLI integration
tests passed on macOS and both native Linux arm64 lab hosts, host-e and host-f.
New Python CI steps are configured in this commit; those newly configured
jobs have not yet run on GitHub at this commit. The sandbox's existing gate
was preserved; the new coordinator ran on the operator's macOS host.

## Authorization and safety

Proposal identity includes repository, PR number, exact head, branch and typed
App author. Registration is idempotent and refuses changed attempts. A local
OS lock serializes coordinator writers. SQLite commits mutation intent before
network effects; restart reconciles accepted effects instead of resubmitting.

Because GitHub dequeue has no expected-head argument, enrolled refs were
frozen with an exact-ref update/deletion ruleset and no bypass actors.
All three Apps were denied real fast-forward updates to their own frozen
branches. All nine cross-App/main probes were also rejected without changing
refs. Cleanup additionally requires persisted stale state, no candidate,
native Git conflict proof, and refreshed policy/head/queue observations.

Publisher App permissions stayed sandbox-only Contents/Pull Requests write
plus Metadata read. GitHub omits bypass actor details from these App tokens,
so an existing operator credential observed policy and check provenance.
Queue mutations and Git fetches used the short-lived App writer. Its scope
and validity were checked every cycle. No permissions were added to the Apps.

Main retained the required `Agent workflow candidate` check from GitHub
Actions integration 15368, no bypass actors, ALLGREEN and one entry per merge.
Temporary build capacity 30 was restored to 1. All five original rulesets
were restored and read back; the temporary freeze was removed. Publisher and
coordinator tokens were revoked, host scratch sources/worktrees removed,
and all three downloaded signing files removed. The App installations and
registered public keys remain. v1.8.0 was used only as a black-box executable
(`--version`); its implementation was never read or fetched.

Final main is `f480055e5cbcaa13070363e02676d52d5f97ade9`.

## Stale withdrawal coverage

GitHub removed both conflicting entries before a coordinator withdrawal was
needed. The final hosted run therefore qualifies unattended cohort processing,
but did not reproduce a retained stale entry. The withdrawal path is covered
by public-CLI integration tests with stateful HTTP behavior and native Git,
including unknown PR mergeability and a lost withdrawal reply. A positive
hosted stale-withdrawal receipt remains unqualified for this final cohort.

## Development findings retained

The first development cohort used PRs #42–#71. It exposed the REST/GraphQL Bot
login difference and a coordinator bug that treated absent queue entries as
terminal removal before GitHub reported a merge. Both were fixed. The same
journal subsequently reconciled all 28 merges and two conflicts without
additional admissions. Its original failed observations and intermediate
source evidence remain archived. This development run is not substituted
for the separate unchanged-source final qualification above.

A transient GitHub HTTP 502 interrupted the original test supervisor. Safe
observation retries were added to the supervisor, and the coordinator watch
now retries transient observation failures while preserving pending intents.
Revoked credentials and policy violations still stop processing.

The development API also reported PR #64 through a closed event associated
with the preceding commit, while Git contained its correct two-parent merge
and the exact successful candidate check. The strict timeline equality check
failed; that adverse evidence is retained. Actual Git history and Actions
candidate SHAs are the publication evidence, rather than a PR summary SHA.

The final cohort retained one API merge-event metadata anomaly, for PR #73.
Its exact response is included in the results. All 28 Git/Actions SHA
matches and file-content checks passed independently; full API merge-event
metadata fidelity is not qualified.

## Remaining boundaries

An accepted entry that disappears waits for a definitive PR outcome. An enqueue
whose outcome is genuinely unobservable remains `unknown_enqueue` and is never
blindly retried. This fail-closed state needs remote-history reconciliation;
there is no claim of exactly-once external effects under arbitrary failures.

This tool coordinates fixed immutable attempts. Task reassignment, scheduler
ownership fencing, independent credential issuance, and distributed controller
leadership remain separate work. Complete Rust regression and native binary
smoke qualification were not repeated for this Python/CI change.

The [machine-readable evidence](coordinator-results-2026-11-17.json) includes
all proposal/check/merge SHAs, controlled restarts and denied writes. Raw
responses, job logs and the durable journal are in `<private-path>`.

| PR | Host | Edit | Outcome | Checked integration |
|---|---|---|---|---|
| [#72](https://github.com/100latent/lit-merge-queue-sandbox/pull/72) | host-a | disjoint | Merged | [612bc8fb5033](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229865851) |
| [#73](https://github.com/100latent/lit-merge-queue-sandbox/pull/73) | host-e | disjoint | Merged | [98d06b0d8220](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229867731) |
| [#74](https://github.com/100latent/lit-merge-queue-sandbox/pull/74) | host-a | disjoint | Merged | [3777b1982ff5](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229868875) |
| [#75](https://github.com/100latent/lit-merge-queue-sandbox/pull/75) | host-e | disjoint | Merged | [aa73878eb874](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229870879) |
| [#76](https://github.com/100latent/lit-merge-queue-sandbox/pull/76) | host-e | disjoint | Merged | [cd704eae9341](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229872601) |
| [#77](https://github.com/100latent/lit-merge-queue-sandbox/pull/77) | host-f | disjoint | Merged | [6b58ef869ee8](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229912528) |
| [#78](https://github.com/100latent/lit-merge-queue-sandbox/pull/78) | host-a | disjoint | Merged | [a18ef9f889be](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229915694) |
| [#79](https://github.com/100latent/lit-merge-queue-sandbox/pull/79) | host-f | disjoint | Merged | [40f049962964](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229916662) |
| [#80](https://github.com/100latent/lit-merge-queue-sandbox/pull/80) | host-f | disjoint | Merged | [90b5b5f4c37c](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229917934) |
| [#81](https://github.com/100latent/lit-merge-queue-sandbox/pull/81) | host-a | disjoint | Merged | [6bada6c4125c](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229919684) |
| [#82](https://github.com/100latent/lit-merge-queue-sandbox/pull/82) | host-e | disjoint | Merged | [b669c423346c](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229958410) |
| [#83](https://github.com/100latent/lit-merge-queue-sandbox/pull/83) | host-a | disjoint | Merged | [9e16b0a94b12](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229959525) |
| [#84](https://github.com/100latent/lit-merge-queue-sandbox/pull/84) | host-e | disjoint | Merged | [f8644c9fdabf](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229961591) |
| [#85](https://github.com/100latent/lit-merge-queue-sandbox/pull/85) | host-a | disjoint | Merged | [7bf9da6f2ac0](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229962936) |
| [#86](https://github.com/100latent/lit-merge-queue-sandbox/pull/86) | host-f | disjoint | Merged | [ce3c747923c5](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229964376) |
| [#87](https://github.com/100latent/lit-merge-queue-sandbox/pull/87) | host-f | disjoint | Merged | [402c87bbfae4](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229966226) |
| [#88](https://github.com/100latent/lit-merge-queue-sandbox/pull/88) | host-e | disjoint | Merged | [f579abf04595](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44229967595) |
| [#89](https://github.com/100latent/lit-merge-queue-sandbox/pull/89) | host-f | disjoint | Merged | [1e647f0afd0d](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44230002877) |
| [#90](https://github.com/100latent/lit-merge-queue-sandbox/pull/90) | host-a | disjoint | Merged | [d6515d500af9](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44230004444) |
| [#91](https://github.com/100latent/lit-merge-queue-sandbox/pull/91) | host-e | disjoint | Merged | [760108768aa2](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44230006079) |
| [#92](https://github.com/100latent/lit-merge-queue-sandbox/pull/92) | host-a | disjoint | Merged | [dd88aec2c6db](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44230007321) |
| [#93](https://github.com/100latent/lit-merge-queue-sandbox/pull/93) | host-e | disjoint | Merged | [674d1352e408](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44230008712) |
| [#94](https://github.com/100latent/lit-merge-queue-sandbox/pull/94) | host-a | disjoint | Merged | [4a795efb1190](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44230010432) |
| [#95](https://github.com/100latent/lit-merge-queue-sandbox/pull/95) | host-f | disjoint | Merged | [59f5afc9f781](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44230043645) |
| [#96](https://github.com/100latent/lit-merge-queue-sandbox/pull/96) | host-f | disjoint | Merged | [3839823c9660](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44230045305) |
| [#97](https://github.com/100latent/lit-merge-queue-sandbox/pull/97) | host-f | disjoint | Merged | [c666fd3b9336](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44230046307) |
| [#98](https://github.com/100latent/lit-merge-queue-sandbox/pull/98) | host-e | disjoint | Merged | [700379a3f9c3](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44230047885) |
| [#99](https://github.com/100latent/lit-merge-queue-sandbox/pull/99) | host-e | shared | Merged | [f480055e5cbc](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44230049925) |
| [#100](https://github.com/100latent/lit-merge-queue-sandbox/pull/100) | host-f | shared | Held conflict | — |
| [#101](https://github.com/100latent/lit-merge-queue-sandbox/pull/101) | host-a | shared | Held conflict | — |
