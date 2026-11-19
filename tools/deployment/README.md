# Authenticated broker deployment

Workers authenticate to `task_broker.py` using individual TLS client certificates.
Only the separate broker App receives repository write credentials. Workers can
obtain repository-read installation tokens and submit proposals through the
broker, but never receive a GitHub write token or signing key. Suspend all legacy
worker App installations that could write directly to the sandbox before enabling
this deployment. Do not give workers other repository-write credentials.

The original Actions-only main rule stays unchanged: no bypass actors, native
Actions provenance, PR and merge-queue requirements. Do not add a main `update`
restriction with a broker bypass: the live pilot found that this also rejects
GitHub's native queue executor after successful checks. An attempted bot-user
exemption did not solve it. Credential separation is the qualified enforcement
boundary; a client-side check or a nonfunctional restriction is not a substitute.

Privileged host operators, GitHub policy administrators and other trusted
organization automations remain outside the worker threat model. The deployment
cannot prevent an administrator from granting a worker new write credentials.
The configured retired-writer inventory must cover every previous worker writer.
New workers need a TLS identity, not another GitHub App.

## Service and credentials

Run the broker under a dedicated, non-login OS account. Keep authority, App key,
server key and configuration inaccessible to workers. The supplied systemd unit
makes code/config read-only and permits writes only under `/var/lib/lit-task-broker`.
A worker with administrator/passwordless-sudo rights on that host is outside this
isolation boundary. The co-hosted pilot worker uses a different, unprivileged UID.

Create a private, repository-selected broker App with Contents and Pull requests
write; Actions, Checks, Administration and Metadata read. Install it on only the
sandbox. It needs neither policy administration write, workflow/status write,
nor access to other repositories. Keep its signing key on the broker host. Write
and read tokens are issued with separate explicit permission subsets, checked
against GitHub's returned scope, cached in memory and refreshed before expiry.
Concurrent requests for the same token share one refresh. A failed refresh
withholds credentials and pauses further refresh attempts for two seconds.
The reader route returns retryable HTTP 503 while the credential is unavailable.
An empty explicit writer credential never falls back to an operator environment.

Create a namespace rule targeting `refs/heads/agents/broker/**/*` with creation,
update and deletion restrictions and only the broker Integration exempt. Exclude
that namespace from a catch-all creation restriction if necessary. This is a
proposal namespace, not a main bypass. Proposal refs must also be frozen by an
active exact-ref update/deletion rule with no bypasses before queue enrollment.

Use the example configuration with real IDs and fingerprints. Fingerprints cover
certificate **DER** bytes, not PEM file bytes. Give each worker a distinct client
certificate and logical owner; multiple agents sharing an owner also share claim
visibility, so use unique owners for independent agents. The scheduler and policy
observer have separate roles and certificates. Certificates require correct key
usage, authority key identifiers, basic constraints and client/server EKUs. Include
the actual server hostname/IP in SAN. Store private files as 0600 in 0700 private
directories; code belongs to the trusted deployment operator.

Select an unused port. Install the Python files from `tools/` in
`/opt/lit-task-broker`, private configuration in `/etc/lit-task-broker`, and the unit
in `/etc/systemd/system`, then enable it. Verify actual mTLS `/health`, the server
identity and its journal; `systemctl is-active` alone is insufficient.

## Read-only policy observer

GitHub omitted bypass actors from the broker App's ruleset responses even with
Administration read. Never interpret omission as an empty list. The observer
reads the configured main/freeze/namespace rules and the configured worker
installation states, then sends filtered metadata over mTLS. GitHub credentials
stay in the observer process and are never sent to the broker.

Run `task_broker_policy_observer.py` with `--url`, `--ca`, `--cert`, `--key`,
`--manifest` and either the `--gh` path or `--github-app PRIVATE_CONFIG`.
The App configuration contains `repo`, `app_id`, `installation_id` and `key`.
Use a dedicated App installed only on the sandbox with repository Administration
write and organization Administration read. The former is required to see
`bypass_actors`. Organization Administration read lets the observer verify
installations across the organization. Run its read-only API client under a separate trusted
service account. The public observer manifest contains
`repo`, `main_ruleset`, `freeze_ruleset`, `publication_ruleset` and
`worker_installations` from service configuration. `--once` checks one delivery;
otherwise it refreshes every five seconds. Give redundant observers their own
mTLS identities and supervise them on separate hosts. The feed expires within
30 seconds, rejects stale/replayed records,
and must show every retired worker App still suspended. Visible policy fields
are compared against fresh direct GitHub reads; authenticated observations fill
only hidden bypass data. Freshness and revocation are checked at mutations.

