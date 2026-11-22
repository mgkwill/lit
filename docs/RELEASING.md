# OpenLit release process

The current OpenLit release is **0.1.1**. The product and Cargo package are
`openlit`; the executable and Rust library are `lit`.

[OpenLit 0.1.1 is published](https://github.com/mgkwill/lit/releases/tag/v0.1.1)
from signed source `777acec2002a69b2429c91a58f9f8371f12724bd`. Its public downloads
match the approved candidate. See the
[publication record](../audits/publication-0.1.1-2026-11-22.md). Future publication
still requires the owner's explicit approval; do not move an existing release tag.

The older 0.1.0 binary assets and signatures still identify their original
source. Its tag points to the privacy-redacted projection; see
[the history mapping](../audits/privacy/README.md). Do not relabel historical
signatures as evidence for a different source revision.

## Release checks

1. Build the same reviewed source revision for Linux x86-64 and arm64, macOS
   Intel and Apple Silicon, and Windows x86-64. Use the lockfile and retain each
   native build receipt.
2. Package `lit` or `lit.exe`, `LICENSE`, `NOTICE.md`, and `THIRD_PARTY_NOTICES.txt`. Check the
   notices and version. Unix archives use `.tar.gz`; Windows uses `.zip`.
3. Sign every archive, `SHA256SUMS`, and `RELEASE.json` with the encrypted
   OpenLit Minisign publisher key kept outside Git. Independently verify with
   the trusted public key. Use the approval-protected publisher workflow or the
   [local signing commands](RELEASE_CREDENTIALS.md).
4. Verify signatures before extraction, then run batch rollback, MCP, remote,
   and native package checks on those final archive bytes. Keep tamper and
   wrong-key rejection evidence too.
5. Review the source, target list, public key, hashes, tests, release notes,
   destination, and limitations with the owner. **Ask before publishing.**

Minisign is the signing requirement for 0.1.1. Apple Developer ID/notarization,
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
they are not publisher-signed releases. The private qualification repository
can still test candidate changes. If a test projection changes workflow files, retain
both commit IDs and verify every other tracked file is identical.

`.github/workflows/publisher-signing.yml` calls that qualification workflow for
the exact `main` revision selected when the owner starts a run. Once all five
targets pass, it waits for the owner's approval of `release-signing`. It then
signs the qualified bytes with the permanent key, checks all signatures and
tamper rejection, and retains a signed candidate for 14 days. Both workflows
have read-only repository permissions. Approval to use the key does not approve
a release or tag; publication still requires a separate decision.

`.github/workflows/platform-signing.yml` preserves the optional native signing
and attestation path, disabled until its separate credentials are configured.
It is not a gate for the Minisign release. The 100latent sandbox's Actions-only
merge rule stays unchanged: no manual statuses or bypass actors.

## Release evidence

[The signed 0.1.1 candidate](../audits/release-0.1.1-2026-11-22.md) passed
regression and native archive checks. The owner subsequently approved publication;
[public download verification](../audits/publication-0.1.1-2026-11-22.md) records
that separate step.

Use [the 0.1.1 release notes](../CHANGELOG.md). Retain exact source and archive
hashes, the trusted publisher public key, signatures, and verification receipts.
Repeat qualification after rebuilding or changing the packaged bytes.

The [publisher-signing evidence](../audits/ci-publisher-signing-2026-11-21.md)
records successful CI signing and independent download verification for source
`67cbd625a372800b7f626de66a43310255c82b82`. The artifact source stays pinned to
that commit even when later documentation commits record the results.

The bounded [provenance review](../audits/provenance-2026-11-20.md) and
[dependency review](DEPENDENCIES.md) describe their recorded scope. Later changes
need their own evidence. Post-MIT upstream code remains a black box.

Publisher-key creation, signing with that actual key, and approval of the final
public release remain separate from successful disposable-key tests.
