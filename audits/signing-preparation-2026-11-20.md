# Signing preparation — November 20, 2026

Signing preparation is committed at `1918abb390f648b8419cf70717655def1bce3cdc`.
No signed release candidate or attestation has been produced. No publication
or signing credential installation was performed. Post-MIT upstream Lit source
was not used; these changes affect release tooling, tests, workflows, and docs.

## Completed

- Added the disabled-by-default manual signing workflow, native macOS and Windows
  helpers, final distribution verification, and exact-identity attestation checks.
- Pinned the new workflow's action references to full SHAs resolved from the
  official action repositories and the Rust toolchain to 1.100.0. This records
  dependency pinning, not a complete audit of those actions' implementations.
- Kept publication absent and `contents: read` throughout. OIDC and attestation
  writes are confined to the attestation job after native verification succeeds.
- Documented publisher credentials, protected environment setup, dedicated
  ephemeral signing workers, and outstanding live gates in
  [the credential guide](../docs/RELEASE_CREDENTIALS.md).

## Verification

[Private source checks](https://github.com/mgkwill/lit-release-qualification/actions/runs/44865122855)
passed on macOS, Windows, and Linux. Source projection
`f0e1d22017f109669428452e7aace5b3dde5bfe6` matched the recovery source except for the
three unrelated workflows listed in the JSON receipt. These jobs test the
installer, notices, evidence exporter, and signing rejection policy; they do not
run the privileged signing workflow.

Local results: ten signing-policy tests passed, nine notice tests passed, two
export tests passed, and both edited workflows passed `actionlint`. The available
Apple Development identity was explicitly rejected by the distribution signing
preflight; no output DMG was created. macOS and Windows CI used their real native
signature verifiers to reject an unsigned file. Other cases exercise tampering,
incomplete manifests, exact attestation identity constraints, unavailable service
failure, and rejection of missing timestamps or hardened runtime.

The same signing-policy suite ran on host-a (x86-64), host-e and host-f (arm64).
Each passed nine tests and skipped the native Apple/Windows verifier test, which
cannot run on Linux. No host service, certificate, or security policy changed.

`cargo build --locked --release` succeeded locally. A fresh **unsigned** Apple
Silicon archive with the final notice inventory passed extracted-binary batch,
MCP, and native/file/smart-HTTP remote checks. Its hash is
`4d5ef3094b4998ee26fea32fe254a01ff8ce7694ebcd8679dba63f007933f1a1`.
The binary is unchanged from the earlier local build; this is a fresh package,
not evidence of an independent clean compilation or a signed candidate. No full
Rust regression rerun was needed for this release-tooling-only change.

The live 100latent rule remained active with no bypass actors, merge queue and
PR enforcement, and `Agent workflow candidate` bound to GitHub Actions integration
15368. No manual status was posted.

[Machine-readable receipt](signing-preparation-2026-11-20.json) records source,
projection, runs, host outputs, unsigned package smoke results, and open gates.
Raw logs and the unsigned archive remain outside Git at
`<private-path>`.

## Still required

The local keychain has no Developer ID Application identity; the existing private
qualification repository has no configured secrets or environments. The Windows
publisher/provider is unselected. Supply secure references for the Apple identity
and notarization profile and Windows signing provider, then configure the
protected signing environment and ephemeral workers. A remote-only Windows
provider needs its adapter implemented and reviewed first.

Private GitHub attestations require an eligible Enterprise Cloud repository;
selection and live capability verification remain pending. The prepared workflow
must pass on all five native targets, and its downloaded final archives and
attestations must pass verification. Complete the clean-machine download review,
then ask for publication approval with the exact final bytes and identities.
This task has not demonstrated successful signing, notarization, timestamping,
or live attestation generation. The new implementation records are outside the
previous transcript archive's explicitly frozen scope.