Loss of the feed stops new mutations. Already admitted queue entries remain under
GitHub's original native gate and may complete independently. Policy administrators
remain trusted not to weaken protections or reactivate worker writers during an
operation. This is bounded observation, not an atomic GitHub policy lock.

## API and publication

All routes require mTLS and a listed certificate. JSON bodies are limited to
64 KiB, with at most 32 proposed files. Browser-origin requests are refused. There
is no generic GitHub proxy or worker-selected repository, server command, API host,
authority path, signing key or credential permission request.

| Route | Role | Contract |
|---|---|---|
| `POST /dependencies` | scheduler | Declare a task's prerequisites before its first assignment |
| `POST /path-hints` | scheduler | Record a task's likely edit paths and check for overlapping assignments |
| `POST /assign` | scheduler | CAS task assignment: task, expect, owner, base, request |
| `GET /status` | scheduler | Claim, dependency, proposal, action, publication and registration metadata, without credentials |
| `POST /recover-removed` | scheduler | Consume one verified native removal receipt; never retry unknown RPCs |
| `POST /reconcile-publication` | scheduler | Recover a receipt for an existing PR whose write response was lost |
| `POST /policy` | policy observer | Fresh rules and suspended-writer observations |
| `GET /claims` | worker | Only its assignments; capability only while active |
| `GET /repository-token` | worker | Only Contents/Pull requests read, for the one repository |
| `POST /propose` | worker | Capability-bound GitHub publication; broker creates commit/ref/PR |
| `GET /publication?claim=ID` | worker | Only that owner's active attempt; pending status or completed receipt |
| `POST /register` | worker | Durable registration of an immutable proposal using its active capability |
| `GET /registration?claim=ID` | worker | Only that owner’s active attempt and its registration status |
| `GET /health` | listed identities | Listener health and latest controller cycle/error classification |

Use `task_broker_client.py --url https://HOST:PORT --cert CLIENT.crt --key CLIENT.key
--ca CA.crt ROUTE`. Send POST JSON on stdin, never in arguments. `/assign`, `/claims`
and `/repository-token` responses contain credentials: consume privately, not in
shared logs. API version 3 is reported by `/health`. The updated CLI waits for
accepted publications and registrations and retries only explicitly retryable capacity/authority
errors with the same bytes. `--no-wait` returns an admission receipt;
`--claim ID publication` or `--claim ID registration` resumes polling. `--wait-seconds` bounds waiting. A wait
timeout leaves accepted work intact; use its status URL, not a different request.

The client's `--ca` authenticates the broker's server certificate. The broker's
`client_ca` authenticates the client leaves. The 100latent operator Mac uses
`server-ca.crt` for its client commands and observer feed, and keeps `ca.crt` with
the client certificate signer. The three managed worker profiles store server
trust in their existing `ca.crt` files.

### Task dependencies

The scheduler can post `{"task":"integration","requires":["parser","storage"]}`
to `/dependencies` before the task's first assignment. A plan has up to 32
distinct prerequisite task IDs. Prerequisites can be declared in any order.
The broker rejects cycles, and the scheduler can safely replay an identical plan.
Plans are durable and immutable. To change a plan, use a new task ID.

The scheduler chooses the owner and base and calls `/assign` as usual. A task
with prerequisites needs a verified integration receipt for each prerequisite's
current generation. A merge reported by GitHub can precede receipt verification.
The selected base must equal or descend from every required merge. The broker
checks ancestry with its repository-read credential, then rechecks the receipt
generations inside the assignment transaction. The grant event records the
prerequisite merges. An identical, already-granted assignment request replays
its original result.

