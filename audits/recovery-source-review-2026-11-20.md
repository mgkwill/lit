# Recovery source-input review — 2026-11-20

## Finding and exact boundary

This bounded review found **no affirmative indicator of post-MIT upstream Lit
implementation being imported into the examined recovery changes**. It verifies
the preserved MIT notice, screens the recorded recovery history for source-input
indicators, and inspects selected new implementation and source-loading paths.
It is not a new similarity comparison against post-MIT upstream and does not
prove the absence of unrecorded copying or extensive adaptation.

| Boundary | Exact identity |
|---|---|
| MIT baseline | `98c59bb2dbebcf7f575993a4ad89e1300a578c83` |
| Earlier full audit endpoint | `cfdf7e69aa7f267ebe56c56c33de8bd427f8cd7a` |
| Reviewed recovery endpoint | `f9f49c9503c043bb51228a347f43db7a4f1fad95` |
| Reviewed endpoint tree | `d83c0a2f25632991dfa927f125aa43a5b495a466` |
| Branch observed | `codex/mit-lit-recovery` |
| Incremental range | 105 commits, 262 changed tracked paths |
| Range commit-time boundaries, UTC | `2026-11-18T06:13:25Z` through `2026-11-20T21:17:18Z` |

All implementation observations below refer to that frozen endpoint or its
specified recovery history. Concurrent notice changes and this report are
outside that endpoint. No implementation, tests, dependency versions, or Git
history were changed by this review. No publication or runtime qualification
was performed.

The [November 17 audit](provenance-2026-11-17.md) remains the source-comparison
finding for its own pinned revisions. Its report was read; its raw comparison
corpus, comparison excerpts, audit-session payloads, and post-MIT upstream source
were not read or rerun. The current review follows [AGENTS.md](../AGENTS.md)
and keeps post-MIT upstream, including v1.8.0, a black box.

## Inputs and method

The primary inputs were local Git commit metadata, diffs between the specified
recovery revisions, source at the frozen recovery endpoint, the unchanged MIT
baseline license, and two sanitized record indexes supplied by the coordinating
review. Revision admission used the prior audit's exact boundary and the
recorded recovery commit sequence; ancestry was checked as corroboration, not
treated as proof of independent authorship.

1. Enumerated every commit and changed path in the incremental range. There are
   no merge commits. The endpoint diff has 75,961 additions and 11,303 deletions;
   much of it is retained qualification data and dependency notices. The Rust
   `src/` changes cover 49 paths, with 1,756 additions and 361 deletions.
2. Screened every added line in the 105-commit history for `src/`, `tools/`,
   `tests/`, Cargo manifests/lockfile, workflows, and installers. The serialized
   history inspected by that screen is 71,837 lines / 3,265,147 UTF-8 bytes.
   Indicators included the upstream owner, known upstream revision prefixes,
   upstream Lit documentation/crate source routes, raw GitHub source URLs,
   copying/adaptation phrases, and AGPL references. This was a literal indicator
   screen, not semantic comparison or a complete manual reading of every line.
3. Screened the changed files at the endpoint for those indicators plus source
   revision markers and v1.8.0 references. Read selected recovery diffs and
   source-loading paths manually, especially migration, packed data handling,
   Git transport, workspaces, Pi seeds/workloads, notice collection, and release
   packaging. Reviewed external acquisition selectors in tools, worker fixtures,
   installers, and workflows.
4. Checked all full 40-character Git values stored under keys containing
   `source` in changed JSON qualification fixtures against recovery ancestry:
   29 occurrences, 27 distinct revisions, all present. Other identifiers such as
   sandbox Git bases, binary hashes, and object hashes are different evidence
   classes and were not silently counted as recovery-source revisions.
5. Checked the frozen lockfile and dependency inventory separately from Lit
   implementation provenance, and compared root `LICENSE` bytes to the MIT
   baseline.

The coordinating-review indexes and replay evidence read here were:

