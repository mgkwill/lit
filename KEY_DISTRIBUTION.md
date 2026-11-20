# Keep keys and credentials separate

OpenLit deployments use several kinds of credentials. They have different jobs.

| Credential | Who should hold it | Purpose |
| --- | --- | --- |
| Repository encryption key and passphrase | The repository's authorized users | Read and write encrypted storage |
| Git HTTPS or SSH credential | A client with the required remote access | Fetch or push to a Git remote |
| Broker GitHub App key | The trusted broker service | Obtain short-lived writer and reader tokens |
| Worker TLS key | One worker identity | Authenticate that worker to the broker |
| Scheduler TLS key | The trusted controller | Assign and reassign tasks |
| Policy observer credential | A separate observer service | Read branch rules and retired-writer status |
| Release signing identity | The approved release service | Sign public distribution artifacts |

Do not give workers the broker's writer token or App key. Separate TLS identities
are useful only if OS permissions also keep private keys apart. Logical identities
sharing one OS account do not provide that protection.

For repository encryption, back up the key, passphrase, and accounting state
using private storage. Test a restore before retiring older material. Use separate
keys on hosts that cannot share one usage ledger. Key rotation cannot revoke a
copy of plaintext or an old backup already obtained by someone else.

For TLS rotation, enroll the new certificate, verify access with it, remove the
old fingerprint, and verify that the old certificate is rejected. Match
fingerprints over DER certificate bytes. Keep the issuing CA private. See the
[broker operations guide](tools/deployment/README.md) for deployment details.

Release signing is still a preparation requirement, not a completed service.
See [release requirements](docs/RELEASING.md).
