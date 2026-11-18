# Lit post-MIT provenance audit — 2026-11-17

**Finding: no substantiated copying or adaptation of post-MIT upstream implementation was found in the audited recovery branch.** Source comparisons and contemporaneous development records support the explanation that the work extends the MIT baseline using observed reference behavior. This is stronger than an ancestry-only check, but it does not prove that no unrecorded manual copying or sufficiently extensive adaptation occurred.

The principal nonbaseline overlap is the sandbox public JSON/CLI interface. The historical record shows those fields/flags being observed through the reference executable before the local edit introducing them. The matching surrounding bodies are inherited MIT code or short serialization/construction idioms; actual confinement implementations differ. No unresolved distinctive implementation-body match was identified by the screens and manual review below.

## Exact scope

| Boundary | Pinned object / observation |
|---|---|
| MIT baseline | `98c59bb2dbebcf7f575993a4ad89e1300a578c83` |
| Upstream license transition | `7ccae9487a1a573ed870c6501fa191f25e2ab501` |
| Audited recovery HEAD | `cfdf7e69aa7f267ebe56c56c33de8bd427f8cd7a` |
| Audited upstream HEAD | `66b2c771e38d3355fba9e0cc77751410d156c23e` |
| Branch | `codex/mit-lit-recovery` |
| Recovery history | 14 commits after the baseline; 84 changed tracked files |
| Upstream history | All 134 commits reachable from the pinned upstream HEAD after the baseline |
| Baseline history | All 19 commits through the MIT baseline, including earlier versions of moved/deleted content |
| Remote verification | Live `git ls-remote https://github.com/nervosys/Lit.git` matched the pinned upstream HEAD; the sole additional advertised branch tip, `a930e44490c83af9cdb9f643979344ca51e737ff`, is already its ancestor |

The recovery implementation was clean at the start of the audit. This audit inspected post-MIT upstream implementation **after** the pinned recovery commit was written and did not alter implementation, tests, dependencies or Git history. This audit added only this report to the repository. A separate active task subsequently changed coordinator/ownership files; those edits are distinct from this audit and are addressed by the supplemental snapshot below.

