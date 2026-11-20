# Run and migrate OpenLit

For a local installation, start with [installation](docs/INSTALL.md). The CLI
alone does not deploy a broker, register agents, or configure GitHub rules.
For hosted teams, use the [broker operations guide](tools/deployment/README.md).

## Run local services

`lit mcp-serve --stdio` serves an MCP client over standard input and output.
`lit mcp-serve --port 8385` provides a loopback HTTP endpoint. The REST service
uses `lit serve --port 3000` and accepts a bearer token through `LIT_API_TOKEN`.
Keep tokens out of shared logs. These local services do not replace the broker's
per-worker identities or task ownership checks.

## Copy a native repository safely

Native migration is available on macOS and Linux. Stop source writers first,
keep the original in place, and choose a new destination outside it:

```sh
lit migrate-repo inspect /absolute/source
lit migrate-repo copy /absolute/source /absolute/new-copy
```

Migration builds a private staging copy and verifies it before an atomic
no-replace publication. It checks paths, entry types, file bytes, ordinary POSIX
modes, and symlink targets. It preserves history, refs, index, working files,
and local metadata, including LFS data already present.

Ownership, timestamps, ACLs, extended attributes, and hardlink relationships are
outside the snapshot. Symlinks inside `.lit`, special files, and active operations
are rejected. Use a trusted destination parent. The copy receives a format receipt
in `.lit/repository-format.json`; unknown receipt versions are rejected.

Repeat the same copy command after interruption only against the same source
snapshot. To return to the original:

```sh
lit migrate-repo rollback /absolute/source /absolute/new-copy /absolute/archive
```

Rollback verifies that the original still matches the receipt, then moves the
migrated repository and its later work to a new sibling archive path. Resume
work at the original. It does not merge new work back automatically.

## Encrypted migration

Supply the current `--passphrase-file` and use a repository-local key under `.lit`.
Inspection reports whether rotation is needed. A legacy key or v1 encrypted pack
requires `LIT_NEW_PASSPHRASE_FILE` during copy. The copy receives a managed key
and encrypted v2 packs. Migration can convert observed encrypted reference tables
to native text refs, so review metadata confidentiality before using the copy.

Keep both old and new key material with the backups they unlock. See
[encryption](ENCRYPTION.md) and the exact migration evidence in
`tests/fixtures/repository-migration/`. Black-box migration tests do not imply
support for every future upstream format.
