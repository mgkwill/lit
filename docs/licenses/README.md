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

Two packages still need resolution: pqcrypto-internals 0.2.11 and pqcrypto-traits
0.3.5. Their published manifests declare MIT or Apache-2.0, but their package and
pinned repository trees contain no license or notice files. Do not invent an
upstream copyright statement or treat the inventory as release approval. Review
this gap along with the planned replacement of unmaintained cryptography.
