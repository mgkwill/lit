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
Administration read. Never interpret omission as an empty list. The separate
observer runs on the operator's Mac using the existing `gh` login. It only reads
the configured main/freeze/namespace rules and the configured worker installation
states, then sends filtered metadata over mTLS. It never reads, exports or sends
the GitHub credential to the broker.

Run `task_broker_policy_observer.py` with `--url`, `--ca`, `--cert`, `--key`,
`--manifest` and the installed `--gh` path. The public observer manifest contains
`repo`, `main_ruleset`, `freeze_ruleset`, `publication_ruleset` and
`worker_installations` from service configuration. `--once` checks one delivery;
otherwise it refreshes every five seconds. Supervise it in the operator's launchd
login session. The feed expires within 30 seconds, rejects stale/replayed records,
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
| `POST /assign` | scheduler | CAS task assignment: task, expect, owner, base, request |
| `GET /status` | scheduler | Claim/proposal/action/publication metadata, without credentials |
| `POST /recover-removed` | scheduler | Consume one verified native removal receipt; never retry unknown RPCs |
| `POST /policy` | policy observer | Fresh rules and suspended-writer observations |
| `GET /claims` | worker | Only its assignments; capability only while active |
| `GET /repository-token` | worker | Only Contents/Pull requests read, for the one repository |
| `POST /propose` | worker | Capability-bound GitHub publication; broker creates commit/ref/PR |
| `GET /publication?claim=ID` | worker | Only that owner's active attempt; pending status or completed receipt |
| `POST /register` | worker | Immutable, frozen proposal and capability; owner is derived from TLS |
| `GET /health` | listed identities | Listener health and latest controller cycle/error classification |

Use `task_broker_client.py --url https://HOST:PORT --cert CLIENT.crt --key CLIENT.key
--ca CA.crt ROUTE`. Send POST JSON on stdin, never in arguments. `/assign`, `/claims`
and `/repository-token` responses contain credentials: consume privately, not in
shared logs. API version 2 is reported by `/health`. The updated CLI waits for
accepted publications and retries only explicitly retryable capacity/authority
errors with the same bytes. `--no-wait` returns an admission receipt;
`--claim ID publication` resumes polling. `--wait-seconds` bounds waiting. A wait
timeout leaves accepted work intact; use its status URL, not a different request.

`POST /propose` validates the active capability and durably admits work before
returning HTTP 202 with `operation`, `state`, `status_path`, `retryable:false` and
`poll_after_seconds`. Poll that exact same-origin status URL. HTTP 200 returns
the completed publication receipt. HTTP clients that previously assumed every
successful POST synchronously returned a PR must handle this versioned change.

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
`files`, for example `[{"path":"src/example.txt","content":"new bytes\n"}]`.
The first publisher supports regular UTF-8 file additions/updates, not deletion,
binary upload, symlink/mode changes or arbitrary Git bundles. Parent traversal,
Git metadata paths and workflow modifications are refused. Publication uses the
assigned base and a broker-derived branch, and journals intent before mutations.
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
`{"proposal":RECEIPT,"capability":"..."}` to `/register`. The broker admits it only
after the newest relevant native preflight has passed. An older success at the
same SHA cannot mask a new pending run. No manual statuses or workflow dispatch
replace native PR or merge-group checks.

## Fencing, recovery and operation

A registered attempt is bound to one PR. Reassignment revokes the old generation,
withdraws and closes its PR, then withholds the successor capability until the old
PR is observed closed/merged with no queue entry. Broker publication has a separate
unregistered stage: if interrupted, it can leave an orphan ref/PR. Such an attempt
cannot register after revocation, workers cannot enqueue it directly, and the
broker never queues it without registration. Orphan cleanup remains an operator
operation; an uncertain publication is not automatically repeated.

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

For rollback, stop broker and observer, preserve the database/frozen refs, and keep
worker installations suspended. Do not restore raw worker write access while
claiming broker enforcement. The original Actions-only rule stays intact.
Drain or reconcile all admitted jobs before rolling back to a pre-v2 broker:
older binaries do not process the admission table. Preserve that table and its
unknown outcomes with the rest of the authority database.