| Index | SHA-256 of bytes examined |
|---|---|
| `commit-records.json`, initial input | `8b1f7112bda9721c7bb7f78818f3dcb1501d64722460e6e6314eaa1c343a0d3f` |
| `commit-records.json`, completed audit-record mapping | `8a1cffea27d96cd3e2dc30b6248981b55fdac658d31905512eba1f17de5e90f6` |
| `seed-source-identities.json` | `ea7b49b6ae12704bc67c00bb2e04f8726fa2311d56de8b82c70e4183a204315b` |
| `seed-workflow-replay.json` | `687773a10f4e2bff5c815c2f750a9a5b58b9e0a51755a3633fcf46b19777aa04` |
| `replay_seed_workflow.py` | `7123e43fa054c88f0d57011f3fd3a55eee30e5879a43eafa4d2c0454a58adbf8` |

They were supplied from the local review evidence directory
`<private-path>`. They identify
records and Git objects without embedding post-MIT upstream implementation.
The original session payloads were not independently inspected by this source
review; transcript review and redacted archival are separate work.

## Findings

### MIT attribution and source history

Root [LICENSE](../LICENSE) is byte-identical to the MIT baseline, with SHA-256
`4448dab94f2c8366b38f763128ba73f0a99b631607187a8efd591e3c06c4ccca`.
[Cargo.toml](../Cargo.toml) retains Nervosys in the authors list, declares MIT,
adds OpenLit contributors, and points package metadata at the recovery project.
The [release workflow](../.github/workflows/release.yml) packages `LICENSE` and
`THIRD_PARTY_NOTICES.txt` alongside each executable. These are source and
packaging-rule observations, not verification of a newly built archive.

The initially supplied commit index associates 104 of the 105 incremental commits with
records in implementation session
`01a1e52b-d71a-7fd0-9edc-6d82560ebd8f`. The exception is
`6159a48b949167f5304b276c740e98cddfcc123f`, the separate provenance policy/report
commit. The completed index was subsequently read and hashed: it locates that
commit in audit session `01a1ed36-7f65-7033-9af9-2b9075365bc7`, record 369,
at `2026-11-18T06:59:51.431Z`. Thus the final index maps all 105 incremental
commits (and all 119 baseline-descendant commits), while preserving the separate
audit and implementation record classes. A recorded commit occurrence
establishes a record association, not authorship of every byte in the commit.

The 29 source-revision fixture occurrences all identify recovery history.
Their older dates and hashes are historical qualification boundaries; this
review found no basis to relabel those runs as tests of the newer endpoint.
The check does not validate every command, binary digest, or success claim in
those fixtures.

### Implementation and behavioral inputs

| Area | Source observation and classification |
|---|---|
| Repository migration | [`migrate_repo.rs`](../src/commands/migrate_repo.rs) adds snapshot receipts, separate destination publication, validation, and rollback using local recovery helpers. [`repository_migration_regression.py`](../tests/repository_migration_regression.py) constructs repositories through supplied binaries and inspects their resulting data. The recorded reference inputs are CLI behavior and emitted repository fixtures, not upstream implementation. The deliberately seeded reflog uses the recorded MIT-derived schema. |
| Legacy packed data | [`pack.rs`](../src/storage/pack.rs) documents a v1.8.0 executable observation: encrypted entries in a v1 pack. The new branch authenticates these entries through the existing recovery encryption manager. This is a compatibility requirement derived from data behavior; the comment is not evidence of an upstream source read. |
| Authenticated Git | [`git_remote.rs`](../src/commands/git_remote.rs) extends recovery subprocess transport with explicit URL handling and scoped authentication. [`transport.rs`](../src/network/transport.rs) delegates native URL decoding to the separately licensed `url` dependency. These paths operate on user repositories and do not contain a fixed acquisition route for post-MIT Lit source. |
| Private workspaces | [`workspace.rs`](../src/commands/workspace.rs) builds a new repository from a pinned object graph, reuses recovery verification/materialization, and carries relevant Git identity mappings. The source population is from the caller's repository, not a bundled upstream Lit implementation. |
| Pi/broker seed loading | [`task_broker_workspace.py`](../tools/task_broker_workspace.py) checks a supplied Git base before importing it. [`pi_pilot_lab.py`](../tools/pi_pilot_lab.py) fetches the pinned base from a configured forge, and the worker validates its recorded Git/Lit base. These safeguards verify identity; they do not automatically approve source provenance. The selected source must still be admitted under repository policy before an agent reads it. |
| Pi workloads | [`pi_source_cases.py`](../tests/fixtures/github-merge-queue/pi_source_cases.py) defines bounded arithmetic/policy contracts. [`pi-workspace-worker.py`](../tests/fixtures/github-merge-queue/pi-workspace-worker.py) restricts Rust workloads to approved modules and passes the selected seed file to the model. Seed-file provenance therefore matters even if the eventual result is merely a test. |
| MCP/schema | The examined recovery diff aligns discovery fields with local dispatch and ontology behavior. Public protocol/schema names alone do not establish copied implementation. No post-MIT acquisition selector was found in the screened history. |
| README image | [`docs/images/README.md`](../docs/images/README.md) records the generated-artwork prompt and identifies an external retained original. This is an asset record, not upstream source. The generation session and original image were not separately verified here. |

