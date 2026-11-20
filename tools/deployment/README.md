# Operate the OpenLit broker

This guide covers the separate hosted broker, not a local `lit` installation.
Use a private sandbox when rehearsing it. The checked-in
[broker configuration](broker-config.example.json) and
[lab profile](pi-pilot-lab.example.json) are examples; replace IDs, hosts, and
private paths with your deployment's values.

## Trust and branch rules

Run the broker under a dedicated service account. Workers must not be able to
read its authority database, App key, TLS key, or configuration. Different
certificates do not isolate clients that can read each other's private keys.
Privileged host operators and GitHub administrators remain trusted.

Keep the main branch's Actions-only rule: native Actions checks, a PR, a merge
queue, and no bypass actors. Freeze each proposal ref before queue enrollment
with an exact-ref update/deletion rule that has no bypasses. Give only the broker
Integration permission to create/update/delete refs in its proposal namespace,
for example `refs/heads/agents/broker/**/*`.

Do not add a main update restriction with a broker bypass as a substitute. The
pilot found that it also blocked GitHub's own queue executor. Worker credential
separation is the tested boundary: suspend every retired worker write installation
and do not give workers another route to repository writes. The inventory must
include every retired writer.

## Credentials and service setup

The private broker App needs Contents and Pull requests write, and Actions,
Checks, Administration, and Metadata read, on the one selected repository.
It does not need workflow/status write or policy administration write. Writer
and reader tokens use separate explicit permission subsets, checked against
GitHub's returned scope. Tokens stay in memory and refresh before expiry. An
unavailable refresh returns a retryable failure; an empty writer credential
must not fall back to an operator's environment token.

Give each logical worker a distinct TLS client certificate and owner name. Use
separate scheduler and observer roles. Fingerprints are SHA-256 over certificate
DER bytes. Certificates need correct key usages, constraints, EKUs, and the
server hostname/IP in SAN. Keep private files mode 0600 in mode 0700 directories.
Keep the CA signing key with the trusted operator.

Install the reviewed `tools/` modules under `/opt/lit-task-broker`, private
configuration under `/etc/lit-task-broker`, and the supplied
[systemd unit](lit-task-broker.service) under the host's service manager. Its writable
state lives under `/var/lib/lit-task-broker`. Select an unused port. Verify a real
mTLS `/health` request and the server identity; an active service flag alone is
not a connection test.

## Policy observer

The broker App may receive ruleset responses without `bypass_actors`. Missing
information is not an empty list. A separate observer reads main, freeze, and
namespace rules and all retired-worker installation states, then sends filtered
observations over mTLS. Its GitHub credential never goes to the broker.

Run `task_broker_policy_observer.py` with `--url`, `--ca`, `--cert`, `--key`,
`--manifest`, and either `--gh` or `--github-app PRIVATE_CONFIG`. The App config
contains `repo`, `app_id`, `installation_id`, and `key`. The tested observer App
uses repository Administration write to see hidden bypass fields, and organization
Administration read to inspect installations. Keep this broad credential in a
separate trusted account, even though this client only performs reads.

The manifest names `repo`, `main_ruleset`, `freeze_ruleset`, `publication_ruleset`,
and `worker_installations`. `--once` checks one delivery; the normal feed refreshes
every five seconds and expires within 30 seconds. Use distinct observer identities
on separate hosts for redundancy. Direct GitHub reads still check visible fields;
the observer supplies only otherwise hidden policy data. Replay and stale records
are rejected.

An expired feed stops new mutations. Already admitted queue entries may finish
under GitHub's rules. This is a freshness check, not an atomic lock on GitHub
administrators. The client `--ca` trusts the server; the broker `client_ca` trusts
client leaves. Those can be different certificate authorities.

## API contract

All routes require an allowed mTLS identity. Browser-origin requests are rejected.
Bodies are limited to 64 KiB. Clients cannot select arbitrary repositories,
commands, API hosts, authority paths, keys, or credential permissions.

