# Privacy-redacted history projection

This repository replaces identifying local paths, private worker names and their
filename variants, an actual deployment address, and a model deployment alias
with neutral placeholders throughout reachable branches, the release tag, file
contents, filenames and commit messages. Generic documentation examples and
standard networking constants remain; they are not the deployment inventory.

## Identities and evidence

The original 161-commit graph is projected one-to-one. 132 commit IDs and 150
blobs changed. The MIT baseline `98c59bb2dbebcf7f575993a4ad89e1300a578c83` is
unchanged. The projected pre-remediation head is `298930a1e82ac2a362a382ea5119ecbc14033fb1`; the projected
release source is `902d7525008524ff2b214089263836032c797a45`. The cleanup/verification commit follows that
projected head and is outside the earlier development transcript boundary.

[history-map.json](history-map.json) preserves original-to-projected commit
identities and changed blob identities without publishing the identifying
replacement table. Historical audit source IDs, receipt IDs, original-record
hashes and historical CI outcomes refer to the original observations. They must
not be relabeled as measurements or signatures of the rewritten commits.

The eight minimized transcript exports retain their exact original bytes and
hashes. The 37,169 records and all 141 original commit-output associations still
verify. Original messages and tool inputs remain private. Public excerpts and
metadata labels are redacted; full reproduction additionally uses the owner-held
redaction policy. The historical inventory keeps original immutable IDs and
redacted text labels; use the projection map for Git traversal.

A private original-history bundle, original transcript prefixes and the exact
redaction policy remain outside the repository under owner-controlled custody.
The published policy hash binds the private policy without exposing its values.
The policy itself must never be committed.

## Verification

All 150 changed blobs were checked byte for byte against the declared privacy
transform. Across all 161 projected commits, Rust source, Cargo manifest/lockfile
and MIT LICENSE are byte-identical to their respective original versions.
No product behavior, dependency version or cryptographic implementation was
changed by the projection. Historical private infrastructure scripts and
qualification labels are examples after redaction, not runnable deployment
configuration or evidence of a new qualification run.

The projection-aware archive verifier validates Git traversal through the map
and retains original commit-output matching against the original record IDs.
The redaction tests cover path/label handling, JSON validity, Windows paths,
idempotence, preserved protocol constants, map tampering and the unchanged MIT
baseline. Git integrity and all reachable blobs, filenames, commit messages and
decompressed historical export versions are checked separately.

```sh
python3 -B audits/evidence/tools/verify_archive.py
python3 -B tests/provenance_export_regression.py
python3 -B tests/provenance_redaction_regression.py
python3 -B audits/privacy/verify_sanitized_history.py
```

The last command verifies structural integrity and the minimized export format.
Full absence-of-identifying-values verification adds the private owner-held
`--policy /path/to/private-policy.json`. Full transcript reproduction adds
`--originals /path/to/private-originals --redactions /path/to/private-policy.json`
to the archive verifier. Original filenames in the manifest identify custody
records after private absolute paths are removed from public reports.

Resolve a historical source ID before traversing sanitized history:

```sh
python3 -B audits/evidence/tools/history_projection.py ORIGINAL_COMMIT_ID
```

## Release and hosted copies

The `v0.1.0` tag is projected to the sanitized source identity. Existing signed
binary archives and their signed manifests retain the originally approved build
identity. They are historical artifacts, not new builds/signatures/CI results
for the rewritten tag. The mapping preserves that distinction. Existing binary
assets and signature bytes are not replaced by this cleanup.

A force push replaces advertised Git refs, not every copy. GitHub cached old
commit views, old CI data and other clones may retain earlier data. The owner
must coordinate cleanup of other clones; use a fresh clone rather than merging
old history. GitHub-hosted cache removal is a separate service-side operation
([GitHub guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)).
This report does not claim a server-side purge or retroactive sanitization of
original signed artifacts.
