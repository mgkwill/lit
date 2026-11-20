# Live pinned workspaces on the task broker

On November 19, 2026 Pacific time, the 100latent sandbox accepted 100
workspace-derived PRs. Each scripted Python worker had its own mTLS identity,
an assigned Git base, and a native Lit workspace. There were 34 workers on
host-a (Linux x86-64), 33 on host-e and 33 on host-f (both Linux arm64). The
harness allowed up to 16 operations per host at once and made zero model calls.

Every GitHub PR tree and sole parent matched its corresponding workspace
export. The Git parent was `14d7325cb4cb69c11dd68d52f683be16eb7c3a55`.
The broker authored the remote commit from the workspace's committed file
changes. All 100 publication replays returned the original receipt.
The production CLI separately reproduced real workspace receipts on all
three hosts and refused a mismatched base on each host.

The quorum promoted the standby during the burst and fenced the original
broker. The operator resumed with the same claims and request IDs. The
production CLI followed the fenced primary to the standby for authenticated
claim reads on all three hosts. Worker permission probes denied scheduler
operations, registration of a peer's proposal, direct GitHub queue insertion,
and direct GitHub ref creation.

The scheduler reassigned queued [PR #357][pr357] from host-f to host-a.
The old worker's attempt to publish and register with its old capability
was denied. The broker retired that attempt before the next generation
could publish [PR #429][pr429]. The paired same-file proposals produced
a landed [PR #330][pr330] and a held [PR #341][pr341]. Git independently
reproduced the add/add conflict on the held head.

The queue required `Agent workflow candidate` from GitHub Actions App
15368, with no bypass actors. The first landed PR's exact merge commit,
merge event, successful merge-group check and absent manual statuses
were confirmed independently. Build capacity was temporarily raised from
one to 30 after the reassignment, while the required check, single-entry
merge and other branch protections were preserved.

The adapter and burst receipts tests passed on macOS, host-a, host-e and
host-f. All 136 broker, queue, failover, replica and quorum tests passed
on each of the four platforms. The live run exercised `511711a`. The
later fixture fix (`3a782b5`) preserves completed receipts when other
workers fail. All implementation inputs came from the MIT snapshot and
the recovery branch. Post-MIT upstream remained a black box.

The [structured evidence](broker-pinned-workspaces-2026-11-19.json) records
the published trees and exact source hashes. The full integration audit
and run cleanup are pending at this checkpoint.

[pr330]: https://github.com/100latent/lit-merge-queue-sandbox/pull/330
[pr341]: https://github.com/100latent/lit-merge-queue-sandbox/pull/341
[pr357]: https://github.com/100latent/lit-merge-queue-sandbox/pull/357
[pr429]: https://github.com/100latent/lit-merge-queue-sandbox/pull/429