| Route | Role | Use |
| --- | --- | --- |
| `POST /dependencies` | Scheduler | Declare prerequisites before first assignment |
| `POST /path-hints` | Scheduler | Record likely edit paths and inspect overlap |
| `POST /assign` | Scheduler | Assign using task, expected generation, owner, base, and request ID |
| `GET /status` | Scheduler | Inspect tasks, proposals, jobs, and receipts without credentials |
| `POST /recover-removed` | Scheduler | Consume one verified native removal event |
| `POST /reconcile-publication` | Scheduler | Recover a receipt after a lost write response |
| `POST /policy` | Observer | Deliver fresh policy and retired-writer observations |
| `GET /claims` | Worker | Read its own assignments and active capabilities |
| `GET /repository-token` | Worker | Obtain read-only access to the one repository |
| `POST /propose` | Worker | Submit edits bound to its active capability |
| `GET /publication?claim=ID` | Worker | Poll its publication |
| `POST /register` | Worker | Register an immutable proposal receipt |
| `GET /registration?claim=ID` | Worker | Poll its registration |
| `GET /health` | Allowed identities | Check listener and latest controller state |

Use `task_broker_client.py --url URL --cert CERT --key KEY --ca CA ROUTE`.
POST bodies go on stdin. Claims, assignment, and repository-token responses can
contain credentials; consume them privately. API version 3 is reported by health.
The client waits for accepted jobs. `--no-wait` returns the admission receipt;
`--claim ID publication` or `--claim ID registration` resumes polling.
`--wait-seconds` limits waiting without canceling accepted work.

Publication and registration return HTTP 202 when admitted, with an operation,
state, same-origin status path, and polling delay. HTTP 200 returns the final
receipt. Poll after acceptance instead of resubmitting with a new request ID.
Identical retries join the existing job, even at capacity. Changed requests for
one attempt are rejected. Registration takes `proposal` and `capability` fields.
A registered binding and completed registration job commit together.

Pending admission defaults to 128 jobs total and four per owner, including
running jobs. Positive `admission_limit` and `admission_per_owner` values can be
set up to 1024. Registration has separate limits. Owners take turns, with FIFO
order within each owner. The cursor survives restart. Registration yields after
up to eight attempts; native queue reconciliation handles up to four proposals
per turn. These limits bound turns, not provider response time.

Honor `Retry-After` for explicitly retryable 429/503 errors. Do not treat every
409 as busy. Revoked assignments, invalid capabilities, changed publication
requests, and unknown publication outcomes are not safe automatic retries.
Queued jobs survive restart. A running job without a durable receipt becomes
uncertain instead of being repeated. A failed precondition before publication
intent can leave a job queued for a paced retry. Polling alone does not start work.

## Edits, dependencies, and integration

A proposal contains task, generation, claim, base, capability, request, and files.
It allows at most 32 edits within the 64 KiB body; encoded base64 counts toward
the limit.

| Edit | Example file entry |
| --- | --- |
| Text | `{"path":"src/a.txt","content":"new text\n"}` |
| Binary | `{"path":"assets/a.bin","content":"AP8=","encoding":"base64"}` |
| Delete | `{"path":"src/old.txt","delete":true}` |
| Mode only | `{"path":"bin/run","mode":"100755"}` |

Content defaults to UTF-8 and mode 100644. Use canonical padded base64 for bytes;
set 100755 when a replacement must stay executable. Mode-only changes preserve
the base blob. Deletions and mode-only edits need an existing regular base file.
Each path occurs once. Traversal, overlapping paths, directory replacement,
Git metadata, and workflow changes are rejected. Symlinks, submodules, and Git
bundles are outside this publication contract.

The broker records intent before provider writes. Its App authors the PR; the
receipt binds it to the authenticated worker. The scheduler uses
`task_broker_freeze_proposal.py` to verify the receipt and freeze exactly that
ref. Registration then waits for the newest relevant native Actions preflight.
An older success cannot hide a newer pending run on the same SHA.

Declare prerequisites with `/dependencies` before assignment. Plans are immutable,
allow up to 32 distinct prerequisite IDs, and reject cycles. Use a new task ID to
change a plan. Assignment needs verified integration receipts for the current
required generations and a base containing every required merge. A blocked task
gets no capability. An identical granted assignment replays its original result.

