# Hosted merge queue coordinator

`github_queue_coordinator.py` admits immutable, App-authored proposals to a
GitHub merge queue and withdraws stale unmergeable entries only after native
Git establishes a conflict. It does not push, merge, publish statuses, dispatch
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
objects and to enqueue/dequeue PRs. The three publisher Apps retain their
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

Stale cleanup defaults to 120 seconds of persisted `UNMERGEABLE` state with
no candidate commit. It fetches main and the immutable proposal into an
isolated bare Git mirror, runs `git merge-tree --write-tree`, then rechecks
main, proposal identity, queue entry and policy. A changed head becomes
`superseded`; a transient unmergeable entry without a real conflict stays
queued. A withdrawn conflict remains held for a new attempt to repair.

This is queue coordination for fixed attempts. It does not implement task
reassignment, scheduler ownership leases, credential issuance or CI policy
administration. A supervisor must preserve the state directory across
process restarts. Do not use two independent state directories to coordinate
the same cohort.

## Regression tests

```sh
python3 tests/github_queue_coordinator_regression.py
```

The tests exercise the public CLI against a stateful loopback HTTP forge and
real Git repositories. They kill coordinator processes after remote
acceptance, drop replies, revoke credentials, compete for the writer lock,
change heads and policies, and preserve unknown outcomes. Test API and Git
remote overrides are limited to an explicit loopback fixture. Production
traffic is restricted to api.github.com and github.com.
