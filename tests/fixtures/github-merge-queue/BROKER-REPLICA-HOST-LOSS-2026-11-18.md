# Replicated broker authority and independent fencing

On November 18, 2026 (Pacific time), three separate lab hosts ran the
whole-host-loss recovery path. The primary broker on host-a acknowledged an
assignment and a publication only after host-c durably stored their state.
The standby broker ran on host-b. The replica on host-c controlled provider
writes using its own mTLS actor map and active epoch.

The drill cut the watcher's connection to the host-a broker. The watcher used
no host-a SSH or source disk access for the transfer. It promoted the standby
through the independent replica. host-b verified and restored the replica's
state, received a fresh authenticated policy observation, and returned the
identical claim, capability and publication receipt in
11.90 seconds. It then published a new proposal through
the same provider gate.

The isolated host-a broker process stayed alive. After promotion it returned
HTTP 503 when asked for its authority. Its old replica client certificate
could not submit a provider write with either the old or the new epoch.
Stopping host-c separately made host-a refuse to acknowledge a new assignment.
The original state survived restart of the replica. Regression tests also
held a provider call in flight and verified the independent epoch change
waited for that submission boundary.

The provider was an isolated loopback fixture with the sandbox policy shape.
The live 100latent Actions-only main rule and its namespace and freeze rules
were unchanged. The test did not interrupt the live broker. The lab drill
simulated loss of access to host-a from the watcher without cutting power to
the shared host. Temporary services, data and credential copies were removed.

All 119 regressions and the 100-identity provider-failure stress test passed
on macOS, host-a, host-e and host-f. The implementation used MIT-derived source
and treated post-MIT upstream as a black box.

[Structured results](broker-replica-host-loss-results-2026-11-18.json) include source fingerprints and test receipts.