`/path-hints` accepts up to 32 repository-relative files or directories; an empty
list clears it. Directory paths match descendants, and paths are case-sensitive.
Hints are advisory. Ownership and the actual candidate checks remain authoritative.
Read [integration receipts](../README.md#integration-receipts) before using
`phase: merged` as a downstream dependency signal.

## Reassignment and uncertain outcomes

Reassignment revokes the old generation. If it has a bound PR, the successor
waits until the old PR is observed closed/merged with no queue entry. An in-flight
provider request can delay this. A completed merge cannot be undone by reassignment.

For a lost publication response, the scheduler can send `{"claim":"CLAIM_ID"}`
to `/reconcile-publication`. The broker performs reads and recovers a receipt only
when the assignment, recorded commit, exact ref, broker author, and unique open
PR into main match. Missing or mismatched evidence leaves the request uncertain.
The route does not repeat a write. Orphan cleanup remains an operator task.

For a confirmed native `branch_protection_failure`, `/recover-removed` takes the
PR number and removal-event node ID. The broker verifies the latest event, GitHub
actor, reason, ownership, immutable identity, rules, and absence of an entry or
pending/unknown RPC. It consumes the event once. Entry absence by itself is not
enough to reset admission.

Restart against the same state directory. Policy freshness resets, so an observer
must reconnect. Do not discard a database to clear unknown outcomes. The special
`--cutover-state` migration is only for the earlier policy pilot: stop first and
preserve its private backup. Normal startup refuses mismatched bindings.

## Certificates and manual handoff

For a client renewal, `task_broker_renew_client.py` prepares an overlap config
from `--config`, `--old-cert`, `--new-cert`, and `--output`. It verifies the new
certificate against the CA and requires a later expiry. Install the overlap
config, verify the new identity live, then use its `retire` phase to remove the
old fingerprint. Verify the old identity is denied. Keep the previous private
configuration for rollback. Already issued read tokens last until expiry or
revocation; reassignment separately revokes publication authority.

For a manual single-broker handoff, stop the active service and verify its process
has exited. Its lifetime `broker-service.lock` excludes another writer or checkpoint
in the same directory, but does not prove cross-host exclusivity.

Export with `task_broker_checkpoint.py --config CONFIG export CHECKPOINT.sqlite`.
Transfer it privately, then use `restore CHECKPOINT.sqlite --sha256 SHA` with the
standby's config. Restore requires an unused database path and matching repository
and rules. Keep TLS and App credentials separate from the snapshot. Fresh policy
observations are required before writes. Fail back with the standby's newer state,
not the original checkpoint.

## Automated failover

The host adapter and `task_broker_failover.py watch --config FAILOVER.json` can
manage a stopped standby. Configure pinned SSH, units, URLs, private journal,
and mTLS files. The default is three consecutive failures at five-second intervals.
Before checkpointing, the watcher must verify the source service is stopped and
exercise a durable start fence. Unknown source state or missing management access
blocks takeover. A completely unreachable primary needs independent fencing and
replicated state; the local-checkpoint mode cannot prove it safe.

Clients can specify `--standby-url`. Reads and admitted-operation polls can move
to the standby. An unacknowledged POST changes endpoint only when connection
refusal or DNS failure proves it was not sent. TLS errors and ambiguous writes
are not replayed. Observers can send the same observation to both endpoints.

Keep the source fence until the returning host has the authoritative newer state.
Use a fresh watcher journal after a completed handoff. Do not run two active
brokers by copying a snapshot and starting both.

## Replica and quorum modes

A separate replica service stores broker checkpoints and holds the GitHub writer
key. Brokers replicate intents before sending mutations and accepted state before
acknowledging it. Promotion changes an epoch; old brokers cannot write under the
new epoch. Unknown external effects remain in the replicated journal.

Before a replica cutover, drain old writers, remove their App signing keys, and
revoke old writer installation tokens. If a token cannot be recovered, stop all
legacy minting and wait through the maximum remaining JWT lifetime plus the
one-hour token lifetime. Suspending then resuming an installation can restore
old token access; suspension alone is not permanent revocation.

A single replica is still one failure domain. Quorum mode uses three etcd voters
on separate hosts with durable storage and mutual TLS. Protect the client keys
and database, which can contain short-lived writer tokens. Configure the replica
`quorum` URLs, CA, certificate, and key; clients list alternate replica origins.
Use at least a 16 MiB etcd request limit. Checkpoints are limited to 128 MiB
uncompressed and 8 MiB compressed. Monitor quota and compact old revisions.

Each provider send reserves a fresh writer token in a transaction conditioned on
epoch and revision. Promotion revokes an outstanding token and requires HTTP 401
before advancing the epoch. A surviving gateway can fence an abandoned same-actor
turn after 75 seconds. Losing the majority refuses new work. Supervise watchers
and observers on independent hosts; a standby-local watcher can use the local
host adapter. Verify fresh policy at both endpoints before removing a management
machine from the path.

## Back up and restore quorum authority

Backups contain claim authority. Keep them encrypted off the voter hosts. A capture
reads the head, compressed checkpoint, and writer presence together. It reports
recovery blockers without exporting a writer token.

Schedule `task_broker_quorum_backup_job.py --config PRIVATE_CONFIG` on an off-voter
host. Configure manifest, pinned cluster ID, private vault, public recovery
recipient certificate, and absolute OpenSSL path. A direct client also configures
quorum and manager. A second host can instead use `gateway` with a distinct mTLS
`backup` identity that can read only `/head` and `/backup`; it needs no etcd or
provider credential. Keep the decryption key under separate operator custody.

Each successful scheduled run publishes `checkpoint.cms` and `receipt.json`
together, with digests, observed head, and blockers. Failed runs do not publish a
partial artifact. Verify the actual supervisor execution, receipt freshness, and
a restore drill. A schedule file alone is not backup proof.

For manual capture, use `task_broker_quorum_backup.py --config CONFIG backup FILE`
and `inspect FILE`. Seal portable archives using OpenSSL CMS AES-256-GCM to the
recovery certificate. Decrypt into private staging and verify the original digest.
Keep the latest acknowledged head independently of the backup.

After permanent majority loss, fence old brokers and gateways. Start a new
three-voter cluster with different origins, peer membership, and TLS credentials.
Keep publication offline. Restore with the replacement config, archive,
`--expected-head`, `--sha256`, `--target-cluster-id`, and `--holder`.
If the independent watermark is missing, the archive is behind, or provider
outcomes remain unresolved, reconcile before activation. Do not restore just
because the archive parses.

Restore preserves checkpoint revision and advances the epoch. Exact retries are
accepted against the seeded state. Check that all new gateways report the same
head, start the selected broker with an empty private cache, reconnect observers,
verify the original Actions-only policy, and rearm the watcher. Other etcd
keyspaces need their own backup process.

For rollback, preserve the database and frozen refs and keep worker writers
suspended. Drain registration before reverting to a pre-v3 broker, and drain or
reconcile publication jobs before reverting to pre-v2. Older versions cannot
process those queues; preserve unknown outcomes rather than deleting them.

## Run Pi cohorts within available inference capacity

Provision Pi, Node, certificates, the trusted client, and the MIT-derived OpenLit
binary on workers. The private state directory contains `plan.json`, `lab.json`,
`principals.json`, and the original policy snapshot. Run
`python3 tools/pi_pilot.py --state PRIVATE_STATE` again to resume.

The plan pins the repository, broker, Git base, initial agents, overlap pairs,
reassignments, and temporary queue build capacity. `publication_window` bounds
concurrent frozen refs; keep related overlap/handoff pairs in one window. The
lab profile defaults to four concurrent host commands and eight admitted coding
sessions. Set `limits.inference` to the model capacity you actually have. Our
lab inference stays on host-a; workers can run on host-a, host-e, and host-f.

The journal pins the plan, lab profile, and adapter sources. Resume at the same
source revision. Source workloads use a bounded Python checker; Rust-regression
workloads accept assigned insertions at pinned anchors and audit the final tree.
These are test workloads, not a claim of arbitrary-code correctness.

For controller handoff, configure named controllers, separate scheduler identities,
a standby checkout at the same revision, and a private checkpoint store.
`pi_pilot_control.py checkpoint` saves a pinned checkpoint. Confirm the outgoing
controller stopped before `takeover`: scheduler credential changes and GitHub
rule updates are separate services. Takeover verifies state and replaces only
the configured scheduler identities; it restarts the sandbox broker to load them.
Use one designated standby and reconcile in-flight effects first.

The 100-attempt regression uses simulated workers and two coding slots. Smaller
live Pi runs provide separate inference and GitHub proof. Interruption options
exit the disposable controller at selected points; they are not instructions to
power off lab machines. See [testing](../../TESTING.md) and the historical reports
in `tests/fixtures/github-merge-queue/`.
