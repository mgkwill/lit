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
and direct GitHub ref creation. The controller also resumed from recorded
receipts after a native check wait expired and a status connection dropped.

The scheduler reassigned queued [PR #357][pr357] from host-f to host-a.
The old worker's attempt to publish and register with its old capability
was denied. The broker retired that attempt before the next generation
could publish [PR #429][pr429]. The paired same-file proposals produced
a landed [PR #330][pr330] and a held [PR #341][pr341]. Git independently
reproduced the add/add conflict on the held head. The broker then published
[PR #430][pr430] from a fresh pin, and Git confirmed that it preserved both
contributions. Both retired attempts closed without merging.

The native queue merged 100 proposals. The independent audit matched each
first-parent Git merge to an event and a successful Actions check on that
exact merge SHA. The queue required `Agent workflow candidate` from GitHub
Actions App 15368, with no bypass actors. All 100 proposal heads and merge
commits had zero manual statuses. `git fsck --full --strict` passed, and
all 99 expected final paths matched byte for byte. Final sandbox `main` was
`0c395bc2447b59f0a3223c4bf17c71a45d7a21a6`.

Three PRs lacked a `MergedEvent`. GitHub supplied a native removal event
with the exact merge SHA and a queue-bot closure that pointed to its first
parent. The tested coordinator change (`02ef381`) accepted these receipts
only after checking the paired events, their Git parents, and the exact
Actions runs. The live standby backfilled all three after deployment.
[PR #351][pr351] · [PR #400][pr400] · [PR #423][pr423]

Build capacity was temporarily raised from one to 30 after the
reassignment. The required Actions check, single-entry merge, and other
branch protections applied throughout. The first cleanup found that the
coordinator still required exact freeze entries for settled proposals.
The deployed fix (`858a331`) retains the freeze until there is a verified
integration receipt or an observed unmerged retirement. The second cleanup
restored the original build capacity and ruleset configuration. All 100
temporary identities and host keys were removed, and a retired credential
was denied on each worker host.

The adapter and burst receipts tests passed on macOS, host-a, host-e and
host-f. All 144 final broker, queue, failover, replica and quorum tests
passed on each of the four platforms. The live workspace run exercised
`511711a`. The fixture fix (`3a782b5`) preserves completed receipts when
other workers fail.

The follow-up (`46fa531`) stages named tracked deletions for workspace
publication. The v1.8.0 reference executable and the prior recovery binary
both refused this operation. Rust release suites passed on macOS and Linux
x86-64 and in a Linux arm64 container. The native mixed workspace regression
passed on all three workers. All implementation inputs came from the MIT
snapshot and the recovery branch. Post-MIT upstream remained a black box.

The structured evidence records the published trees, integration receipts,
cleanup and exact source hashes.
[Structured results](broker-pinned-workspaces-2026-11-19.json)

[pr330]: https://github.com/100latent/lit-merge-queue-sandbox/pull/330
[pr341]: https://github.com/100latent/lit-merge-queue-sandbox/pull/341
[pr351]: https://github.com/100latent/lit-merge-queue-sandbox/pull/351
[pr357]: https://github.com/100latent/lit-merge-queue-sandbox/pull/357
[pr400]: https://github.com/100latent/lit-merge-queue-sandbox/pull/400
[pr423]: https://github.com/100latent/lit-merge-queue-sandbox/pull/423
[pr429]: https://github.com/100latent/lit-merge-queue-sandbox/pull/429
[pr430]: https://github.com/100latent/lit-merge-queue-sandbox/pull/430
