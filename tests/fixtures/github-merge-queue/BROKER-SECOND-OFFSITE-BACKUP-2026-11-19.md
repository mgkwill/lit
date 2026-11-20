# Second off-voter backup host and live archive restore

host-e is the second off-voter backup host. It writes encrypted archives
and receipts to a private local ext4 vault. Its cron runs hourly at
minute 21 Pacific time. The management Mac runs at minute 51.

The host-e mTLS identity can read the broker head and backup archive.
All three replica gateways denied attempts by that identity to fetch
worker or broker credentials, submit provider operations, change
authority or retrieve a broker snapshot directly. The scheduler holds its
own TLS key and the public recovery certificate. A read-only probe used
a refused first origin and successfully obtained the same authority
through a configured alternate.

With the Mac backup and policy jobs stopped, the Linux policy observer
remained active. host-e's cron produced a live archive at epoch
10, revision 36.
An isolated Linux arm64 quorum restored that exact encrypted artifact
after independent head and digest checks. The replacement broker opened
the identical SQLite bytes over mTLS, preserving 229
claims and 199 integration receipts.
It rejected the old epoch, acknowledged a new checkpoint, and all three
replacement members returned that checkpoint. The isolated quorum had
zero provider calls. Its temporary resources and plaintext were removed.

The final standing hourly cron entry was observed again with both Mac
jobs stopped. Its independently verified archive digest matched the
restored sample. The Mac itself remained available to the operator for
out-of-band observation. Both Mac jobs returned to service afterward.

The live sandbox rules matched their original Actions-only, freeze and
namespace projections. All 139 broker regressions and the 100-owner
stress test passed on macOS, host-a, host-e and host-f. All implementation
source came from the MIT-derived branch. v1.8.0 remained a black box.

[Structured evidence](broker-second-offsite-backup-2026-11-19.json) records source and archive hashes,
role checks, timer receipts and the isolated restore.
