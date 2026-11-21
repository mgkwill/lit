# OpenLit provenance and transcript review — 2026-11-20

The available development records and the recovery-only source review found no
recorded import or substantiated copying of post-MIT upstream Lit implementation
in the examined work. This is a bounded engineering finding. It does not prove
that unrecorded manual copying or extensive adaptation never happened.

The two crypto-notice gaps are resolved at `7ed82dd4c3a16f8f93cb84f9f5c7366adf1dcdd3`.
The reviewed transcript exports are now archived and verified, rather than left
as a future task. They are deliberately minimized exports, not public verbatim
transcripts. The unaltered originals remain outside Git.

## Boundaries

| Item | Boundary |
| --- | --- |
| MIT baseline | `98c59bb2dbebcf7f575993a4ad89e1300a578c83` |
| Earlier source-comparison audit | Through `cfdf7e69aa7f267ebe56c56c33de8bd427f8cd7a` only |
| New recovery-only source review | 105 later commits through `f9f49c9503c043bb51228a347f43db7a4f1fad95` |
| Notice fix and original-record snapshot | Through `7ed82dd4c3a16f8f93cb84f9f5c7366adf1dcdd3` |
| Archived records | Eight sessions, 33,515 records; exact UTC bounds in the manifest |
| Commit-output coverage | All 120 baseline-descendant commits through the notice fix |

The existing [November 17 audit](provenance-2026-11-17.md) was read as required.
Its raw upstream comparison excerpts and scripts were not used as implementation
inputs, and its comparisons were not rerun. No post-MIT upstream implementation
was opened during this review. The [new source review](recovery-source-review-2026-11-20.md)
examines only the MIT baseline and recovery changes; it must not be represented
as extending the older similarity audit to the new endpoint.

## Development-record review

The original principal session and three recovery review children were discovered
from local session metadata and checked against the earlier audit's identifiers.
Two older provenance-audit sessions are separately classified. This review's
crypto-notice and recovery-source children are also preserved. The current
principal record was frozen immediately after the notice-fix commit receipt.

For the historical implementation prefix through `f9f49c9`, the review scanned
4,095 tool-call inputs for explicit upstream names/routes, saved refs, Git object
reads, archives, clones, fetches and source selectors. The primary screen had
134 candidate calls; a supplemental array-form Git/documentation/acquisition
screen had 133. These are heuristic source-input screens, not 267 findings of
copying and not a manual reading of every payload byte.
[The disposition ledger](evidence/source-selector-review.json) preserves record
locations and input hashes without publishing private commands.

The flagged selectors refer to MIT/recovery source, test repositories, sandbox
projections, protocol/platform/dependency documentation, metadata, or authored
reports. Public post-MIT README/architecture exposure before the earlier audit
is retained explicitly at principal record 98; the earlier audit already examined
its inherited data-structure sketches. We do not claim total historical isolation
from upstream material. No later post-MIT Lit implementation acquisition was
identified in the screened records.

All 119 recovery commits through `f9f49c9` have Git commit-output associations.
The policy/report commit `6159a48` is in the older audit session at record 369,
not in the implementation session. The notice-fix receipt adds the 120th commit.
[The index](evidence/commit-records.json) records each commit, UTC time, session,
and record location. A receipt establishes a record association; it is not proof
of authorship of every byte.

### Traced sandbox source inputs

The review checked the selected Rust/Cargo blobs read from three pinned sandbox
seeds. They match blobs in allowed recovery history. One workflow blob did not:
`0b7ff912bbf19e0c5d0eceb3dd8dec734ac4edd1`.

Principal records 4744 and 4865 contain the authored workflow transformations.
Replaying those edits from recovery commit `bafdc653` reproduced the exact blob,
including the intermediate `a81e4ebdaaad92df2fe910ef2c39a3908e90e12b`.
The independent source reviewer reproduced the same result. This supplies an
affirmative recorded authorship route for that selected projection; ancestry
alone would not have resolved it. [Identity receipts](evidence/receipts/seed-source-identities.json)
and [replay receipt](evidence/receipts/seed-workflow-replay.json) preserve the result.
Unlisted sandbox files are not preapproved by this finding.

## Transcript custody and redactions

[The archive](evidence/README.md) and [manifest](evidence/manifest.json) cover
original record counts, prefix hashes, UTC bounds, export hashes, classifications,
commit associations, and redactions. All 33,515 entries were regenerated from
the frozen originals and compared. Every frozen copy also still matched its
live session file's prefix. The old 9,216-record principal prefix remains exactly
`b8ecc3fdbae2c0fbf2e16ab2901097608cc98e18b5daef1d6cab20fe46e15d2b`.

The exports omit all payload bodies by default, including tool results and
source-bearing audit material. Seven separately reviewed excerpts retain the
key instructions and workflow edits, with explicit omissions. This protects
credentials and private operational details, but limits what an independent
reader can reconstruct from Git alone. Full re-review requires controlled access
to the preserved originals. Hashes do not make local sessions a complete or
externally authenticated activity log. Missing/deleted sessions and unrecorded
manual activity remain outside the evidence.

## Notices, branch deletion, and current controls

The [crypto review](crypto-notices-2026-11-20.md) ties the two package declarations,
embedded source notices and NIST permission to exact published dependency inputs.
All 246 resolved packages now have collected notice text. This does not change
the remaining security advisory or maintenance warnings and is not a worldwide
legal certification. The notice fix changes distribution metadata and its
collector/tests; Rust source, Cargo manifest/lockfile, and root MIT LICENSE are
unchanged from `f9f49c9`.

At the owner's explicit request, the local backup branch
`codex/pre-mit-reset-2026-11-16` was deleted. Its former tip was `66b2c77`.
The active recovery branch was preserved. Deleting that ref is not a claim that
all old objects or other references were purged, and does not alter the historical
exposure record.

A fresh read verified the sandbox's active Actions-only rule: required
`Agent workflow candidate` check from integration 15368, pull requests and merge
queue required, and no bypass actors. The ruleset matched the prior qualification
snapshot. This task did not change remote protections or post passing statuses.
Historical bootstrap operations are not relabeled as never having happened.

## Validation and next release gates

The notice-integrity tests, documented examples, complete local notice regeneration,
export privacy/tamper tests, full original-to-export reproduction, commit-record
links, and unchanged-source/license checks passed. Runtime Rust suites were not
repeated for notice and audit tooling changes. Older unsigned archives do not
contain this notice fix and must not be published as the final distribution.

Next: choose the publisher identities, configure signing/notarization and verified
Actions attestations, build fresh archives with the final notices, test those
signed bytes, and present the exact release for owner approval. No public release,
release tag, package publication, or hosted repository rename occurred. The archive
commit and subsequent work are outside the frozen implementation snapshot; later
implementation requires updated evidence rather than inheriting this conclusion.
