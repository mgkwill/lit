# Scheduled encrypted quorum backups and recovery drill

The production backup runs from the off-voter management Mac under
launchd every hour at minute 51 Pacific time. It reads the current
quorum and independently checks the authenticated broker head before and
after capture. It verifies the SQLite checkpoint in memory and seals it
with AES-256-GCM using the recovery certificate. Output consists of an
encrypted archive and its receipt, published together in the private
vault. The job configuration contains the public recovery certificate
and leaves the decryption key under operator custody.

The initial timed capture observed an active writer reservation and
recorded that blocker. The job now retries transient writer reservations.
Launchd ran the updated job on its calendar schedule and reported exit
code 0. It sealed live epoch 10, revision 36, with no
reported blockers. A separate verification decrypted the archive,
matched both digests and validated the captured authority head.

The physical lab used host-a, host-c and host-d for separate original and
replacement clusters. A test launchd timer on the Mac was observed
before and after its first scheduled invocation. It produced the only
archive of the latest checkpoint before the loss. The original host-a and
host-c voter processes and their disposable data were removed. The
original host-d voter stayed online and refused linearizable reads,
checkpoints and provider work.

The operator decrypted the same scheduled ciphertext and seeded a
new quorum. The restored broker recovered the latest acknowledged
assignment and fixture integration receipt over mTLS, fenced the stale
epoch and acknowledged a fresh checkpoint. The drill took 12.85
seconds from voter removal to the verified new checkpoint with
replacement voters already running. The replacement quorum accepted
more work while one voter was stopped and converged after its return.
All disposable lab resources were removed.

The live Actions-only, namespace and freeze rules matched their verified
projections. The rehearsal used a separate fixture provider and voters.
The implementation stayed on the MIT-derived branch and treated
post-MIT upstream, including v1.8.0, as a black box.

All 137 broker regressions and the 100-owner stress test passed on macOS.
All 15 quorum regressions passed on Linux arm64.
[Structured evidence](broker-scheduled-quorum-backup-2026-11-19.json) records the timings, hashes and safety checks.
