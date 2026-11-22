# OpenLit 0.1.0 publication — November 21, 2026 (Pacific)

[OpenLit 0.1.0](https://github.com/mgkwill/lit/releases/tag/v0.1.0) was published
with the owner's explicit approval. The release is public, is not a prerelease,
and is marked Latest. GitHub recorded publication at `2026-11-22T00:25:06Z`
(November 21 in the owner's Pacific time zone).

## Exact source and downloads

Tag `v0.1.0` points to `67cbd625a372800b7f626de66a43310255c82b82`, the exact
source qualified and signed in
[workflow run 44958839171](https://github.com/mgkwill/lit/actions/runs/44958839171).
Later commits document the results; they do not move the tag or alter the
signed source identity. This is the independent MIT-based OpenLit release,
not an upstream post-MIT release republished under a new license.

Release ID `454083447` has 15 explicitly selected assets: five native archives,
five archive signatures, two manifests with their two signatures, and the
reviewed public key. CI-internal receipts, private keys, passwords, and vault
records are not release assets. GitHub also supplies its standard source
archives for the tagged commit.

Before publication, all uploaded assets were downloaded from the draft and
checked against the approved candidate. After publication, all 15 assets were
downloaded again from public URLs without an authorization header. All seven
Minisign signatures verified with the independently pinned public key, and every
signed file hash matched the approved CI bundle. The public-key file also
matched exactly. The published tag and Latest release were checked through
Git and GitHub's API.

The [publication receipt](publication-0.1.0-2026-11-21.json) records the source,
release identity, timestamps, and verified file hashes. The earlier
[publisher-signing report](ci-publisher-signing-2026-11-21.md) retains the five
native qualifications, six rejection tests, local macOS smoke checks, and
approval history. Those earlier reports describe their own pre-publication
snapshots and have not been rewritten as if publication had already happened.

## Previous-release check

Before creating this release, the authenticated, paginated GitHub release
inventory for `mgkwill/lit` was empty, and `git ls-remote --tags origin` returned
no tags. There were therefore no previous releases to classify or delete under
the owner's instruction to remove non-MIT releases. After publication, this
repository had exactly one release: `v0.1.0`.

This check covers this repository's releases and remote tags. It does not claim
to remove releases in other repositories or audit unreachable Git objects.
No post-MIT upstream source code or tests were inspected during publication.

## Limits

Minisign signatures authenticate the approved download bytes. This release
does not add Apple notarization, Windows Authenticode, SmartScreen reputation,
or independent build attestations. Platform limitations remain documented in
the release notes and installation guide. No registry package was published.

Raw GitHub responses and downloaded verification copies remain outside Git.
The committed receipt contains only public release metadata and file hashes;
it is not an implementation transcript archive or an expanded provenance audit.