`/status` includes each plan's blocked prerequisite IDs and verified merge
records. A blocked assignment returns HTTP 409 with prerequisite IDs and issues
no capability. Choose a base that includes the required merges after they verify.
A temporary provider failure during ancestry checks returns retryable HTTP 503.
Use the broker API to assign a task whose base needs a remote ancestry check.

For overlap hints, post `{"task":"parser","paths":["src/parser","README.md"]}`
to `/path-hints`. The scheduler can declare up to 32 repository-relative files
or directories and update the list as work changes. An empty list clears it.
The response lists matching paths on current active or draining tasks. Git path
components are case-sensitive, and a directory path matches its descendants.
The hints are advisory. Assignment and publication still use ownership fencing,
and the protected queue checks the actual integration candidate.

`POST /propose` validates the active capability and durably admits work before
returning HTTP 202 with `operation`, `state`, `status_path`, `retryable:false` and
`poll_after_seconds`. Poll that exact same-origin status URL. HTTP 200 returns
the completed publication receipt. HTTP clients that previously assumed every
successful POST synchronously returned a PR must handle this versioned change.

API v3 registration accepts `{"proposal": RECEIPT, "capability": "..."}` and
returns HTTP 202 with the same admission envelope and a `/registration` status
URL. Completion returns HTTP 200 with `{"registered": PR_NUMBER}`. Use the v3
client before upgrading the broker, or update HTTP callers to poll on 202.
Identical registration retries join the existing job, including at capacity.
A changed proposal for the same attempt returns a permanent conflict. Stale
claims are rechecked when the job runs. Job admission stores the proposal and
owner without the capability. The registered binding and completed job commit
in the same local transaction, so queued jobs resume after restart and completed
jobs retain their result. Pause submissions and drain registration jobs before
rolling back to a broker that lacks the registration queue.

Registration has its own global and per-owner limits using the settings below.
HTTP 429 `registration_full` returns `Retry-After`. The broker serves owners in
rotation and retains FIFO order within each owner. It attempts up to eight
registrations before yielding to publication and native queue reconciliation.
Registration completion records the binding. Native queue entry still requires
the original policy checks, frozen ref, and passing native Actions preflight.

Pending admission defaults to 128 jobs total and four per logical owner, including
running jobs. Configure positive integer `admission_limit` and
`admission_per_owner` values up to 1024. Identical requests reuse their existing
job even at capacity; changed requests for the same attempt are rejected.
HTTP 429 `admission_full` and HTTP 503 `authority_busy`/`authority_unavailable`
carry `retryable:true` and `Retry-After`. Rejections such as
`publication_request_conflict`, `assignment_revoked`, `invalid_capability`, and
`publication_outcome_unknown` carry `retryable:false`. Never treat every 409 as
busy. A 202 means accepted: poll rather than repeatedly posting.

The broker persists queued payloads without capabilities in its private authority
database. One logical owner gets one turn before another waiting owner; jobs from
each owner retain admission order. The turn cursor survives restart. Each native
queue-controller turn handles at most four proposals before yielding to admission.
This bounds controller work per turn, not provider-call duration or wall-clock
latency. Reassignment is rechecked at execution under the task fence; a revoked
queued attempt cannot publish. Workers cannot choose another owner's identity.

Queued jobs survive restart. Jobs found running at restart return the existing
published receipt if one was durably recorded; otherwise they become uncertain
and are never automatically repeated. A provider failure after publication intent
has the same conservative outcome. An unavailable policy/credential precondition
before any publication intent leaves the accepted job queued, with a visible
blocked reason and a paced server retry. Status polling itself never starts work.
Scheduler `/status` shows job phases/errors but no queued file payloads or
capabilities. Back up the whole authority database; there is no independent worker
copy of admission state.

A `/propose` body contains task, generation, claim, base, capability, request and
`files`. The broker accepts up to 32 edits in a 64 KiB request body. Base64 bytes
count toward that limit in their encoded form.

| Edit | Entry in `files` |
| --- | --- |
| Write UTF-8 text | `{"path":"src/example.txt","content":"new text\n"}` |
| Write binary bytes | `{"path":"assets/data.bin","content":"AP8=","encoding":"base64"}` |
| Delete a file | `{"path":"src/obsolete.txt","delete":true}` |
| Change only the mode | `{"path":"bin/run","mode":"100755"}` |

