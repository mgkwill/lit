# Encryption recovery work

OpenLit's recovery branch adds encrypted v2 packing, reads of packed objects,
packed-object verification, key rotation, passphrase backoff, and per-key
usage accounting shared across processes on the same accounting authority.

These changes address specific storage and recovery problems. They do not
turn all repository metadata into encrypted data or make separate hosts share
a global usage counter automatically.

The user instructions are now in [encryption](ENCRYPTION.md). Use that guide
for packing, rotation, interrupted operations, and ledger handling. The
[testing guide](TESTING.md) describes how to run regressions. Historical
fixture reports retain their original commit IDs and results.

Before broader deployment, verify restore with your own key storage and backup
process. Treat an unavailable ledger as a reason to recover or rotate, not as
permission to bypass a key's limits.
