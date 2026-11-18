# Hosted merge queue coordinator

`github_queue_coordinator.py` admits immutable, App-authored proposals to a
GitHub merge queue and withdraws stale unmergeable entries only after native
Git establishes a conflict. Task reassignment also withdraws and closes revoked
proposals before a successor is granted publication authority. It does not push,
merge, publish statuses, dispatch
workflows, change permissions, or resolve conflicts. macOS and Linux are
supported. The coordinator is separate from the v1.8.0 executable.

## Enrollment

Create an active repository branch ruleset covering the exact proposal refs
with update and deletion restrictions and **no bypass actors or exclusions**.
Keep the proposal branches frozen through queue processing and recovery.
The main rule must retain its native GitHub Actions requirement, no bypasses,
PR requirement, and ALLGREEN merge queue. Policy administrators are trusted
and must not weaken either rule during the run.

GitHub's [dequeue input](https://docs.github.com/en/graphql/reference/pulls#dequeuepullrequestinput)
has no expected-head parameter. Server-enforced branch immutability closes
the race that a client-side head check alone cannot close. Admission also
uses GitHub's `expectedHeadOid` argument. The coordinator checks both policy
and proposal identity again before mutations.

Example manifest, with real rule IDs and full proposal SHA substituted:

```json
{
  "repo": "100latent/lit-merge-queue-sandbox",
  "main_ruleset": 27439049,
  "freeze_ruleset": 12345678,
  "proposals": [{
    "number": 42,
    "head": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "branch": "agents/app-burst/example/host-a/00",
    "author": "100latent-lit-pilot-host-a[bot]",
    "kind": "disjoint"
  }]
}
```

```sh
python3 tools/github_queue_coordinator.py --state /private/path/queue register manifest.json
python3 tools/github_queue_coordinator.py --state /private/path/queue run --watch
python3 tools/github_queue_coordinator.py --state /private/path/queue status
```

Authentication comes from `GH_TOKEN`, `GITHUB_TOKEN`, or `gh auth token` for
github.com. Tokens are never written to the state database or Git config.
The credential needs access to read the repository's rules, PRs, checks and
objects and to enqueue/dequeue and close PRs. The three publisher Apps retain their
existing sandbox-only Contents and Pull Requests write permissions.

When an App cannot see ruleset bypass actors or check provenance, provide
`LIT_QUEUE_OBSERVER_TOKEN` from an existing operator credential. It is used
only for observations. Mutations and Git fetches still use the writer token.
In this mode the writer must be an installation token limited to this one
repository; its scope and validity are checked every cycle. An observer
credential cannot substitute for a revoked writer. No additional permissions
are granted to the publisher Apps.

## Durable behavior

Registration binds each PR number to its original SHA, branch, author and
repository. Repeating the same registration preserves existing progress.
Changing an enrolled attempt is rejected. Use a new PR for a new attempt.
An OS file lock permits only one writer per state directory. `status` remains
readable while the writer is running. SQLite commits an operation intent
before the remote mutation and records acknowledgement separately.

Restart with the same state directory. If GitHub accepted an enqueue or
withdrawal before the response was lost, the next observation reconciles the
intent without repeating it. Revoked credentials stop processing. Refresh
the credential and restart against the same journal. An accepted queue entry
that disappears is never automatically submitted again. Its absence can
precede GitHub reporting the merge; the coordinator waits for a definitive
merged, closed or conflicting outcome instead of classifying absence as removal.

If an enqueue's outcome is unknown and GitHub exposes neither an entry nor
terminal evidence, the coordinator retains `unknown_enqueue` and does not
blindly retry. This is a deliberate fail-closed limit, not an exactly-once
claim. A watch deadline fails with durable state intact. Inspect the journal
and remote history to resolve genuinely unobservable outcomes.

Stale cleanup defaults to 120 seconds without a candidate commit, with either
`UNMERGEABLE` state or `QUEUED` state and a PR reported `CONFLICTING`. GitHub can
leave a conflicting PR queued while earlier candidates occupy build slots.
The coordinator fetches main and the immutable proposal into an
isolated bare Git mirror, runs `git merge-tree --write-tree`, then rechecks
main, proposal identity, queue entry and policy. A changed head becomes
`superseded`; a transient unmergeable entry without a real conflict stays
queued. A withdrawn conflict remains held for a new attempt to repair.
Queued-conflict cleanup evaluates current main; it does not plan dependencies
or predict whether a preceding proposal might later resolve the conflict.

