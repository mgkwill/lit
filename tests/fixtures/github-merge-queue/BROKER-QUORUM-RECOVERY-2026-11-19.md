# Quorum recovery after permanent majority loss

On November 19, 2026, a Linux arm64 Colima lab ran separate original and
replacement three-voter etcd clusters with independent mTLS trust,
membership and storage. SSH to the named hosts timed out, so the drill
used six voter containers on isolated internal networks.

The original quorum acknowledged a checkpoint at revision 6 after an
earlier archive at revision 5. A separate authenticated head read recorded
the latest acknowledged revision and digest. The latest archive was
sealed with AES-256-GCM outside the voter networks, and the working copy
was removed. Two original voter containers and their data volumes were
then removed. The remaining voter stayed online and refused linearizable
reads, checkpoints and provider work.

The operator rejected tampered ciphertext, decrypted the intact copy,
and restored the original checkpoint into a new quorum. Restore refused
the older archive, an incorrect digest and an incorrect cluster ID. The
new authority kept revision 6, advanced the epoch from 12 to 13, and
returned the same result on an exact retry.

The broker restored its cache through the replacement gateway over mTLS.
The latest acknowledged assignment, active capabilities, a revoked
claim and a fixture integration receipt matched the saved state. The old
epoch could not request provider work. The recovery took 20.30
seconds from two-voter removal to a verified new broker checkpoint, with
the replacement voters already running.

With one replacement voter stopped, the new quorum accepted another
checkpoint. All three returned revision 8 and epoch 13 after the voter
returned. The disposable lab containers, volumes and networks were
removed.

The fixture ran without provider access. A read-only check against the
live 100latent sandbox confirmed that all three rulesets matched their
last verified projections, including the original Actions-only gate.
The live quorum did not participate in the drill. The implementation
used MIT-derived code and treated post-MIT upstream, including v1.8.0,
as a black box.

## Physical lab follow-up

After SSH returned, the drill repeated with original and replacement
voters on host-a, host-c and host-d. Each voter used private fixture data on
its host. The two clusters used separate TLS roots and dedicated ports.
Loss of the original host-a and host-c voters included deletion of their
fixture data directories. The original host-d voter stayed online and
refused reads, checkpoints and provider work.

The replacement quorum restored the checkpoint from an authenticated
encrypted copy held outside the voter hosts. The broker verified the
checkpoint and acknowledged a new revision over mTLS in 10.86
seconds from voter removal, with replacement voters already running.
The replacement also wrote a checkpoint while host-c's replacement voter
was stopped. All three replacement voters returned the final revision
after it restarted. The disposable processes, data and listeners were
removed.

## Read-only live backup

The operator captured a live archive at epoch 10, revision
36. Independent authenticated head reads before and after
capture matched the archive. There was no active writer reservation or
recovery blocker at capture. An encrypted copy was written to the
operator's private off-voter vault and decrypted back to the verified
archive digest. The live Actions-only, freeze and namespace rulesets
matched their recorded projections after the drill.

All 133 broker regressions and the 100-owner stress test passed on macOS.
All 11 quorum regressions passed in the Linux arm64 container.
[Structured evidence](broker-quorum-recovery-2026-11-19.json) includes the source revision, artifact
hashes and verification results.
