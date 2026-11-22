# OpenLit 0.1.1 publication — November 22, 2026 (Pacific)

[OpenLit 0.1.1](https://github.com/mgkwill/lit/releases/tag/v0.1.1) was published
with the owner's explicit approval. Release `454614985` is public, is not a
prerelease, and is marked Latest. GitHub recorded publication at
`2026-11-22T17:21:43Z` (9:21 a.m. Pacific).

## Exact source and assets

Tag `v0.1.1` points to `777acec2002a69b2429c91a58f9f8371f12724bd`, the exact
source qualified and signed in
[run 45045042335](https://github.com/mgkwill/lit/actions/runs/45045042335).
The source identity in the signed `RELEASE.json` matches that tag. Later
commits record evidence and update documentation; they do not move the tag,
rebuild the binaries, or change the signed identity.

There are exactly 15 uploaded assets: five native archives, seven detached
Minisign signatures, `SHA256SUMS`, `RELEASE.json`, and `openlit.pub`. GitHub also
provides its standard source archives for the tag. CI-internal receipts,
private keys, and passwords are not release assets. Existing 0.1.0 assets and
signatures were left unchanged.

## Public download verification

Before publication, all 15 draft assets were downloaded and matched against
the approved candidate hashes. GitHub's server-reported asset digests matched
as well. Only after that check was the draft made public.

After publication, every asset was downloaded again from its public URL using
HTTPS without an Authorization header. All 15 hashes matched the approved
bytes. The downloaded public-key file matched the independently trusted key,
with SHA-256
`020727c93e30003cbab9f902459465e55ed6db37e951b2d006b9ec95ac4d77c3`.
All seven signatures verified against that trusted key. Anonymous API checks
confirmed the tag source, published state, asset count, and Latest release.

The full verifier was rerun on the public downloads. It rejected a wrong key,
wrong source, altered archive, altered checksums, altered release identity,
and damaged signature. These tests modified copies and preserved the original
public downloads.

Every archive contains the executable and `LICENSE`, `NOTICE.md`, and
`THIRD_PARTY_NOTICES.txt`. These were compared directly with the pinned source.
Windows `LICENSE` and `NOTICE.md` have equivalent CRLF line endings; its
third-party notices and all Unix notices are byte-identical to the source.

The public Apple Silicon archive also passed native batch rollback, MCP,
remote, version, and package checks on the local Mac. The test receipt's `head`
identifies the checking checkout (`d37505a`); its verified Minisign source
identifies the binary build (`777acec`). Other platforms retain their native
qualification evidence because all published archive hashes match those tested
bytes; publication did not rebuild them.

[The publication receipt](publication-0.1.1-2026-11-22.json) records the public
asset URLs, IDs, sizes, hashes, timestamps, signature and tamper results, notice
checks, and local native receipt. [The qualification record](release-0.1.1-2026-11-22.md)
retains the earlier regression results and signing approval. Its pre-publication
snapshot has not been rewritten to imply that publication had already happened.

## Documentation and limits

The README, installation and quick-start guides, release notes, project summary,
roadmap, and signing guidance now identify 0.1.1 as published. Download examples
and the trusted-key link use this release. Current capability limits remain
unchanged. Documentation examples and local guide links were checked against
`lit 0.1.1`.

Minisign authenticates the approved bytes; it does not add Apple notarization,
Windows Authenticode, SmartScreen reputation, or independent build attestations.
No registry package was published. Post-MIT upstream implementation and tests
were not inspected or imported. Actions-only merge rules were not changed.

Raw API responses and verification copies remain outside Git. This report and
its receipt contain public metadata rather than private deployment identifiers
or custody paths. They do not extend the archived implementation-transcript
boundary or claim a new provenance audit.
