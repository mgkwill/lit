# Pi coding pilot with inference on host-a

Three Pi 1.5.2 workers ran on host-a, host-e, and host-f in the 100latent
sandbox. All five accepted coding sessions used the `model-under-test`
API alias on host-a. The endpoint mapped to a local vLLM container with
two GPU worker processes. The host-a worker used loopback. The two ARM
workers reached host-a over the lab network. The first attempt used each worker host's local
inference endpoint. Its three PRs closed without merging, and the pilot
was rerun with the corrected routing. [PR #431][pr431] · [PR #432][pr432] · [PR #433][pr433]

The three generation-one workers started at Git commit
`0c395bc2447b59f0a3223c4bf17c71a45d7a21a6`. Pi used its file tools on a
scoped directory inside each pinned Lit workspace. The launched process
denied filesystem access outside its allowed paths, child-process creation,
and connections to a test TCP port outside the model allowlist. The fixed
provider endpoint selected host-a. The broker credentials stayed in the
worker controller's scope. [Pi documentation](https://pi.dev/) · [Landlock documentation](https://docs.kernel.org/userspace-api/landlock.html)

| Worker host | Task | Generation-one PR |
|---|---|---|
| host-a (x86-64) | Normalize a route string | [#434][pr434] |
| host-e (arm64) | Calculate a bounded retry delay in the same file | [#435][pr435] |
| host-f (arm64) | Classify capacity in a separate file | [#436][pr436] |

The broker observed #436 in the native queue, reassigned the task from
host-f to host-a, and withdrew the old entry. The stale worker's publication
and registration both returned `assignment_revoked`. Pi on host-a
produced a fresh generation-two proposal that passed its six acceptance
checks. [PR #437][pr437]

The two routing tasks independently created the same file. After #434
landed, the broker held #435, and Git independently reproduced the add/add
conflict. Pi on host-e added the retry function on a fresh pin using host-a
for inference. Git verified that the entire syntax tree of the original
routing function was preserved. All 11 routing checks passed before the
resolution was published. [PR #438][pr438]

All five proposal heads passed their native pull-request checks. PRs
#434, #437, and #438 merged through the Actions-only queue. The audit
matched each merge to its exact Git parents, native merge event, and
successful merge-group check from Actions App 15368. The three merged
heads and merge commits had zero manual statuses. The two retired PRs
closed without merging. `git fsck --full --strict` and all 17 acceptance
checks passed on the final Git tree at
`2f9f55b9ab09d6094b25d9a9f48f4d9d49e7e4c2`.

The original ruleset configuration was restored, the queue drained, and
all three temporary worker identities were rejected after revocation. The
broker was healthy after the cleanup restart. The 144 broker regression
tests passed on macOS and on all three worker hosts. Post-MIT upstream
stayed black-box. Raw Pi transcripts are retained outside the repository.
[Structured results](pi-hosta-coding-pilot-2026-11-19.json)

[pr431]: https://github.com/100latent/lit-merge-queue-sandbox/pull/431
[pr432]: https://github.com/100latent/lit-merge-queue-sandbox/pull/432
[pr433]: https://github.com/100latent/lit-merge-queue-sandbox/pull/433
[pr434]: https://github.com/100latent/lit-merge-queue-sandbox/pull/434
[pr435]: https://github.com/100latent/lit-merge-queue-sandbox/pull/435
[pr436]: https://github.com/100latent/lit-merge-queue-sandbox/pull/436
[pr437]: https://github.com/100latent/lit-merge-queue-sandbox/pull/437
[pr438]: https://github.com/100latent/lit-merge-queue-sandbox/pull/438
