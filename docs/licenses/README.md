# Supplemental dependency notices

These are license texts from separately licensed dependencies, not upstream Lit.
They fill gaps where a Cargo package omitted its repository's license files.

`supplements.json` binds each text to the exact version and `.cargo_vcs_info.json`
revision of the downloaded crate. It records the source URL and SHA-256 digest.
The collector checks the revision, package version, path, and digest before adding
text to `THIRD_PARTY_NOTICES.txt`. It rejects unused entries after lockfile changes.

The WASI and wasm-tools license files were fetched from those exact repository
commits and compared byte for byte. r-efi already ships its MIT permission and
copyright text in `AUTHORS`; the collector now includes that file directly.

For pqcrypto-internals and pqcrypto-traits, the manifest selects their declared
Apache-2.0 alternative and includes the official license text. Internals also
carries notices in source comments; exact slices are pinned by source hash and
line range. NIST's original permission comes from its official December 2021 KAT
archive, with a separate, clearly labeled distribution attribution note.
[The review](../../audits/crypto-notices-2026-11-20.md) records exact sources and
limitations. Official license texts, dependency comments, and our explanatory
annotation retain distinct origins in the manifest.
