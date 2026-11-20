# Cryptography in OpenLit

OpenLit uses library implementations of hashes, signatures, and encryption.
An algorithm name is not a product certification. OpenLit has no FIPS module
validation claim and no general claim of resistance to every quantum attack.

| Purpose | Current implementation |
| --- | --- |
| Native object identity | Composite SHA3-512 and BLAKE3 hash |
| Repository signatures | ML-DSA-87 through the signature module |
| Stored payload encryption | AES-256-GCM |
| Passphrase derivation | PBKDF2-HMAC-SHA512 |
| Git object interoperability | Git's object identifiers and the system Git tool |

Native IDs are 192 hexadecimal characters. They are not Git SHA-1 IDs. Git
import/export keeps the necessary mapping and metadata for supported round trips.
Use the interoperability tests when changing either format.

Authenticated encryption detects changed ciphertext when it is read with the
right key. It does not conceal every filename or metadata field, protect an
unlocked process, or recover a lost key. Nonces and per-key use limits matter;
keep the usage ledger and use [key rotation](ENCRYPTION.md) as documented.

Commit signing, release signing, and TLS authentication answer different
questions. A commit signature binds repository content to a key. Release signing
binds a distributed executable to a publisher. TLS authenticates a connection.
None of them proves that the proposed code is correct.

Implementation entry points are `src/crypto/`, `src/storage/pack.rs`, and
`src/commands/rotate_key.rs`. Read the current MIT-derived implementation and
its tests before making security claims. See [security](SECURITY.md) and the
[release policy](docs/RELEASING.md).