Content writes accept `encoding` of `utf-8` (default) or canonical, padded
`base64`, and `mode` of `100644` (default) or `100755`. Specify `100755` when
replacing the contents of a file that should stay executable. A mode-only edit
keeps the assigned base's blob. Mode-only edits and deletions require an existing
regular file at that base. Each path may appear once. Overlapping paths, directory
replacement, parent traversal, Git metadata paths and workflow modifications are
refused. The publisher handles regular files. Symlinks, submodules and Git bundles
are outside this contract. The broker checks every edit against the assigned base
and journals intent before the first provider write to its derived branch or
Git object store.
An identical completed request returns its receipt; changed or genuinely unknown
requests cannot blindly republish. Publication request IDs are attempt-scoped.

The broker App is the GitHub PR author; its receipt's `agent` is the TLS-authenticated
logical owner. A worker cannot fabricate that relationship: broker-authored
registration must exactly match a durable published receipt. Legacy worker-authored
PRs created before credential cutover remain enrollable by their original owner.

The trusted scheduler feeds the receipt to `task_broker_freeze_proposal.py`, using
its scheduler TLS identity and local `gh` login. That helper verifies the broker's
receipt and live PR identity, and appends only its exact ref to the configured
freeze rule. It never edits main's rule. Then the worker posts
`{"proposal":RECEIPT,"capability":"..."}` to `/register`. The broker enqueues the registered proposal on GitHub after the newest relevant
native preflight has passed. An older success at the
same SHA cannot mask a new pending run. No manual statuses or workflow dispatch
replace native PR or merge-group checks.

## Fencing, recovery and operation

A registered attempt is bound to one PR. Reassignment revokes the old generation,
withdraws and closes its PR, then withholds the successor capability until the old
PR is observed closed/merged with no queue entry. Broker publication has a separate
unregistered stage and records the commit ID before creating the branch or PR.
If a write response is lost, the scheduler can post `{"claim":"CLAIM_ID"}` to
`/reconcile-publication`. The broker reads GitHub with repository-read credentials
and recovers the receipt only if the current assignment, exact ref and commit,
broker author, and a single open PR into sandbox main all match. The scheduler
then freezes the ref and the worker registers through the usual Actions gate.

An absent or mismatched PR, a missing recorded commit ID, or a revoked assignment
leaves the uncertain outcome fenced. A retryable provider read error leaves the
request unchanged. The route never repeats a provider write. Orphan cleanup
remains an operator operation.

Restart against the same state directory. Durable queue intents are reconciled,
not blindly replayed. For a confirmed native `branch_protection_failure`, send
`{"number":104,"event":"REMOVED_EVENT_NODE_ID"}` to `/recover-removed`. The broker
fetches the latest timeline event itself and checks its node ID, GitHub bot actor,
reason, active ownership, immutable identity, current policy and absence of a queue
entry or pending/unknown RPC. It consumes that receipt once before resetting
admission; a repeated receipt never resets a later operation. Entry absence alone
is insufficient. A genuinely unobservable close still needs operator attention.

`--cutover-state` is a one-time, trusted migration from the discarded extra-main-
restriction pilot. Stop the service first. It preserves repository/main/freeze
identity, backs up SQLite privately and journals the policy transition without
resetting claims, generations or intents. Normal startup refuses mismatched state.

Restart resets policy freshness, so the observer must reconnect. Remove a client
certificate from the allowlist and restart to revoke its API identity. Reassignment
revokes attempt authority without waiting for certificate rotation. Already issued
repository-read tokens remain valid until expiry/revocation. Renew 30-day pilot
leaf certificates before expiry; update fingerprints and keep the CA signing key
with the trusted operator, off broker and worker hosts.

For client renewal, use `task_broker_renew_client.py` with `--config`,
`--old-cert`, `--new-cert`, and `--output`. The `overlap` phase prepares a
new private config allowing both certificates for the same principal. It
verifies the replacement against the configured CA and checks that the
expiration extends past the previous leaf. Install the overlap config and
verify the replacement against the live broker. Then run `retire` using the
installed overlap config to prepare the final allowlist. The command writes a
new file each time. Keep the previous config for rollback.

