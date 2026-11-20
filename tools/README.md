# Coordinate OpenLit agents

The tools in this directory coordinate separate workers through a trusted broker
and GitHub's protected merge queue. They are Python services and clients, separate
from the `lit` executable. Installing OpenLit does not start these services.

## The workflow

1. The scheduler assigns a task, worker, and full base commit.
2. The worker edits a separate checkout and submits a proposal to the broker.
3. The broker checks the worker's identity and current task generation, then
   creates a commit, branch, and pull request with its own GitHub credential.
4. The scheduler freezes that exact branch. The worker registers the receipt.
5. Native GitHub Actions preflight checks must pass before queue admission.
6. GitHub tests the merge candidate in its protected queue. The coordinator
   verifies the actual merge and Actions evidence before recording integration.

Workers receive repository-read tokens and individual TLS identities. They do
not receive a GitHub writer key. Retired worker write installations must stay
suspended. Privileged host operators and GitHub policy administrators remain trusted.

## What Actions-only means

The required check must come from GitHub Actions on the exact integration
candidate. A manually posted status cannot stand in for that check. The main
branch rule also requires a PR and merge queue, with no bypass actors. Proposal
refs are frozen by an active exact-ref rule with no update or deletion bypasses.
The broker's publication namespace has its own scoped rule; it is not a main
branch exemption. See [deployment](deployment/README.md).

## Ownership and reassignment

The scheduler grants the first assignment using expected generation zero. Each
reassignment must match the current generation, then advances it. Concurrent
claims for the same generation have at most one winner. Repeating an identical
request returns its original receipt; changing that request is rejected.

A capability authorizes one worker's active attempt. Keep it private. An old
capability cannot regain authority when ownership later returns to the same
worker. A claim binds to one immutable PR identity: number, head, branch, author,
repository, and base.

For a claim already tied to a PR, reassignment first revokes the old generation.
The successor waits while the coordinator removes the old queue entry and closes
the old PR. The old attempt must be observed closed or merged, with no queue
entry, before the successor gets its capability. A merge that already completed
is recorded; reassignment cannot undo it. The scheduler chooses a new base.

Task dependencies require verified integration receipts for the current required
generations, and a base containing those merges. File overlap hints help planning;
they do not authorize or block publication by themselves.

## Durable results

The coordinator records intent before a remote mutation and acknowledgement
after it. Restart against the same private state directory. If GitHub may have
accepted a write before a response was lost, reconcile it using remote evidence.
Do not blindly retry. An absent queue entry alone does not prove a withdrawal;
GitHub may be completing a merge.

An `unknown_enqueue` stays unresolved until evidence is available. A watch timeout
also leaves durable state intact. Revoked credentials stop work. A refreshed
credential resumes the same journal; it does not erase earlier uncertainty.

The standalone coordinator permits one writer per state directory. An OS lock
and SQLite are local authority, not a distributed lock. Use the replica/quorum
setup for managed cross-host authority rather than copying live databases.

## Integration receipts

In coordinator or broker status, inspect `proposals[].integration`. Downstream
tasks need `state: verified`; `phase: merged` alone is not sufficient.

Git must show one merge on main's first-parent history whose second parent is the
frozen proposal head. A successful native `merge_group` run, matching check suite,
and matching check run must cover that exact merge SHA. The forge's summary SHA
is retained as a diagnostic, not accepted as a substitute. This handles the
[observed PR #210 mismatch](../tests/fixtures/github-merge-queue/BROKER-V2-BURST-2026-11-18.md).

Missing evidence remains pending and can be checked again. Normal broker polling
verifies receipts. A standalone queue watch can finish before receipt verification;
run more reconciliation cycles for pending receipts.

## Tools and tests

| Tool | Purpose |
| --- | --- |
| `github_queue_coordinator.py` | Ownership, immutable registration, queue reconciliation, receipts |
| `task_broker.py` | Authenticate workers and expose task/publication operations |
| `task_broker_client.py` | Submit requests and poll accepted operations |
| `task_broker_policy_observer.py` | Deliver fresh policy observations without sharing its GitHub credential |
| `task_broker_freeze_proposal.py` | Verify a publication receipt and freeze that proposal ref |
| `pi_pilot.py` | Run and resume a bounded cohort of coding attempts |
| `pi_pilot_control.py` | Checkpoint and transfer a pilot controller |

Read each tool's `--help` and [the operations guide](deployment/README.md) before
using it on a live sandbox. Requests carrying capabilities belong on private stdin,
not command-line arguments or shared logs.

Run `python3 tests/github_queue_coordinator_regression.py` and
`python3 tests/task_broker_regression.py` for disposable forge/CLI tests. They
exercise lost responses, stale owners, racing claims, changed policy, and interrupted
operations. See [testing](../TESTING.md) for the complete lanes.
