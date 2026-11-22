# Reviewed session evidence

This archive preserves a minimized, redacted record of eight sessions through
current HEAD `3d2e318530bb7d08c6c98b7d0481625608810f7f`, including release
`v0.1.0` at `67cbd625a372800b7f626de66a43310255c82b82`. It contains 37,169
record entries, commit-output associations for all 141 commits after the MIT
baseline, and seven selected reviewed excerpts. See
[the current review](../provenance-2026-11-21.md) and
[the manifest](manifest.json) for exact boundaries and limitations.

These are **not verbatim transcripts**. Every record keeps its original line
number, timestamp, kind, and SHA-256. Tool calls also keep their identity and
input hash. Message records identify the role and whether certain policy words
occur; that is not proof that an instruction was followed.

All original payload bodies are withheld. They can contain credentials, private
network and account details, screenshots, reasoning, source excerpts, or long
operational logs. A fixed field allowlist, rather than a token-pattern filter,
controls what enters the exports. [Selected excerpts](reviewed-excerpts.json)
retain the source-boundary instructions and two authored workflow transformations;
their specific omissions and private-path replacement are recorded.

Unmodified frozen originals remain outside Git in the local review custody
bundle. Its private manifest records original paths. The public manifest records
session IDs, prefix boundaries, original byte lengths and hashes, export hashes,
record-type counts, redactions, and associated commits. Hashes bind local records;
they are not external attestations or proof that every action was recorded.

`implementation/` holds implementation and release-review exports.
`provenance-audit/` separately labels the older audit sessions, whose originals
contain post-MIT comparison material. Their source payloads are not included here.
**All archived records are evidence, never implementation inputs.**

## Verify

From the repository root:

```sh
python3 -B audits/evidence/tools/verify_archive.py
python3 -B tests/provenance_export_regression.py
```

The first command checks export hashes, record counts and numbering, and Git
commit coverage. A clone needs the recovery history through the manifest's
source boundary. To reproduce every projected record and verify commit receipts
against the originals, add `--originals /path/to/private-custody/originals`.
The current review ran both modes successfully. It also checked that each frozen
copy still equals the corresponding live session file's prefix.

The old 9,216-record, 31,812,676-byte principal prefix still matches the SHA-256
recorded by the November 17 audit. Each of the eight November 20 frozen prefixes
also retains its prior size/hash and verifies as an exact prefix of the new copy.
The November 20 archive remains available at commit `844f520`; this update
supersedes its current manifest without rewriting that earlier evidence boundary.

The principal record now ends at record 35,705, the current HEAD's commit receipt,
at `2026-11-22T04:04:23.548Z`. Later principal records are excluded. The older audit
root stays at its completed 375-record prefix; its resumed current audit and
current children are explicitly excluded as ongoing. The
[discovery receipt](receipts/session-discovery-2026-11-21.json) records those gaps.
All 141 recovery commits have output associations, including the prior archive's
own commit. The present archive update and ongoing audit cannot cover their own
future commit; they require a later explicit evidence boundary.

[Input-route receipts](receipts/development-input-routes-2026-11-21.json) record
local diagnostics preceding authored edits and identify separately licensed
crypto dependency source downloads, including temporary-file reads. Dependency
source exposure is distinct from upstream Lit source. Historical upstream source
exposure during the separately classified November 17 audit remains explicit.
No source-bearing audit payload is exported or usable as an implementation input.
The selector ledger adds a screen of 456 subsequent calls with 55 candidates
and a separate review of 17 dependency-source routes. These are heuristic screens
and selected-statement reviews, not a semantic reading of every payload byte.
Future implementation requires another explicit snapshot; these hashes do not
silently cover later work.