The historical indicator hits were explainable attribution, behavioral
references, local operational copying, and an independently licensed
Wycheproof URL. No explicit post-MIT upstream Lit source download, code import,
or copying attribution was identified by these screens. Absence of such text
does not exclude acquisition through an unrecognized route.

### Sandbox source projections

The seed identity index names three sandbox projections:
`2f9f55b9ab09d6094b25d9a9f48f4d9d49e7e4c2`,
`44417d32877fc5dbd937eb348fbf6b80b4fd986f`, and
`57910ab4cf28b8807a627f8ffdd23dc75a0bc3c8`.
Its selected Cargo and Rust source blobs are present in allowed recovery
history. Those matching blob identities provide more specific evidence than
the sandbox commit's ancestry alone; they do not establish the provenance of
every unlisted file in each sandbox tree.

One selected workflow blob, `0b7ff912bbf19e0c5d0eceb3dd8dec734ac4edd1`, is
not present in the recovery history. The coordinating transcript reviewer
identified it as a sandbox-authored workflow projection, with recorded creation
through commits `ff290cf6c0082c0a5e6d01a4307df908233eb9a2` and
`42b96b93441cc8f57d8f0598d6e8ce8cd32a87d7`. The supplied replay records authored
edits at principal records 4744 and 4865, starting from the allowed recovery
workflow at `bafdc65301ae2980e3ce4f7e0eeb7a517d263fe4`.

This reviewer independently executed the non-writing reconstruction portion of
the supplied replay script against that admitted recovery object. It reproduced
intermediate blob `a81e4ebdaaad92df2fe910ef2c39a3908e90e12b` and the exact
workflow blob used by both seed inputs, `0b7ff912bbf19e0c5d0eceb3dd8dec734ac4edd1`.
The replay therefore resolves this selected blob's identity route with specific
recorded authored transformations, rather than an ancestry assumption. The
raw transcript records themselves remain the coordinating reviewer's evidence
responsibility. This finding does not preapprove unlisted sandbox-tree files.

### Separately licensed dependencies and test data

The frozen `Cargo.lock` contains 246 registry source entries, all using the
crates.io registry identifier, and no Git-source dependency. The corresponding
inventory records 246 packages; no Lit upstream package/source dependency was
identified. Dependency updates and separately licensed source use are not
post-MIT Lit implementation imports.

The [signature fixture record](../tests/fixtures/cryptography/README.md) pins
Wycheproof revision `65ca494727a52944c204441c860b039e6786b99b` and includes
`LICENSE-Wycheproof`. The small probe uses the pqcrypto public API; it is not an
upstream Lit test. This review verified the local record and harness, not a
fresh upstream download of the vector corpus.

