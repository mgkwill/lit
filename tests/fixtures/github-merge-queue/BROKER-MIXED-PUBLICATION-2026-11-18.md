# Live broker mixed-file publication

On November 18, 2026 (Pacific time), the private
[100latent sandbox](https://github.com/100latent/lit-merge-queue-sandbox) ran the mixed-file publisher
from `5c69e8f1c9d7162bd2c7678bbc48a7f62d09e763` on host-a. The deployment replaced two broker modules
and verified all eight installed Python modules against that commit. The broker
restarted with its existing authority state and configuration and completed a
healthy API v3 controller cycle. The existing host-a worker identity published
through mTLS.

The seed PR created two disposable regular files under
`qualification/mixed-file-qf91uiyp/`. The mixed PR used the seed merge
`fa6530098a7c7238cd2f3512c819585718b01fe0` as its assignment base. Git verified
the following edits in both the published head and the commit that landed on
`main`.

| File | Git result |
| --- | --- |
| `asset.bin` | Added 281 exact bytes as mode `100644`. SHA-256 `e2a4712dcd0a58834f37e095391647621599de535516835f36e84f888e6588b1` |
| `obsolete.txt` | Present in the seed and absent after the mixed edit |
| `mode.sh` | Changed from `100644` to `100755` with the same Git blob `87f56e1d3ebb0074c4153717fd9de8f765b885bc` |

Git reported exactly these three changes. The mixed proposal's parent was its
assigned base, and its published head `70c6a749bbc42f0bad635a425b05f715f037d3d1`
was the second parent of the first-parent merge on `main`,
`b2acf9bf1f68ee1b4a2a048b7203bbbac90cb702`.

| PR | Automatic PR check | Automatic merge-group check |
| --- | --- | --- |
| [#323](https://github.com/100latent/lit-merge-queue-sandbox/pull/323) seed | [Run 44458337936](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44458337936) | [Run 44458960184](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44458960184) |
| [#324](https://github.com/100latent/lit-merge-queue-sandbox/pull/324) mixed edits | [Run 44459693930](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44459693930) | [Run 44460343903](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44460343903) |

Both PRs used the native queue and produced verified broker integration receipts.
The mixed merge event `ME_lADOVxXMNs8AAAABgmMsVM8AAAAIhjH4bQ`
named the same Git commit as the successful merge-group run. The Actions jobs
checked out their integration candidates, verified the candidate SHA, built and
tested rollback, and ran the agent workflow. Git independently verified the
published file bytes and modes.

The Actions-only main rule required `Agent workflow candidate` from GitHub
Actions App `15368`, a PR and the native merge queue, with no bypass actors.
The protected main and broker namespace rules compared equal before and after the
run. The trusted scheduler added only the two exact proposal refs to the freeze
rule. Both proposals kept their immutable refs for receipt verification. The
native queue was empty afterwards. No manual statuses were posted.

The deployment used MIT-derived recovery code. Post-MIT upstream remained a black
box.

[Structured results](broker-mixed-publication-results-2026-11-18.json)
