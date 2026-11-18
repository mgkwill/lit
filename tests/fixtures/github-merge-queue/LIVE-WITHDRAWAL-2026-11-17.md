# Live stale-entry withdrawal, 2026-11-17

Verified a real GitHub queue withdrawal by the scoped sandbox App. The
coordinator removed PR #100's live entry, journaled exactly one observed
dequeue, and left main and the proposal head unchanged during withdrawal.
GitHub's timeline attributes the removal to
`100latent-lit-pilot-host-a[bot]`, rather than the queue's automatic remover.

## Defect found and fixed

The committed coordinator at `64d873505241bfee1f473b14138db6aee12b24b3` only aged entries
whose queue state was `UNMERGEABLE`. The live service retained this entry as
`QUEUED`, with PR mergeability `CONFLICTING` and no candidate commit, while a
valid earlier candidate occupied the single build slot. Native Git confirmed
the conflict. The original CLI waited 20 seconds with a configured one-second
stale interval and performed no withdrawal. A new public-CLI regression also
failed against that committed implementation.

Cleanup now recognizes either `UNMERGEABLE`, or `QUEUED` with an explicitly
`CONFLICTING` PR. Both require no candidate commit and the persisted stale
interval, followed by native Git conflict proof and fresh policy, main, head
and entry checks. The default interval remains 120 seconds; this controlled
live test used one second. The new regression cases also preserve a queued
entry when its conflict label is contradicted by Git, or it has a candidate.

The fixed CLI resumed the existing journal. It did not need a state reset,
manual dequeue, manual status or workflow dispatch.

The reused publisher fixture also needed two corrections: allow its existing
fixture directory, and fetch full ancestry so Git can validate a fast-forward
update to the previously used branch. The successful republish from host-a
exercised both corrections without a forced push.

## Live proof

- Entry `MQE_lQDOVxXMNs8AAAABQAdoX84ABY2lzgQXo9A` existed on GitHub
  in `QUEUED` state with no candidate and a conflicting PR.
- An active exact-ref update/deletion rule with no bypasses froze the target.
- Native Git proof digest was `2ee47b9252ad55922ad23d1fbf222f558cfcc950609ea31ab3f6672d8251f9d5`.
- Main stayed `f480055e5cbcaa13070363e02676d52d5f97ade9` and target head stayed
  `454c0f5c12d12052dca53bdfcad5850963e6ccd9` across the App's withdrawal.
- The queue entry disappeared, the PR remained open, and the journal action
  became `observed` exactly once. The App removal event is preserved.

## Fixture and admission safety

The first attempt to enqueue the already-conflicting held PR was rejected by
GitHub. A bounded metadata-refresh probe then admitted a real entry, but
GitHub removed it before the CLI could act. Those results remain archived.

To make the live case observable, App-authored PR #102 was published from
host-a and passed the native automatic PR check. It occupied the build slot
under the unchanged original main rule. The held test PR was temporarily
retargeted to an ancestor branch to refresh mergeability, restored to main,
and enqueued behind it using stale metadata. Its head never changed.
This was controlled live fault injection in the private sandbox, not a
mocked GitHub response or a claim of natural reproduction on every run.

Main's Actions-only rule stayed unchanged throughout: native Actions check
integration 15368, no bypass actors, ALLGREEN, merge commits, and one build
and merge at a time. All five original rulesets were compared before/after.
No publisher App permissions were expanded. An existing operator credential
observed policy; queue writes used a sandbox-scoped installation token.

The valid barrier subsequently merged at its exact checked SHA
`24c3c504983dd79698adb0e0f7bc6e54bb9bf2a0`. Native Git verified its two parents and single assigned
file change. Its successful merge-group run
[44243898641](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44243898641)
ran the six batch regressions and 10/50/100-worker fixtures. All checked heads
had zero manual commit statuses. The conflicting PR remains open and intact.

## Platform regression results

| Environment | Architecture | Coordinator regressions |
|---|---|---|
| Local macOS | arm64 | 23 passed |
| host-a Linux | x86_64 | 23 passed |
| host-e Linux | arm64 | 23 passed |
| host-f Linux | arm64 | 23 passed |

These are native CLI/HTTP-fixture/Git regression runs on each named platform.
The positive real-GitHub coordinator withdrawal ran locally on macOS. The
App-authored barrier publisher ran on host-a. This does not claim a separate
live withdrawal from each Linux host.

## Cleanup and remaining work

The queue is empty, the temporary freeze is removed, and all original policy
settings remain unchanged. Temporary App tokens were revoked, the downloaded
signing file removed, and host scratch source/workspaces cleaned. App
installations/public key registrations and the existing operator login remain.
v1.8.0 was used only as a black-box executable (`--version`). No v1.8.0 source
was fetched or inspected.

Remaining agent-workflow work includes scheduler task ownership and
reassignment fencing, dependency-aware scheduling, a supported resolution path for genuinely unobservable
`unknown_enqueue` outcomes, and durable credential-broker deployment. Broader
history/hosting/identity product parity remains outside this qualification.
The previously observed GitHub merge-event metadata anomalies also remain
external limitations; publication proof uses Git and exact Actions SHAs.

Full evidence is in [the structured results](live-withdrawal-results-2026-11-17.json).
Raw requests/responses, failed probes, before/after CLI results, native Git
proofs and platform logs are at `<private-path>`.