## Authority handoff

Use one active broker for the sandbox. Stop it and verify its process has exited
before moving authority. Keep the standby stopped until you have verified the
source shutdown. A broker holds `broker-service.lock` for its lifetime. This
excludes a second broker and checkpoint operations in the same state directory.
Across hosts, the trusted operator verifies single-writer handoff.

Run `task_broker_checkpoint.py --config CONFIG export CHECKPOINT.sqlite` as the
trusted authority account against a stopped and repository-bound broker.
The private SQLite snapshot contains assignment authority. The command reports
its SHA-256. Transfer it privately and run
`task_broker_checkpoint.py --config STANDBY_CONFIG restore CHECKPOINT.sqlite --sha256 SHA`.
Restore requires an unused database path, verifies integrity and the repository
and ruleset binding, and refuses to overwrite existing authority. Configure TLS
and the scoped App key separately. Keep the CA signing key with the trusted
operator. The standby needs a fresh authenticated policy observation before
provider mutations.

Fail back by stopping and verifying the standby, exporting its newer checkpoint,
and restoring into an unused state directory on the primary. Select that
directory in the primary config and keep it inside the unit's writable state
path. Keep the prior directory as a private backup.
Journaled uncertain writes stay fenced after a handoff and can be reconciled
using the existing read-only proof route.

### Unattended takeover

Deploy the failover host adapter beside the broker modules on each systemd host.
Provide a stopped standby under a dedicated service account, with an unused
authority path, a trusted server certificate and the same repository and ruleset
binding. Pass both trusted endpoints to the policy observer using repeated
`--url` flags. One policy read supplies both endpoints. An unreachable primary
does not delay the delivery of that observation to the standby.

Configure workers with `task_broker_client.py --standby-url HTTPS_STANDBY`
and a trust bundle that covers both brokers. The client can follow an admitted
operation's status URL to the standby. Reads also switch when the primary
returns a retryable HTTP 503 with `authority_unavailable`. The client switches
an unacknowledged POST only when the connection was refused or the name could
not resolve, before the request could be sent. TLS validation errors and
ambiguous writes stop the request without replaying it.

Use `task_broker_failover.py watch --config FAILOVER.json` on the trusted
management host. Start with an authenticated primary response. The watcher
waits for consecutive HTTPS connection failures, checks the primary service
has exited over pinned SSH, and installs a durable systemd start condition.
It exercises that condition and proves the unit stays stopped before taking
a fresh checkpoint. Missing management access, invalid credentials, unknown
source state or an unverified fence prevents standby activation.

The private config includes `journal`, `ca`, `cert`, `key`, and `primary` and
`standby` objects with `host` (pinned SSH alias), `adapter` (remote module
path), `config` (remote broker config), `unit`, and `url`. The optional
`poll_seconds` and `failure_threshold` default to five seconds and three
consecutive misses. Keep the journal and its temporary SQLite checkpoint on
a private filesystem. Restart the watcher with the same configuration and
journal to reconcile an interrupted transfer.

The source fence remains on disk after promotion. For failback, first stop
and verify the standby and transfer its latest state. Remove the source
fence only after the returning host has the authoritative snapshot. A
completely unreachable primary requires independent fencing and a current
authority backup before any takeover.

### Replicated authority for whole-host loss

Run `task_broker_replica.py --config CONFIG` on a separately powered host with
durable local storage. Its mTLS allowlist maps one certificate to each of
`manager`, `primary`, and `standby`. Configure the replica with the broker's
`manifest`, the scoped App ID and installation ID, and the App signing key.
Keep that key exclusively on the replica. Before converting an existing
deployment, drain the old brokers, remove their signing keys, and revoke any
writer installation tokens issued outside the replica. GitHub revokes an
installation token using the token itself. When prior tokens cannot be
recovered for revocation, end all legacy signing and wait through the latest
possible expiry. Include the maximum remaining signing-JWT lifetime plus the
one-hour installation-token lifetime when the last mint time is uncertain.
Suspending an installation is temporary and resuming it can restore access
to previously issued tokens. Keep each broker's replica client credential
on its own host.

