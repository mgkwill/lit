# FIPS 140-3 status

**OpenLit has no FIPS 140-3 validation or certified security level.**

Using AES, SHA-3, or ML-DSA does not validate the application. The repository's
algorithm tests, `fips_mode` setting, and earlier compliance-oriented filenames
do not establish a certificate, module boundary, or approved deployment.

This filename remains so existing links reach an accurate statement. The project
makes no ISO/IEC 19790 certification claim either. A future validation effort
would need its own defined boundary, evidence, and external assessment; it is
not part of the 0.1.1 release claim.

See [cryptography](CRYPTOGRAPHY.md) and [security](SECURITY.md).
