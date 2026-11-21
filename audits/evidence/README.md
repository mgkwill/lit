# Reviewed session evidence

This archive preserves a minimized, redacted record of eight sessions through
notice-fix commit `7ed82dd`. It contains 33,515 record entries, commit-output
associations for all 120 commits after the MIT baseline, and seven selected
human-reviewed excerpts. See [the review](../provenance-2026-11-20.md) and
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
recorded by the November 17 audit. Current-session material after the frozen
notice-fix boundary and the archive's own commit are outside this snapshot.
Future implementation requires another explicit snapshot; these hashes do not
silently cover later work.