Export the stopped broker's authority with the checkpoint tool. Run
`task_broker_failover.py seed-replica --config MANAGER_CLIENT.json --checkpoint CHECKPOINT.sqlite`
to seed the replica. The manager config has `url`, `ca`, `cert` and `key`.
Each broker uses the same fields in its `replica` object plus its `actor`. On
startup the assigned actor restores the replica's current snapshot. Run
the watcher with a `replica` field containing its manager client config. In
this mode it needs the primary HTTPS URL and only the standby's SSH details.
Configure the standby and dual-endpoint observer before arming the watcher.

In replicated mode, the broker serializes authority operations. It synchronously
replicates durable intents before sending GitHub mutations and commits accepted
work to the replica before acknowledging it. The replica holds the GitHub write
credential and serializes promotion against remote calls it has already sent.
Changing the active epoch rejects new writes and state updates from the old
broker. Any previously issued request with an unknown result retains its
replicated intent for reconciliation. The existing Actions and branch-policy
checks continue to gate admission.

If the replica cannot confirm authority, broker operations fail closed. The
replica is a single independent failure domain, and losing both its durable
state and the active broker is outside this mode. A completed takeover changes
the active actor. A returning original host stays fenced until the manager
performs a deliberate handoff.

The watcher records the authenticated baseline in a private file beside the
journal. It can restart during a primary outage within the same replica epoch
and still requires consecutive probe failures before takeover. After failback,
keep the completed journal and its baseline and use a fresh journal path.
A stopped standby may retain its private checkpoint and
replicated-authority marker. The readiness check verifies the cache's repository
binding and refuses SQLite sidecars that require offline recovery. On startup
the standby restores the current authoritative snapshot from the replica.

### Quorum authority

Configure three etcd voters on separate hosts with durable storage and mutual
TLS for peer and client traffic. Use an independent CA for the quorum. Give only
trusted replica services access to its client keys and storage. The database
contains checkpoints and short-lived GitHub writer tokens. Set etcd's maximum
request size to at least 16 MiB and provision storage for checkpoint revisions.
The application rejects uncompressed checkpoints above 128 MiB and compressed
checkpoints above 8 MiB. Monitor storage quota and compact old MVCC revisions.

In each replica service config, set `quorum` to `urls`, `ca`, `cert` and `key`.
Each service uses the same pinned repository binding and a copy of the scoped
writer App key, held by its private service account. Use `alternates` in broker
and manager replica client settings to list the other replica HTTPS origins.
For migration, stop the legacy broker and writer, export the stopped authority
and bootstrap the quorum with the verified snapshot, a new epoch, and the
current checkpoint revision. Start brokers using the quorum before admitting
new work. Keep the legacy service stopped once the quorum begins accepting work.

Every provider send reserves a fresh sandbox writer token in a quorum
transaction conditioned on the current epoch and revision. Promotion revokes
an outstanding token at GitHub and requires HTTP 401 before advancing the
epoch. Returning requests use the token recorded for their original turn.
Uncertain effects retain their durable broker intent for reconciliation.
After 75 seconds a surviving gateway can also fence an abandoned turn for
the same broker actor. Losing the majority refuses new work.

Run a supervised watcher on a surviving management host. When the watcher
shares a host with the standby, `standby.local` selects the local systemd host
adapter instead of SSH. Its service account must be able to manage the pinned
broker unit. Arm it and verify both broker endpoints see fresh policy
observations before disconnecting the operator machine.

### Quorum backup and recovery

Run the backup from a trusted quorum client. It captures the authority head,
compressed SQLite checkpoint and writer presence in one linearizable read. The
archive contains claim authority. Keep it in private encrypted storage away from
the voter failure domain. A capture records writer presence without exporting
the GitHub token. The reported recovery blockers identify unsettled work.

#### Scheduled encrypted capture

Schedule `task_broker_quorum_backup_job.py --config /secure/backup-job.json`
on a trusted host outside the voter failure domain. The private configuration
contains `manifest`, `quorum`, `cluster_id`, `manager`, `vault`, `recipient`,
and `openssl`. The quorum fields match the replica configuration. Pin the
cluster ID and set `manager` to an authenticated replica client configuration.
Set `recipient` to the private-path copy of the public recovery certificate,
`openssl` to the absolute binary path, and `vault` to a private directory.

