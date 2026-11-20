# Resumable Pi pilot with 10 coding agents

The `pinned-pi-fb34d649` run used the private 100latent sandbox and
Git base `44417d32877fc5dbd937eb348fbf6b80b4fd986f`. Pi 1.5.2 ran on host-a
(x86-64), host-e (arm64), and host-f (arm64). The three hosts ran
4, 3, and 3 workers respectively, with
all inference served by the vLLM endpoint on host-a. The service advertised
the `model-under-test` API alias. Workers used a private Lit
workspace pinned before the controller released the cohort.

All ten initial Pi prompts fell within 127 ms
by worker-clock timestamps. Their session intervals overlapped for
9.89 seconds. The intervals
measure Pi activity from its user message to its last assistant message.

Each task edited a JSON executable-contract fixture and its README.
A trusted harness ran the fixtures against the MIT-derived Lit binary
and Git, covering JSONL batch, worktree status, MCP over stdio, and Git
round-trip bytes, modes, and a tag. The harness verified the files
before publication and again on the final Git tree.
One initial attempt produced a top-level JSON array and was rejected.
A fresh Pi attempt wrote the required JSON object and passed.

## Controller restart

The controller exited after the live broker accepted the proposal for [#453](https://github.com/100latent/lit-merge-queue-sandbox/pull/453)
and before the controller saved the acceptance response in its journal.
On restart, it used the owner-scoped publication lookup and adopted the
existing receipt. The trusted transport recorded one proposal submission
for that attempt, and the run kept the same request identifier.
The journal also recorded the exact broker registration and integration
receipts for completed attempts.

## Reassignment and overlapping writes

| Exercise | First proposal | Accepted replacement | Outcome |
| --- | --- | --- | --- |
| host-f to host-a | [#457](https://github.com/100latent/lit-merge-queue-sandbox/pull/457) | [#463](https://github.com/100latent/lit-merge-queue-sandbox/pull/463) | Native queue withdrawal and closure before the replacement published |
| host-e to host-f | [#458](https://github.com/100latent/lit-merge-queue-sandbox/pull/458) | [#464](https://github.com/100latent/lit-merge-queue-sandbox/pull/464) | Native queue withdrawal and closure before the replacement published |
| batch overlap | [#454](https://github.com/100latent/lit-merge-queue-sandbox/pull/454) | [#465](https://github.com/100latent/lit-merge-queue-sandbox/pull/465) | Git reproduced the conflict. The replacement preserved the case from [#453](https://github.com/100latent/lit-merge-queue-sandbox/pull/453) |
| mcp overlap | [#456](https://github.com/100latent/lit-merge-queue-sandbox/pull/456) | [#466](https://github.com/100latent/lit-merge-queue-sandbox/pull/466) | Git reproduced the conflict. The replacement preserved the case from [#455](https://github.com/100latent/lit-merge-queue-sandbox/pull/455) |

Both old cross-host proposals were observed in the native queue before
reassignment. GitHub recorded their queue removal followed by PR closure.
All four retired attempts received `assignment_revoked` for both stale
publication and registration.

## Integration and cleanup

Fourteen PR heads passed their native pull-request checks. Ten PRs merged
([#453](https://github.com/100latent/lit-merge-queue-sandbox/pull/453), [#455](https://github.com/100latent/lit-merge-queue-sandbox/pull/455), [#459](https://github.com/100latent/lit-merge-queue-sandbox/pull/459), [#460](https://github.com/100latent/lit-merge-queue-sandbox/pull/460), [#461](https://github.com/100latent/lit-merge-queue-sandbox/pull/461), [#462](https://github.com/100latent/lit-merge-queue-sandbox/pull/462), [#463](https://github.com/100latent/lit-merge-queue-sandbox/pull/463), [#464](https://github.com/100latent/lit-merge-queue-sandbox/pull/464), [#465](https://github.com/100latent/lit-merge-queue-sandbox/pull/465), [#466](https://github.com/100latent/lit-merge-queue-sandbox/pull/466)), and four closed without merging
([#454](https://github.com/100latent/lit-merge-queue-sandbox/pull/454), [#456](https://github.com/100latent/lit-merge-queue-sandbox/pull/456), [#457](https://github.com/100latent/lit-merge-queue-sandbox/pull/457), [#458](https://github.com/100latent/lit-merge-queue-sandbox/pull/458)). Each merge receipt matched the exact Git
parents, native merge timeline, and successful `merge_group` run from
Actions App 15368. No manual statuses appeared on the proposal heads
or merge commits. The experiment increased the sandbox build capacity
from one to ten and restored it after the run. The required check
`Agent workflow candidate`, single-entry merges, and empty bypass list
stayed in force.

The final tree at `c242741047d50427c4a9684176d48e0ec4b4bed7` passed
`git fsck --full --strict` and all ten executable-contract cases across
eight fixture directories. The original ruleset and broker configuration
were restored, the queue emptied, all ten temporary worker certificates
were denied, and the three temporary worker directories were removed.

The 151 broker and runner tests passed on macOS and all three Linux
worker hosts. The live run used recovery commit `9bf7875`.
Post-MIT upstream remained a black box. Raw session and control records
are retained outside the repository. [Structured evidence](pi-resumable-pilot-2026-11-19.json)
