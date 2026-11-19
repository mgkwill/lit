# Broker provider failure qualification

Each of four environments ran the native broker from `d5c85f06c0e2636c70daa6a3932e71f602862ed4` with 100 distinct
mTLS worker identities, up to 16 concurrent clients, and a loopback provider
fixture. The sandbox and its live Actions-only policy were unchanged.

- The provider returned HTTP 429 and 503 on policy reads. The broker durably
  admitted all 100 requests and issued zero provider writes while blocked.
- Once reads recovered, the provider accepted exactly one PR per task. It applied
  two PR creations before returning a fault, first closing the connection and
  then returning HTTP 502 on a different task. The broker returned 98 receipts
  and kept both failed responses in the explicit unknown state.
- After restarting the broker, each worker checked its status and resubmitted the
  exact original request. The 98 receipts replayed and both unknown operations
  stayed unknown. The provider saw zero additional writes.

| Native environment | Owners | Receipts | Unknown outcomes | Replayed writes |
| --- | ---: | ---: | ---: | ---: |
| local Darwin arm64 | 100 | 98 | 2 | 0 |
| host-a Linux x86_64 | 100 | 98 | 2 | 0 |
| host-e Linux aarch64 | 100 | 98 | 2 | 0 |
| host-f Linux aarch64 | 100 | 98 | 2 | 0 |

All 92 broker and coordinator regressions also passed in each environment.
The injected errors came from the test fixture. The live sandbox evidence for
automatic PR and merge-group Actions checks is recorded
[with the mixed-file publication](BROKER-MIXED-PUBLICATION-2026-11-18.md).
The remaining recovery gap is a verified way to recover an existing PR receipt
after its create response was lost.

[Structured results](broker-provider-failure-results-2026-11-18.json) include the tested source fingerprints and log
hashes. Raw logs and the exact test snapshot are retained at `<private-path>`.

## Receipt recovery follow-up

The same four environments reran the 100-owner scenario with the reconciliation
route. Each run kept both uncertain writes fenced through restart, recovered
both exact PR receipts from read-only evidence, and observed zero extra provider
writes. All 94 broker and coordinator regressions passed in each environment.
A read-only query against live PR #324 also confirmed GitHub's branch-filtered
PR listing and exact Git ref lookup. The fault and recovery exercise used the
loopback provider. Raw logs and source fingerprints are at `<private-path>`.