Baseline [LICENSE](https://github.com/nervosys/Lit/blob/98c59bb2dbebcf7f575993a4ad89e1300a578c83/LICENSE) is MIT; the [transition commit](https://github.com/nervosys/Lit/commit/7ccae9487a1a573ed870c6501fa191f25e2ab501) changes upstream to AGPL/commercial options. This report addresses source provenance from that upstream boundary, not a full third-party dependency license assessment.

## Methods and results

### History and whole-content checks

- Recovery and upstream have merge-base `98c59bb`; the license-transition commit is not an ancestor of recovery HEAD.
- No merge commits occur in the 14-commit recovery range.
- Stable patch IDs of nonmerge recovery and post-baseline upstream commits have no intersection. This detects patch reuse beyond merge ancestry, but cannot exclude partial copying.
- No eligible recovery blob introduced after the baseline is identical to an upstream blob unique to post-baseline history. Inherited MIT blobs are deliberately excluded from this test.

### Cross-file and cross-history similarity screening

Collected eligible unique Git blobs from the entire histories, rather than comparing only current files at identical paths: **318 baseline-history blobs, 519 post-baseline upstream blobs and 114 recovery blobs**. This covers code, scripts, workflow/configuration files and Markdown; it excludes lockfiles, generated GUI schema JSON, unlisted/binary extensions and files over 2 MB. The manifest lists every included blob and path. Markdown participates in line/prose checks, not token checks.

For each comparison, windows present anywhere in the MIT history are removed from the upstream fingerprint index, so moved MIT code does not become a false post-MIT attribution. Recovery blobs are compared against every upstream candidate path, including historical code later removed upstream and historical recovery revisions later replaced.

| Screen | Window | Candidate pairs before manual classification |
|---|---|---:|
| Whitespace-insensitive significant source lines | 4 lines | 33 |
| Tokens, retaining identifiers/literals | 40 tokens | 44 |
| Tokens with identifiers anonymized | 70 tokens | 39 |
| Tokens with identifiers and literals anonymized | 100 tokens | 18 |

The **134 candidate pairs** include repeated versions and overlapping screens; they are not 134 distinct suspected copies. They involve 12 recovery paths. Every candidate was reviewed and assigned a disposition. An additional screen checked individual whitespace-normalized lines of at least 60 characters, including comments/prose, and string literals of at least 22 characters, against the same MIT exclusion corpus. Its three line matches and one literal match were also reviewed.

The tokenizer is a lightweight lexical heuristic, not a Rust parser or a semantic derivation detector. Thresholds are screening choices, not legal or probabilistic cutoffs. Anonymization intentionally produces false positives. Synthetic sanity checks confirmed that the relevant modes preserve matching through identifier changes and, in the final mode, through literal changes. No application runtime tests were needed because no implementation changed.

### Manual classification

| Recovery area | Finding and provenance explanation |
|---|---|
| `commands/sandbox.rs`, `response.rs`, sandbox CLI in `main.rs` | Matching control-array fields, strict flag and empty-vector initialization. Historical binary help/output expose the interface before the recorded local edit. Large matching spans consist of MIT list/init/destroy/dispatch bodies surrounding those few additions. Recovery uses Seatbelt/Bubblewrap and its own bootstrap/readiness checks; upstream uses `hv2-sandbox` and a different options/backend interface. |
| `commands/push.rs`, `fetch.rs`, `swarm.rs`, `merge.rs` | Existing imports, signatures and short object-read patterns with multiline formatting or explicit `LitError` spelling. No distinctive novel algorithm body matched. The cross-file merge/converge match is a short commit-to-tree read pattern also present in the MIT baseline. |
| `commands/checkout.rs` | MIT commit/tree reads adjacent to index construction after changes to checkout. Matching novel windows arise from rearrangement of baseline context; upstream's explanatory comments are absent. Recovery uses its separate worktree materialization code. |
| `core/refs.rs` | MIT HEAD/ref lookup bodies with a short `read_ref_file` call. Helper argument order, error handling and underlying encryption policy differ. Recovery keeps native text refs and can read existing encrypted refs; upstream also implements a single encrypted refs index. No novel helper implementation match found. |
| `core/objects.rs`, `tests/feature_integration_test.rs` | MIT constructors, signing code and tests with one extra field. Ours retains raw Git payload bytes in `git_source`; upstream adds timezone text. Identifier normalization conflates these different additions. |
| `crypto/encryption.rs` | MIT config-save/initialize code near changed boundaries. Recovery key-state accounting is separate from upstream's throttle/cache implementation; no distinctive novel implementation-body match found. |
| Additional single-line/literal matches | Resolve's error text already occurs in MIT source with different line wrapping. `load_pack_index` is an MIT signature moved out of `commands/gc.rs`. `read_packed` is a short descriptive helper signature whose bodies differ. `filesystem-isolation` is an observable control label; no distinctive copied prose was found by the long-line screen. |

Focused structural comparison also found materially different implementations for Git conversion, packing, transaction recovery and encryption lifecycle. Recovery Git import/export delegates object access to Git subprocesses and uses an iterative conversion helper; upstream retains its own object/pack parsing approach. Recovery pack reads use a validated catalog/location representation and versioned encryption format; the pack format and index structure themselves originate in MIT `commands/gc.rs`. Recovery transaction checkpoints and write locks differ from upstream's legacy WAL state. These differences support the finding but cannot independently prove nonadaptation.

## Supplemental in-progress working-tree snapshot

A separate active task changed `tests/github_queue_coordinator_regression.py`, `tools/github_queue_coordinator.py` and new `tools/task_ownership.py` while this audit was running. A read-only snapshot of all three was captured outside the repository, with exact byte lengths, Git blob IDs, SHA-256 hashes and UTC capture time in `working-snapshot.json`. It is included as a separate working corpus in the four source screens and individual-line/literal checks. The source screens produced **no additional candidate matches**. This is a narrower source-similarity finding for the captured bytes, not a complete development-record attestation for that unfinished task or later edits. Generated `tools/__pycache__/` was excluded. A later concurrent edit to `tools/README.md` is outside this three-file snapshot. The three implementation/test files still matched the captured hashes at the final check.

## Development-record corroboration

Historical principal record:

`<private-path>`

Audited prefix: first **9,216 newline-preserved records**, **31,812,676 bytes**, SHA-256 **`b8ecc3fdbae2c0fbf2e16ab2901097608cc98e18b5daef1d6cab20fe46e15d2b`**. Last prefix timestamp is `2026-11-18T05:56:21.817Z` (November 17, Pacific time). Recovery commit `cfdf7e6` is recorded at line 9151 and was committed at `2026-11-17T21:44:40-08:00`.

| Record lines | Evidence |
|---|---|
| 13, 342 and repeated subsequent instructions | Explicit MIT-baseline rebuild using v1.8.0 as a black box. These instructions establish intent, not proof of compliance. |
| 43–46 | Actual reset from `66b2c77` to `98c59bb`, while retaining the old tip as a saved ref. |
| 48, 58, 83, 127 | Reference release retrieval, CLI help and executed behavioral probes. |
| 85, 92 | Reference help/error suggestions and executed sandbox JSON expose the control-array fields and strict behavior. |
| 957–958, 966, 1040 | Reads of the local MIT-derived sandbox/main/response/errors precede a recorded authored edit adding the fields and strict option. No post-MIT Lit implementation-source read was found as an input to that edit. Name validation already exists in MIT sandbox code. |
| 2191, 3803, 4934 | Source reads through Git select recovery HEAD, baseline README or a baseline-descended recovery workflow, rather than upstream post-MIT implementation. |
| 98 | Post-MIT README and architecture documentation were consulted. This is actual documentation exposure, not complete isolation from upstream material. |

The record investigation checked source-acquisition selectors throughout the historical principal record and three discovered recovery review children. Recorded local source reads follow the baseline reset. No saved-post-MIT-ref/origin-master implementation selector, upstream `.rs` source acquisition or recorded manual source import was identified. Testing transfers move recovery code and authored probes to disposable sandboxes/hosts.

Indirect inputs were also examined: `<private-path>`, its compacted transcript, and all **26 persisted tool-output files (approximately 720 KB)**. The docs.rs response contains a module/API landing page and tiny ontology usage examples, with no recorded source-page traversal. One architecture document contains five Rust data-structure sketches. Every shown field/type is already present in the MIT baseline: Blob, Tree/TreeEntry, Commit, Index/IndexEntry and NetworkConfig. No novel post-MIT algorithm body was identified in these persisted inputs. Thus documentation exposure is disclosed and accounted for, without attributing inherited MIT structure to post-MIT work.

## Limits and conclusion

The evidence supports **no detected post-MIT upstream implementation copying/adaptation**, including the manual-copy routes the inspected development record can reveal. The prominent interface matches have affirmative behavioral evidence, not merely an absence of merges.

This does not establish a universal negative. Local sessions are not a complete activity attestation; original external/manual activity, unpersisted/deleted records, unreachable or unadvertised upstream history, copying below the selected thresholds, and sufficiently extensive semantic rewrites may evade this audit. Prior post-MIT source availability is known; no claim of total source isolation is made. Similar features, matching field names and standard serialization idioms alone do not establish source copying. This is an engineering provenance finding, not a legal certification.

Recommended statement:

> We audited the MIT-baseline recovery branch through cfdf7e6 against all post-MIT history reachable from upstream 66b2c77, including cross-file historical source similarities and available development records. We found no substantiated copying or adaptation of post-MIT upstream implementation. The identified overlaps are explained by MIT inheritance, observed public interfaces, or short conventional code patterns. The audit cannot exclude unrecorded copying or adaptations beyond its detection limits.

## Reproduction and retained evidence

Local evidence bundle: `<private-path>`.

- `audit_source.py`: pinned-history collection and four source screens.
- `audit_literals.py`: long-line/literal and whole-blob checks.
- `manifest.json`: exact corpus objects, paths and screen counts, including the separate three-file working snapshot.
- `working-snapshot.json`, `working-snapshot/`: captured in-progress bytes and identity checks.
- `candidates.json`, `candidate-review.md`: retained match locations and excerpts for inspection, kept outside implementation sources.
- `candidate-dispositions.json`: disposition for every candidate pair.
- `literal-matches.json`, `history-checks.json`, `*-patch-ids.txt`: additional checks.
- `development-records.md`: detailed record locations, review children, indirect-input findings and record hash.
- `scanner-self-checks.json`, `SHA256SUMS`: sanity-check results and retained artifact checksums.

Run the scripts with Python 3 on this machine; they invoke read-only Git object commands in `<private-path>` and write results beside themselves. Stable patch IDs are reproducible with `git log --no-merges --format='commit %H' -p BASE..TIP | git patch-id --stable`. History boundaries are pinned in the scripts; do not silently replace them with moving branch names. No merge, publication, dependency install or runtime qualification was performed for this audit.

## Follow-up instructions and transcript archival

Repository policy is now recorded in [AGENTS.md](../AGENTS.md): implement from
the pinned MIT snapshot and independently authored recovery code; treat
post-MIT upstream versions as behavioral black boxes. Source comparisons for
provenance auditing are separate from implementation work.

**Transcript archival is pending until the rewrite is complete.** Preserve the
implementation principal record and the three recovery review-child records
identified in the retained `development-records.md`. Archive reviewed exports
next to this report under `audits/evidence/`, with a manifest of session IDs,
covered commits, UTC record boundaries, original-prefix hashes, export hashes
and redactions. Keep unmodified originals outside Git and remove credentials
and private operational data from committed exports. Mark implementation
records separately from this provenance audit's records, which contain
post-MIT source comparisons. The prefix hash above remains the existing
evidence boundary; raw transcripts have not yet been added to repository
history.

At the instruction/report commit preparation, repository HEAD had advanced to
`b53c7cb` (task ownership/fencing). This report's full history/development-record
finding remains bounded to `cfdf7e6`. Its supplemental three-file snapshot
does not extend that finding to the entire later commit.