A supervisor must preserve the state directory across process restarts. Use
one local authority directory for the same cohort. SQLite and OS locks are
single-machine authority primitives; do not copy the database to workers or
use independent directories as competing authorities.

## Task ownership and reassignment

The trusted scheduler creates the first claim with expected generation zero:

```sh
python3 tools/github_queue_coordinator.py --state /private/path/queue assign task-42 \
  --expect 0 --owner 'worker[bot]' --base cccccccccccccccccccccccccccccccccccccccc \
  --request assignment-42-first
```

This returns task, generation, claim ID, owner, base, phase and a capability.
Keep the capability out of logs. The scheduler authenticates the worker before
delivering it. The owner is the worker's GitHub App bot login. Add the receipt's
`task`, `generation`, `claim` and `base` fields to that attempt's proposal
manifest. Submit a JSON object mapping claim IDs to capabilities on stdin:

```sh
python3 tools/github_queue_coordinator.py --state /private/path/queue \
  register manifest.json --claims-stdin < /private/path/claim-capabilities.json
```

A bound attempt has exactly one immutable PR identity. Registration atomically
checks capability, task, generation, owner and assigned base with the PR binding.
The assigned base is recorded and matched; this does not prove Git ancestry.
Once tasks exist, unowned registration is rejected. Legacy fixed-attempt states
must use a fresh authority directory to enable ownership, avoiding silent upgrade
of already-published work. Legacy states remain usable in fixed-attempt mode.

To reassign, repeat `assign` with the current expected generation, a new owner
and a new unique request ID. Generations never decrease. Concurrent claims or
reassignments for the same expected generation have at most one winner. An
identical request retries the same receipt; changing its arguments is rejected.
A revoked request cannot resurrect its capability, even if ownership returns
to the same bot later. Capability values are not stored or included in status;
the private database contains their derivation key and must remain private.

An unbound claim can transfer immediately. A bound claim advances the generation
and revokes the old owner, but the successor starts `draining`, without a
capability. The coordinator withdraws the old entry even if it is nonconflicting,
then closes the old PR using GitHub's
[closePullRequest mutation](https://docs.github.com/en/graphql/reference/pulls#closepullrequest).
Only observing closed or merged **and no queue entry** retires that attempt and
activates its successor. Replay the scheduler's reassignment request to obtain
the now-active capability. A changed immutable identity or missing policy blocks
handoff. Unknown remote effects stay journaled and are never blindly retried;
closure fences pending effects without falsely reporting them as observed.
An old attempt that merged before retirement is recorded as merged; reassignment
does not undo a completed merge. The scheduler must choose the next base.

Per-task OS locks cover the generation check, durable intent and remote call.
An in-flight publication makes same-task reassignment return busy; retry the
same request. Other tasks can still be claimed. A killed process releases the
lock, while its bound attempt still requires remote retirement. The long-lived
coordinator writer lock does not prevent scheduler claim/reassignment commands.

This is an authority core for a trusted scheduler/broker. Do not expose `assign`,
the authority filesystem/account, or the broker's GitHub write token to workers.
Possession of the state database is administrative access. A worker capability
is not an authenticated network API; workers need a broker that authenticates
their identity before invoking these commands. The pilot Apps still capable of
direct GitHub writes can bypass this client-side fence. Broker deployment and
forge-level enforcement against such direct writes remain unqualified. No
GitHub rules are weakened, no manual status is created, and no lease expiry,
dependency scheduler or credential issuer is introduced here.

## Regression tests

```sh
python3 tests/github_queue_coordinator_regression.py
```

The tests exercise the public CLI against a stateful loopback HTTP forge and
real Git repositories. They kill coordinator processes after remote
acceptance, drop replies, revoke credentials, compete for the writer lock,
change heads and policies, and preserve unknown outcomes. Task tests also race
claims and reassignments, reject stale/forged capabilities, exercise A-to-B-to-A
ownership, kill a publisher during reassignment, and lose retirement replies. Test API and Git
remote overrides are limited to an explicit loopback fixture. Production
traffic is restricted to api.github.com and github.com.
