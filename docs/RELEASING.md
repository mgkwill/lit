# Prepare OpenLit 0.1.0

The first OpenLit version is **0.1.0**. The product and Cargo package are
`openlit`; the executable and Rust library name stay `lit`. This is an independent
release line built from the MIT baseline, not an upstream 1.8.0 patch.

## Required for the first release

1. Build the same reviewed source revision for Linux x86-64 and arm64, macOS
   Intel and Apple Silicon, and Windows x86-64. Use the lockfile and retain each
   native build receipt.
2. Package `lit` or `lit.exe`, `LICENSE`, and `THIRD_PARTY_NOTICES.txt`. Check the
   notices and version. Unix archives use `.tar.gz`; Windows uses `.zip`.
3. Sign every archive, `SHA256SUMS`, and `RELEASE.json` with the encrypted
   OpenLit Minisign publisher key kept outside Git. Independently verify with
   the trusted public key. See [key setup and commands](RELEASE_CREDENTIALS.md).
4. Verify signatures before extraction, then run batch rollback, MCP, remote,
   and native package checks on those final archive bytes. Keep tamper and
   wrong-key rejection evidence too.
5. Review the source, target list, public key, hashes, tests, release notes,
   destination, and limitations with the owner. **Ask before publishing.**

Minisign is the signing requirement for 0.1.0. Apple Developer ID/notarization,
Windows Authenticode, and GitHub artifact attestations are optional later work.
Minisign does not remove OS trust warnings or prove how a binary was built.
Commit signatures and OpenLit's encrypted repository storage are separate.

## Preparation does not publish

The qualification workflow has read-only repository permissions and no release,
tag, registry, or store publication step. A version tag does not publish
anything. `publish = false` in Cargo.toml prevents accidental registry publication.
Do not create a public release or push a release tag without approval.

The manual workflow `.github/workflows/release.yml` builds fresh candidates on
all five native targets. It also runs for `lit-release-qualification-*` branches.
It signs with disposable test keys, rejects tampered copies, and tests the
extracted programs. The signed metadata marks these as qualification artifacts;
they are not publisher-signed releases. Keep these runs in the private
qualification repository. If a test projection changes workflow files, retain
both commit IDs and verify every other tracked file is identical.

`.github/workflows/platform-signing.yml` preserves the optional native signing
and attestation path, disabled until its separate credentials are configured.
It is not a gate for the Minisign release. The 100latent sandbox's Actions-only
merge rule stays unchanged: no manual statuses or bypass actors.

## Release evidence

Use [the 0.1.0 release notes](../CHANGELOG.md). Retain exact source and archive
hashes, the trusted publisher public key, signatures, and verification receipts.
Repeat qualification after rebuilding or changing the packaged bytes.

The bounded [provenance review](../audits/provenance-2026-11-20.md) and
[dependency review](DEPENDENCIES.md) describe their recorded scope. Later changes
need their own evidence. Post-MIT upstream code remains a black box.

Publisher-key creation, signing with that actual key, and approval of the final
public release remain separate from successful disposable-key tests.
