# OpenLit 0.1.0 provenance and development history — November 21, 2026

**This review found no substantiated copying or adaptation of post-MIT upstream
Lit implementation in the examined OpenLit history through `3d2e318`, including
the published `v0.1.0` source.** It combines a fresh historical source comparison,
reviewed development-record evidence and a live release-identity check. It does
not prove the absence of unrecorded copying or extensively rewritten adaptations.

The committable evidence now includes a [160-commit Git history inventory](history-2026-11-21.json),
[minimized session exports](evidence/README.md) covering all 141 OpenLit commits,
a [source-match disposition ledger](source-matches-2026-11-21.json), an
[audit receipt](provenance-2026-11-21.json), and a reproducible audit-only scanner.
The exports are **not verbatim transcripts**: payload bodies are withheld, while
record identities, timestamps, hashes and selected reviewed excerpts remain.
Originals are preserved outside Git for controlled reproduction.

## Exact boundaries

| Item | Identity / scope |
| --- | --- |
| MIT baseline | `98c59bb2dbebcf7f575993a4ad89e1300a578c83` |
| Inherited history | All 19 commits reachable from that baseline |
| Upstream license change | `7ccae9487a1a573ed870c6501fa191f25e2ab501` |
| Compared upstream tip | `66b2c771e38d3355fba9e0cc77751410d156c23e` |
| Upstream comparison history | All 134 commits after the baseline reachable from that tip |
| Published release | [`v0.1.0`](https://github.com/mgkwill/lit/releases/tag/v0.1.0) at `67cbd625a372800b7f626de66a43310255c82b82` |
| Release development history | All 138 OpenLit commits after the baseline through the tag |
| Reviewed current history | All 141 OpenLit commits through `3d2e318530bb7d08c6c98b7d0481625608810f7f` |
| Complete retained Git history | 160 commits: 19 inherited MIT commits plus 141 OpenLit commits |

The live upstream branch inventory still advertises `66b2c77` as `master`; its
only other advertised branch tip, `a930e44490c83af9cdb9f643979344ca51e737ff`,
is already an ancestor of that tip. The comparison does not claim coverage of
unadvertised/unreachable upstream objects or future commits.

This is a requested provenance audit under [AGENTS.md](../AGENTS.md). Post-MIT
source was read for comparison only, after the pinned development work existed.
Source-bearing comparison output stays outside the repository and is never an
implementation input. No product implementation, dependency version, release
tag, published asset or remote protection was changed by this audit. This
report/archive update's eventual commit is outside its own pinned boundary;
a later evidence snapshot is required to associate that commit with records.

## Development chronology

| Phase | Preserved boundary and evidence |
| --- | --- |
| MIT starting point | Original upstream history and MIT notice retained at `98c59bb`. |
| Initial recovery and queue work | `2e23eae` through `cfdf7e6`; the [November 17 audit](provenance-2026-11-17.md) covers its own earlier comparison scope. |
| Provenance policy | `6159a48` records the MIT-source/black-box instructions and initial audit; its receipt is in the separate audit session. |
| Continued implementation and qualification | Through `f9f49c9`, followed by notice fix `7ed82dd`; the [November 20 review](provenance-2026-11-20.md) preserves its bounded recovery-only review, source-input tracing and initial transcript archive. |
| Packaging and release fixes | Native tests, notice integrity, CI fixes, broker readiness, key-rotation flushing, Minisign integration and publisher approval culminate in `v0.1.0` at `67cbd62`. |
| Publication and attribution records | `351d130`, `86777c9` and `3d2e318` record publisher verification, publication and attribution; they do not move the release tag. |

The full inventory records every commit's parents, tree, author, UTC times,
subject and release membership. [Commit-record associations](evidence/commit-records.json)
separately locate actual commit outputs for all 141 OpenLit commits. The 19
inherited MIT commits are Git-history evidence; no claim is made that OpenLit
sessions authored those earlier commits. A commit-output association is not
proof of independent authorship of every byte.

## Fresh source comparison

Unlike the recovery-only November 20 review, this audit actually compares
post-MIT upstream source with the full pinned OpenLit history. It does not
silently extend either older report's conclusions to a new endpoint.

The scanner collected eligible unique blobs across the entire histories:
**318 MIT-history blobs, 519 later-upstream blobs and 708 OpenLit blobs**.
It compares across file paths and includes historical versions later replaced
or deleted. A fingerprint already present anywhere in MIT history is excluded
from the later-upstream novelty index, accounting for inherited/moved code.

| Screen | Window | Candidate pairs |
| --- | --- | ---: |
| Whitespace-insensitive significant lines | 4 lines | 145 |
| Tokens retaining identifiers and literals | 40 tokens | 182 |
| Tokens with identifiers anonymized | 70 tokens | 175 |
| Tokens with identifiers and literals anonymized | 100 tokens | 171 |

All **673 candidate pairs across 44 paths** were classified. These counts
include repeated historical versions and overlapping screens; they are not
counts of copied code. Spans can include surrounding unmatched context. The
lexer is heuristic, not a parser or a semantic derivation detector. This
version corrects the earlier scanner's treatment of Rust lifetimes, so its
counts are not a like-for-like trend measure against November 17.

Coverage includes the explicitly listed code/script/configuration extensions
and Markdown. Lockfiles, generated GUI schemas, binary/media files, unlisted
extensions and blobs of at least 2 MB are excluded. Markdown receives the
line/prose screens rather than token screens. Lockfile/dependency notices have
separate prior evidence; this is not an exhaustive audit of all dependency terms.

### Strongest matches and their resolutions

- **37 identical complete blobs:** each was reproduced byte for byte by running
  `rustfmt` on the corresponding MIT-baseline file. The ledger retains baseline,
  formatted and matching blob IDs plus the formatter version/command. These
  are demonstrably available from MIT source by formatting; their presence in
  later upstream is not evidence of novel upstream implementation being needed.
- **Reformatted baseline code and tests:** multiline calls, trailing commas,
  closures and type signatures create new fingerprints around inherited code.
  The overlapping changes were traced against MIT source. This explains the
  large matches in LFS, reset/stash helpers, binary indexes and command tests.
- **Sandbox interface:** public control fields and the strict option were
  observed through the reference binary before the recorded local edit.
  Baseline init/list/destroy and CLI bodies surround those additions. The
  underlying confinement backends differ. The earlier audit and preserved
  implementation records provide the observation/edit route.
- **Airgap cleanup:** original record 30879 contains a local Clippy unused-method
  diagnostic; authored edits at 30918 and 30935 remove the redundant non-Windows
  helper and gate imports. The result is committed in `2e34a3e`. Matching retained
  mount/removable-drive bodies already exist in MIT source.
- **CI fixes:** records 32595/32604 show the failing command and inspection of
  local workflow/Cargo help before `f467904`. Record 32700 records the fail-fast
  setting edit before `71392a2`. The surrounding CI template is inherited MIT
  code; the small settings/argument-placement changes have recorded local routes.
- **Other small overlaps:** ordinary manifest syntax, inherited ref/HEAD and
  object constructors, short descriptive helpers and baseline tests with local
  extensions. For example, identifier anonymization equates OpenLit's raw Git
  payload field with upstream's different timezone field. No distinctive novel
  implementation-body match remained unexplained in the reviewed candidates.

[Input-route receipts](evidence/receipts/development-input-routes-2026-11-21.json)
retain safe summaries, UTC locations and original-record hashes for the diagnostic
and edit routes. [The ledger](source-matches-2026-11-21.json) preserves every match's
paths, blob IDs, line ranges, excerpt hashes and disposition without committing
post-MIT source excerpts.

An additional screen found 24 shared long-line values and one long literal after
MIT exclusion. Long-line matches are reformatted MIT text or short helper
signatures; the literal is an observable sandbox control label. There are no
shared stable whole-patch IDs. Recovery/upstream merge-base is the MIT baseline,
no upstream license-change commit is reachable from OpenLit, and the recovery
range contains no merge commits. These history checks corroborate the source
review; they do not alone exclude manual copying.

## Development records and custody

The extended archive contains **eight historical sessions and 37,169 minimized
records**. The principal implementation session is frozen at record **35,705**,
through the `3d2e318` commit receipt, ending `2026-11-22T04:04:23.548Z`
(November 21 Pacific). Its original prefix is **125,570,000 bytes**, SHA-256
`ae6653044f22c1077ef12b40d88e89b6069a426b7f608c8814701e7bdf09c36f`.

All eight earlier frozen prefixes still match, including the original
9,216-record prefix hash from November 17. The previous archive remains in Git
at `844f520`; updating the current manifest does not rewrite that boundary.
The current resumed audit and its active child are excluded from the historical
archive. [Session-discovery evidence](evidence/receipts/session-discovery-2026-11-21.json)
records the method, completed sessions and exclusions.

The selector ledger retains the earlier 267 candidate entries and adds 55
candidates from 456 subsequent tool-call inputs plus 17 separately reviewed
dependency-source routes. These are heuristic acquisition/source-selector
screens and selected-statement reviews, not semantic inspection of every
payload byte. The examined selectors concern MIT/recovery source, owned release
artifacts, platform/protocol documentation, signing tools and separately licensed
dependency material. No later post-MIT upstream Lit implementation acquisition
was identified in the screened development statements.

Crypto-notice work did read downloaded pqcrypto/liboqs/NIST source, including
private temporary-file inputs. That exposure is explicitly retained as
separately licensed dependency research, not confused with Lit implementation
copying. Historical public upstream prose-documentation exposure and the
separately classified earlier source-comparison audit also remain disclosed;
there is no claim of total historical isolation from upstream source.

Exports withhold payload bodies through a fixed field allowlist. Seven selected
reviewed excerpts remain separately available. Exact reproduction of every
projected record and source-input re-review requires the preserved originals,
not just the public minimized archive. Hashes establish byte identity with the
local evidence; they are not external attestations or a complete activity log.

## Release identity and attribution

Live GitHub release metadata confirms `v0.1.0` targets the pinned release commit,
is public and is not a prerelease. All **15 advertised asset SHA-256 digests**
match the [committed publication receipt](publication-0.1.0-2026-11-21.json).
This audit checked API metadata; it did not redownload/requalify the binaries
or rerun signature verification. The [publication report](publication-0.1.0-2026-11-21.md)
and [publisher report](ci-publisher-signing-2026-11-21.md) retain those earlier,
separate download/signature/runtime results and limitations.

The tagged release's root LICENSE is byte-identical to the MIT baseline. Current
`3d2e318` adds the contributor copyright line while retaining all original MIT
notice text. [NOTICE.md](../NOTICE.md) distinguishes inherited Nervosys code from
OpenLit contributions. Later attribution/docs changes are not retroactively
claimed as contents of the earlier signed release. Separately licensed
third-party components and fixtures retain their own notices; this finding
is not a statement that every dependency is exclusively MIT.

## Validation and reproduction

Passed: export verification, full original-to-export reproduction, all 141
commit-receipt links, eight old/live-prefix identity checks, original November 17
prefix verification, two export privacy/tamper regressions, all 37 formatter
replays, six scanner sanity checks and metadata/receipt identity checks.
[Archive validation](evidence/receipts/archive-validation-2026-11-21.json) and
[the audit receipt](provenance-2026-11-21.json) preserve the results. Runtime Rust
suites were not rerun for this evidence-only change.

From a clone containing the pinned history:

```sh
python3 -B audits/evidence/tools/verify_archive.py
python3 -B tests/provenance_export_regression.py
```

For a separately requested provenance comparison, use the audit-only script
with an output directory **outside** the repository:

```sh
python3 -B audits/tools/source_history_screen.py \
  --private-output /path/outside/repository/source-audit \
  --baseline 98c59bb2dbebcf7f575993a4ad89e1300a578c83 \
  --upstream 66b2c771e38d3355fba9e0cc77751410d156c23e \
  --head 3d2e318530bb7d08c6c98b7d0481625608810f7f
```

The upstream objects must be available locally; the script makes no network
requests and changes no Git objects or product source. Full transcript
reproduction adds `--originals /path/to/private-custody/originals` to the archive
verifier. Formatting receipts can be reproduced by piping each listed baseline
blob to the recorded `rustfmt` command and hashing the output as a Git blob.

Raw comparison output and private custody files remain outside Git. The public
receipt records their hashes; no source-bearing audit payload is an implementation
input. This engineering audit cannot exclude missing/deleted records, external
manual work, indirect inputs outside retained evidence, short copies below the
screens or sufficiently extensive semantic adaptations. It supports the stated
bounded finding, not a legal certification or a clean-room claim.