The job verifies SQLite in memory and sends the archive to OpenSSL over stdin.
It asks etcd for the writer key count and reads the independent manager head
before and after capture. Each successful run publishes a directory with
`checkpoint.cms` and `receipt.json` together. The receipt records both digests,
the observed head and any recovery blockers. The recovery private key can stay
offline. Failed runs emit `quorum_backup_failed` without publishing a partial
artifact.

Use your host supervisor to schedule the command. A macOS LaunchAgent can run
hourly with `StartCalendarInterval` and a minute of the hour. Verify the
supervisor's successful run and a recent receipt. At recovery time, compare
the archive with the separately established latest acknowledged watermark.

#### Manual capture and restore

```sh
umask 077
mkdir -m 700 /secure/lit-backups
python3 -B tools/task_broker_quorum_backup.py --config /secure/source.json \
  backup /secure/lit-backups/checkpoint.lit-quorum
python3 -B tools/task_broker_quorum_backup.py --config /secure/source.json \
  inspect /secure/lit-backups/checkpoint.lit-quorum
```

For portable storage, an operator can seal the archive to the recovery
certificate before transferring it off the quorum hosts. Keep the private
decrypting key under separate recovery custody. Decrypt into a private staging
file and verify the archive digest before activation.

```sh
openssl cms -encrypt -binary -aes-256-gcm -outform DER \
  -in /secure/lit-backups/checkpoint.lit-quorum \
  -out /secure/lit-backups/checkpoint.cms /secure/recovery-recipient.crt
```

Keep the emitted archive SHA-256 separately. For a planned recovery, stop new
admission, settle pending publications and registrations, and verify the forge
queue and Actions receipts. Stop and fence the former brokers and writer gateways.
Record the final authority head independently through the manager's authenticated
`GET /head` and capture its matching archive while quorum is still available.

After permanent majority loss, bring up a fresh three-voter etcd cluster with
separate origins, peer membership and TLS credentials. Keep the broker gateway
offline until the replacement authority has been seeded. Use the new etcd cluster
ID from an independently verified member status and the old acknowledged Lit head
saved before the loss. If the watermark is missing, the archive is behind it,
or provider outcomes are unresolved, inspect the archive and reconcile with the
forge before returning the broker to service.

```sh
python3 -B tools/task_broker_quorum_backup.py --config /secure/replacement.json \
  restore /secure/lit-backups/checkpoint.lit-quorum \
  --expected-head /secure/independent/last-acknowledged-head.json \
  --sha256 "$ARCHIVE_SHA256" --target-cluster-id "$NEW_CLUSTER_ID" \
  --holder standby
```

Restore preserves the Lit checkpoint revision and advances the Lit epoch. It
seeds the head and checkpoint atomically in the new cluster and accepts retries
against the exact seeded state. Use distinct trust and endpoints so surviving
old members and gateways remain fenced. Verify all replacement gateways return
the same head. Start the chosen broker with an empty private cache, reconnect
fresh policy observers, verify the exact protected rules and native Actions
checks, and rearm the watcher at the new epoch. This procedure transfers Lit's
authority keys into a new etcd keyspace. Other etcd keyspaces require their own
backup and recovery.

For rollback, stop broker and observer, preserve the database/frozen refs, and keep
worker installations suspended. Do not restore raw worker write access while
claiming broker enforcement. The original Actions-only rule stays intact.
Drain or reconcile all admitted jobs before rolling back to a pre-v2 broker:
older binaries do not process the admission table. Preserve that table and its
unknown outcomes with the rest of the authority database.

During bursts, clients should honor `Retry-After` on pending operations. The
broker increases the interval with backlog, up to 12 seconds for publication
and six for registration. The coordinator gives revoked attempts up to three
of its four reconciliation slots when other proposals are waiting. Each group
rotates independently, and a due reconciliation turn runs before the next
publication. An in-flight provider call or a native merge that already finished
can still affect the handoff. The successor grant continues to require observed
retirement.