At the frozen endpoint, the inventory explicitly lists missing notices for
`pqcrypto-internals@0.2.11` and `pqcrypto-traits@0.3.5`. The parallel notice
review addresses those gaps after this endpoint; this report does not claim
they were already resolved in `f9f49c9`. The inventory also includes
`option-ext@0.2.0` under MPL-2.0 and two `r-efi` versions with an LGPL option
alongside MIT/Apache alternatives. Such dependency terms must be assessed on
their own scope; they are neither evidence of post-MIT Lit copying nor a claim
that all bundled code is solely MIT. A declared license and a collected notice
are not a completed distribution-compliance determination.

## Reproduction and limitations

The local evidence directory above retains `recovery-source-review.py` and
`recovery-source-review-results.json`. The script reads only admitted recovery
objects and writes counts, source-revision checks, indicator locations with
line hashes, lockfile source classes, and evidence identities. It does not
retain implementation excerpts or consult upstream source. The captured script
SHA-256 is `0f9d6b9d1819bc1bf2acf7211f52093df936aa27ffb3f35f59e6debd5b99af01`;
the result SHA-256 is
`5acf64f7f1ebb22d438f2e8db603c6171981c6270f99e09ef38a4290639a7212`.

Use the full endpoints above, not moving branches, for reproduction:

```sh
git rev-list --count cfdf7e69aa7f267ebe56c56c33de8bd427f8cd7a..f9f49c9503c043bb51228a347f43db7a4f1fad95
git rev-list --merges cfdf7e69aa7f267ebe56c56c33de8bd427f8cd7a..f9f49c9503c043bb51228a347f43db7a4f1fad95
git diff --stat cfdf7e69aa7f267ebe56c56c33de8bd427f8cd7a f9f49c9503c043bb51228a347f43db7a4f1fad95
git diff --exit-code 98c59bb2dbebcf7f575993a4ad89e1300a578c83 f9f49c9503c043bb51228a347f43db7a4f1fad95 -- LICENSE
```

The historical added-line screen can be reproduced with the following
read-only Python procedure. Results identify candidates for classification;
they are not a stand-alone independence test.

```python
import re
import subprocess

base = "cfdf7e69aa7f267ebe56c56c33de8bd427f8cd7a"
head = "f9f49c9503c043bb51228a347f43db7a4f1fad95"
paths = ["src", "tools", "tests", "Cargo.toml", "Cargo.lock", ".github",
         "install.sh", "install.ps1"]
history = subprocess.check_output(
    ["git", "log", "--reverse", "--format=COMMIT:%H", "--no-ext-diff",
     "-p", base + ".." + head, "--", *paths], text=True)
pattern = re.compile(
    r"nervosys|docs\.rs/lit|crates\.io/crates/lit|66b2c77|7ccae948|"
    r"raw\.githubusercontent|copied from|adapted from|ported from|"
    r"upstream source|upstream implementation|AGPL", re.I)
commit = path = ""
seen = set()
for line in history.splitlines():
    if line.startswith("COMMIT:"):
        commit = line[7:]
    if line.startswith("+++ b/"):
        path = line[6:]
    if (line.startswith("+") and not line.startswith("+++")
            and pattern.search(line) and (path, line) not in seen):
        seen.add((path, line))
        print(commit, path, line)
```

The literal searches used the admitted recovery objects only. Do not rerun the
earlier report's upstream comparison scripts as part of implementation work.
No new post-MIT source-similarity corpus was collected, and no comparison
against upstream implementation was performed in this review.

The finding is bounded by the selected indicators, paths, manual inspection,
and available record indexes. It cannot exclude manual copying outside
recorded sessions, hidden/deleted records, indirect acquisition through an
unrecognized source, or sufficiently rewritten adaptations. This source review
also does not independently verify the complete session export, model input
history, seed-tree coverage, dependency legal obligations, or binary builds.
The transcript archive and its manifest supply distinct evidence and must be
checked separately. The earlier source-comparison finding must not be silently
extended from `cfdf7e6` to this endpoint.
