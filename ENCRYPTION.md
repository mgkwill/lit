# Encrypt stored repository data

OpenLit can encrypt stored objects with AES-256-GCM. This protects data at rest;
files checked out for editing remain readable to programs with filesystem access.
The first OpenLit release is not a validated cryptographic product.

## Use a passphrase file for automation

Use the global `--passphrase-file` option with a private file. Keep that file
outside the repository and out of command output. Do not put passphrases in
arguments, Git remotes, examples, or shared environment logs.

Encryption settings live in `.lit/encryption.toml`, with key settings in the
repository's encryption configuration. Initialization and key loading are
separate from `lit init`; do not assume a newly initialized repository is
encrypted. Test an encrypted disposable repository and verify the configuration
before saving private material. Changing a setting does not prove that all
older plaintext objects have been rewritten.

## Packing

`lit gc` packs loose objects, verifies the pack, then removes the loose copies.
Encrypted repositories write v2 packs with authenticated encryption around
each compressed object payload. Pack indexes still expose IDs and sizes.
Unencrypted repositories can read the older plaintext v1 packs.

```sh
lit --passphrase-file /private/current-passphrase verify
lit --passphrase-file /private/current-passphrase gc
lit --passphrase-file /private/current-passphrase verify
```

## Change the passphrase and key

Keep a backup and stop other writers. Set `LIT_NEW_PASSPHRASE_FILE` to a separate
private file, then run:

```sh
lit --passphrase-file /private/current-passphrase rotate-key
```

Rotation rewrites loose and packed objects, the index, and references that were
already encrypted. Native text refs stay in their existing format. If publication
is interrupted, resume `rotate-key` with the new credential. The operation installs
a repository-local key and retains the old key file because other repositories
or backups may still need it. Test access with the new key before retiring an old
one. Rotation does not revoke access to copies someone already has.

## Counters and failed guesses

Passphrase backoff and encryption usage are recorded by key identity under
`~/.lit/key-state`. Processes and repository aliases sharing that directory
share counters. Failed guesses delay another expensive derivation attempt.
Encryption slots are reserved before use; unused reservations are discarded when
a process exits. Keep the ledger with the installation. A legacy key or a missing
usage ledger can require rotation before another write.

A new key receives a new usage budget. Copying a key onto unrelated hosts does
not create one shared counter: use distinct keys when hosts cannot share one
accounting authority. Do not delete ledger files to reset limits.

## What remains visible

Working files, filenames in some metadata, object IDs, sizes, local logs, and
native text refs are not all hidden by object encryption. A compromised process
with an unlocked key can read data. Losing both the key material and passphrase
can make the data unrecoverable. See [cryptography](CRYPTOGRAPHY.md),
[key handling](KEY_DISTRIBUTION.md), and [migration](DEPLOYMENT.md).
