# Prepare an OpenLit release

The first OpenLit version is **0.1.0**. The product and Cargo package are
`openlit`; the installed executable and Rust library import stay `lit`.
The `.lit` directory, `LIT_*` environment variables, `lit://` protocol, and
`lit_*` MCP tools keep their existing names. This is a new release line, not
an upstream 1.8.0 patch. The repository URL remains `mgkwill/Lit` until its
owner explicitly changes it.

## Publication is a separate approval

The current release workflow only builds and tests private candidates. It has
read-only repository permissions and no release, tag, registry, or upload-to-store
step. Pushing a version tag no longer publishes anything. `publish = false` in
Cargo.toml also prevents accidental registry publication.

Before publishing, show the owner the exact source commit, target list, artifact
hashes, signing evidence, release notes, destination, and remaining limitations.
Ask for approval. Do not create a public release, push a release tag, rename the
hosted repository, or publish a package as part of preparation.

## Required for a public binary release

1. Build the same approved source revision on all five native targets. Use the
   lockfile. Check package identity, `lit --version`, the bundled MIT license,
   batch rollback, MCP, and remotes from the extracted archives.
2. Sign macOS executables with the chosen Apple Developer ID Application
   identity and hardened runtime, then submit the distribution to Apple's
   notary service. Keep the accepted result and test the downloaded artifact
   under Gatekeeper. Choose a notarizable distribution container; a raw CLI
   executable cannot carry a stapled ticket. Do not call an ad-hoc signature
   a Developer ID signature. [Apple guidance](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
3. Sign Windows executables with the chosen trusted Authenticode publisher,
   SHA-256, and an RFC 3161 timestamp. Verify the signature under the Authenticode
   policy and test a clean download. A timestamp preserves signing-time evidence;
   it does not guarantee SmartScreen reputation. [Microsoft guidance](https://learn.microsoft.com/en-us/windows/win32/seccrypto/time-stamping-authenticode-signatures)
4. Generate and verify an artifact attestation for every final archive and the
   checksum manifest, tied to the approved repository, workflow, and source
   revision. Use GitHub Actions identity with narrowly scoped permissions.
   Verify the expected signer identity as well as the hash. GitHub's private
   repository attestations require Enterprise Cloud; the private rehearsal must
   not be represented as attestation proof if that feature is unavailable.
   [GitHub guidance](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
5. Compute `SHA256SUMS` after platform signing and final packaging. Repeat the
   archive smoke checks on those final bytes. Keep source, build, signature,
   checksum, and test receipts together. Signing changes binary bytes.
6. Use a separate, protected publication environment with a required human
   reviewer. Grant write permissions only to that approved publication job.
   Pin third-party actions to reviewed full commit SHAs before giving a workflow
   signing credentials. Never expose signing secrets to pull-request builds.

Linux has no required platform-specific executable signature in this policy;
its archives still require verified provenance and checksums. Repository commit
signatures and OpenLit's encrypted storage do not satisfy release signing.

## What is prepared and what is not

Prepared: the version and names, the native build matrix, extracted-package
checks, and a publication-disabled workflow. The new README and guides describe
current behavior rather than inherited marketing claims.

Still required before publication: select and verify the publisher identities;
wire signing and attestation into a reviewed workflow; qualify the signed final
archives; complete the source-provenance and dependency-notice review; archive
reviewed implementation records as required by AGENTS.md; obtain approval.
No signing credentials have been created or used by this preparation. Publisher
identity selection is pending. See [dependency review](DEPENDENCIES.md) for the
remaining maintenance and notice work.

## Candidate testing

A branch named `lit-release-qualification-*` runs `.github/workflows/release.yml`.
The native targets are Linux x86-64/arm64, macOS Intel/Apple Silicon, and Windows
x86-64. Keep qualification in the dedicated private repository until publication
is approved. If that checkout omits unrelated workflows, record both commit IDs
and prove all other tracked files match.

The protected merge sandbox keeps its Actions-only rule. Candidate preparation
must not weaken branch rules, add a bypass actor, or post a manual passing status.

## Release notes

Use [the 0.1.0 draft](../CHANGELOG.md). Link exact evidence for the final candidate.
Do not reuse earlier binary hashes after rebuilding, renaming, or signing.
A successful build is not signing proof, and a signed binary is not a passing test.
