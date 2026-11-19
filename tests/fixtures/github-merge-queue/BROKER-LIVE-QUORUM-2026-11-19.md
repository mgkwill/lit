# Live Lit quorum and independent management

The 100latent sandbox migrated its existing authority checkpoint to a
three-voter mTLS etcd quorum on host-a, host-c and host-d. The snapshot hash,
claims, proposals and queued-operation records matched across cutover.
Replica gateways use the same repository-scoped App. A provider turn
records its fresh GitHub token in the quorum before sending a request.

## host-c outage

The drill stopped both the host-c replica gateway and its etcd voter.
The surviving two voters retained the authority and the broker returned
the same acknowledged state. The host-c host stayed powered on for
out-of-band verification. The broker published, registered and completed
a live proposal with both affected services still offline. [PR #327](https://github.com/100latent/lit-merge-queue-sandbox/pull/327) passed the [pull-request run](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44570109653) and [merge-group run](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44571123176). Git verified the merge commit `4c055b0d11abc381e7ccea912a2de291f053e193` and its two parents. Both commits had zero manual statuses.

host-c rejoined the quorum with the same holder and epoch. A separate
isolated quorum namespace on the live cluster used the real GitHub App
to prove that a paused old writer received HTTP 401 after revocation and
the replacement writer received HTTP 200. The fixture tokens were revoked.
A separate three-voter integration test verified that loss of the
majority prevented new changes.

The Mac outage rehearsal is pending.

The original Actions-only main gate and sandbox namespace rules were
checked against their live projections. Freeze edits targeted the
test proposal ref. The source followed the
MIT-baseline policy and treated v1.8.0 and post-MIT upstream as black boxes.

All 127 broker regressions and the 100-owner stress test passed on
macOS, host-a, host-e and host-f. [Structured results](broker-live-quorum-2026-11-19.json) retain
commit and provider identities, timings and source hashes.
