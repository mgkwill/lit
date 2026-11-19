# Broker failover and certificate renewal rehearsal

On November 18, 2026 (Pacific time), the
[100latent sandbox](https://github.com/100latent/lit-merge-queue-sandbox)
upgraded its host-a broker from `5c69e8f` to `cc36a28eaa68c072a62cc9fea246ec10c981f863`.
The eight installed Python module hashes matched the commit. The broker resumed
API v3 with its claim and action journal intact.

| Handoff | Measured interval |
| --- | ---: |
| Primary stopped to healthy controller cycle on host-b | 7.0 s |
| Standby stopped to healthy controller cycle on host-a | 6.8 s |

The operator verified the source service's process had exited before each
authority transfer. The stopped database was copied through SQLite backup and
passed `PRAGMA quick_check` in both directions. The temporary host-b service ran
under a dedicated non-login account with a loopback listener reached through SSH.
The primary kept a private pre-restore copy. The standby account, state, and
credential copies were removed after failback.

The rehearsal used temporary worker, scheduler, and policy observer leaf
certificates with an overlap period. Each replacement mapped to the same
principal as its predecessor. TLS presented the renewed standby server leaf.
After the retire step, the old client fingerprints received HTTP 403 while the
old certificates were still valid and the worker's replacement recovered the
same active assignment and capability. The CA key stayed on the operator's Mac.
The primary's regular certificates continue serving their original identities.

The live scheduler used PR #324's verified merge as a prerequisite and refused
the earlier base. The standby accepted the publication with no fresh policy
observation, held it without a GitHub PR, and preserved the queued job through
the certificate restart. An authenticated observation from the renewed observer
resumed that same job, using the scoped App credential. The standby published
and registered [PR #325](https://github.com/100latent/lit-merge-queue-sandbox/pull/325).
After failback, the original worker got the same receipt from host-a.

| Automatic GitHub Actions check | Run |
| --- | --- |
| PR preflight | [44466623072](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44466623072) |
| Native merge group | [44467271633](https://github.com/100latent/lit-merge-queue-sandbox/actions/runs/44467271633) |

The merge event, first-parent Git history, and successful merge-group check all
identified `320b776f58c9b06ebc6e97deaf46eb4c4c292bd4`. Git verified the published and landed
file bytes. The protected main rule retained the Actions integration, the native
queue and zero bypass actors. The scheduler added only the exact proposal ref
to the freeze ruleset. The run posted zero manual statuses and ended with an
empty native queue and a healthy host-a broker.

This was an operator-controlled rehearsal with a planned stop. The operator
continued to provide the policy observer during failover. Post-MIT upstream Lit
remained a black box.

[Structured results](broker-operations-drill-results-2026-11-18.json) contain timestamps and fingerprints. Raw
receipts and operator scripts are retained at `<private-path>`.

## Follow-up tooling verification

The guarded checkpoint tool exported and restored a private copy of the actual
pre-failback backup on host-a. The restored SQLite file matched the checkpoint
bytes, and every row of the backup matched after restore. The live broker PID
and Actions-only rule remained stable during the smoke test. The checkpoint
and client renewal tools also passed the 104 regressions and 100-identity
provider-failure stress test on macOS, host-a, host-e, and host-f.
