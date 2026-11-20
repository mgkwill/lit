# Security evidence and limits

This document is a guide to evidence, not a certification or a claim that no
vulnerabilities remain. Earlier broad security claims have been removed from
the active guides.

Current repository tests cover transaction interruption, competing writers,
reachable object verification, packed storage, encryption accounting, strict
sandbox refusal, and broker ownership and recovery. Hosted records cover
specific GitHub rules and exact Actions checks. Each record is bounded to its
source revision and environment.

Read [testing](TESTING.md) for the test lanes and [security](SECURITY.md) for
operating boundaries. Historical fixture reports remain unchanged under
`tests/fixtures/`; do not silently relabel them as OpenLit 0.1.0 results.

The separate [provenance audit](audits/provenance-2026-11-17.md) concerns source
origins. It is not a security review of the current commit. Its source-comparison
material must not become an implementation input. The audit's recorded scope
does not automatically cover later work.

Before publication, review the exact candidate, dependency inventory, signing
workflow, secret handling, and remaining limits. Preserve adverse results as well
as passing ones. Transcript archival is pending until the exports and manifest
have actually been reviewed and committed under AGENTS.md.
