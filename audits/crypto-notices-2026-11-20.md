# PQCrypto dependency notices, 2026-11-20

## Result and scope

The two missing **package-level license texts** can be supplied without inventing
copyright ownership: select the Apache-2.0 alternative declared by each exact
published crate and distribute the official Apache license text. This is a
supported inventory repair, not certification of every contributor's authority or
completion of the release license review. In addition, pqcrypto-internals bundles
third-party notices inside source comments that the filename-based collector
misses. Four MIT notices, a binary-redistribution notice, public-domain/CC0
attributions, and two seed-expander attribution comments are now preserved as
14 exact comment-only extracts beside the canonical Apache text. Follow-up found
the original NIST permission in its official KAT archive; that exact comment is
also preserved as `NOTICE-NIST.txt`. A separately labeled OpenLit distribution
annotation, `NOTICE-NIST-PROVENANCE.txt`, acknowledges NIST and records the
upstream modification dates/nature without pretending to be an upstream notice. No concrete missing permission text remains
identified within these two packages after integrating these proposed records.

This research started at OpenLit recovery commit
`f9f49c9503c043bb51228a347f43db7a4f1fad95`, branch
`codex/mit-lit-recovery`. Only separately licensed dependency inputs were read.
No post-MIT upstream Lit implementation, test, patch or source archive was read.
The Lit provenance audit has not been expanded by this dependency research.
No maintainer was contacted and no publication occurred. The parent task
integrated the checked notices into the collector and supplement manifest.

## Exact dependency evidence

| Published crate | `.cargo_vcs_info.json` revision | `path_in_vcs` | Archive SHA-256, also verified against Cargo.lock |
| --- | --- | --- | --- |
| pqcrypto-internals 0.2.11 | `ea05906fb47c99411b21ce3c2ead3fcffe4a365a` | `pqcrypto-internals` | `b4a326caf27cbf2ac291ca7fd56300497ba9e76a8cc6a7d95b7a18b57f22b61d` |
| pqcrypto-traits 0.3.5 | `2ba2d2a859c38270c9c1cd3adff8c02a1a9b0719` | `pqcrypto-traits` | `94e851c7654eed9e68d7d27164c454961a616cf8c203d500607ef22c737b51bb` |

All 29 published internals files and all six published traits files were compared
byte-for-byte with their local registry copies. The registry-created `.cargo-ok`
is not part of either archive. Primary archive sources:
[internals](https://static.crates.io/crates/pqcrypto-internals/pqcrypto-internals-0.2.11.crate),
[traits](https://static.crates.io/crates/pqcrypto-traits/pqcrypto-traits-0.3.5.crate).

The exact [internals manifest](https://github.com/rustpq/pqcrypto/blob/ea05906fb47c99411b21ce3c2ead3fcffe4a365a/pqcrypto-internals/Cargo.toml)
and [traits manifest](https://github.com/rustpq/pqcrypto/blob/2ba2d2a859c38270c9c1cd3adff8c02a1a9b0719/pqcrypto-traits/Cargo.toml)
both declare `MIT OR Apache-2.0`; neither specifies a license file. The traits
manifest names Thom Wiggers in its `authors` field. Internals has no `authors`
field. This records declared authorship, not a copyright ownership determination;
no year or copyright holder was manufactured from metadata.

GitHub's recursive tree API returned 165 entries for
[internals revision](https://api.github.com/repos/rustpq/pqcrypto/git/trees/ea05906fb47c99411b21ce3c2ead3fcffe4a365a?recursive=1)
and 157 for
[traits revision](https://api.github.com/repos/rustpq/pqcrypto/git/trees/2ba2d2a859c38270c9c1cd3adff8c02a1a9b0719?recursive=1),
with `truncated: false`. Neither tree has filenames containing LICENSE, LICENCE,
COPYING, NOTICE, COPYRIGHT or AUTHORS. Submodule contents are not recursively
included in that claim. The three root-path history queries for
[LICENSE](https://api.github.com/repos/rustpq/pqcrypto/commits?path=LICENSE&per_page=100),
[LICENSE-MIT](https://api.github.com/repos/rustpq/pqcrypto/commits?path=LICENSE-MIT&per_page=100)
and [LICENSE-APACHE](https://api.github.com/repos/rustpq/pqcrypto/commits?path=LICENSE-APACHE&per_page=100)
each returned an empty list on the default history. This is not a search of every
historical ref or filename. The traits manifest's old `rustpq/pqclean` URL redirects
to `rustpq/pqcrypto`; this is the Rust binding repository, distinct from
`PQClean/PQClean`.

## Package-level Apache choice

Cargo documents that the license field describes the license under which the
package is released and that `OR` permits a choice. Therefore selecting Apache-2.0
is supported by the publisher's exact metadata, without reconstructing an absent
MIT copyright notice. [Cargo manifest reference](https://doc.rust-lang.org/cargo/reference/manifest.html#the-license-and-license-file-fields)

Apache-2.0 section 4(a) requires providing the license; 4(b) concerns changed
files; 4(c) preserves source-form attribution; 4(d) carries forward an existing
NOTICE file. No package/root NOTICE was found here, so there is no observed
package NOTICE text to reproduce under 4(d). That does not erase embedded
third-party terms. The canonical license's appendix placeholders remain unchanged:
they are the standard license's example, not OpenLit's asserted author notice.
[Official Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0)

The ASF explicitly permits non-ASF projects to use Apache-2.0 and distinguishes
included third-party licensing from a project's own licensing. This does not
make PQCrypto an Apache Software Foundation project.
[ASF licensing FAQ](https://www.apache.org/foundation/license-faq.html)

`docs/licenses/pqcrypto/LICENSE-APACHE` is the 11,358-byte official
[text](https://www.apache.org/licenses/LICENSE-2.0.txt), retrieved 2026-11-20,
SHA-256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.
Its source is the ASF, **not a nonexistent file in the dependency repository**.
The collector must preserve that origin distinction.

## Bundled source notices

All entries below are exact byte slices, including original comment markers and
trailing newline, from files compared byte-for-byte with the pinned remote
internals revision and checked published crate. No implementation statements were
copied into these notice files. The JSON proposal below records both whole-source
and excerpt SHA-256 values. Local filenames use `NOTICE-` plus the package-relative
path with `/` replaced by `-`, then `.txt`.

| Package-relative source | Inclusive source lines | Notice classification |
| --- | --- | --- |
| `cfiles/aes.c` | 1–27 | Thomas Pornin 2016 MIT permission and attribution |
| `cfiles/randombytes.c` | 1–23 | Daan Sprenkels 2017 MIT permission |
| `cfiles/keccak2x/fips202x2.c` | 2–34 | Named authors 2023, offered MIT/CC0; preserve complete notice |
| `cfiles/keccak2x/feat.S` | 2–25 | Bas Westerbaan 2020 and named authors 2023 MIT |
| `cfiles/keccak4x/brg_endian.h` | 1–27 | Brian Gladman 1998–2008; includes explicit binary-documentation condition |
| `cfiles/fips202.c` | 1–6 | Public-domain source attribution |
| `cfiles/sha2.c` | 1–3 | Public-domain source attribution |
| `cfiles/keccak2x/fips202x2.h` | 4–9 | Apache/public-domain source declaration |
| `cfiles/keccak4x/KeccakP-1600-times4-SIMD256.c` | 1–18 | Named authors' waiver and CC0 link |
| `cfiles/keccak4x/KeccakP-1600-times4-SnP.h` | 1–14 | Named teams/authors' waiver and CC0 link |
| `cfiles/keccak4x/KeccakP-1600-unrolling.macros` | 1–14 | Same waiver; separate source provenance retained |
| `cfiles/keccak4x/align.h` | 1–14 | Same waiver; separate source provenance retained |
| `cfiles/nistseedexpander.c` | 1–7 | Bassham copyright and Verschoor modification attribution; no permission text |
| `include/nistseedexpander.h` | 4–10 | Corresponding header attribution; no permission text |

The first five entries contain actual permission/redistribution conditions.
The others preserve license declarations and attribution evidence conservatively;
this report does not assert that every public-domain attribution is independently
required for binary distribution. `randombytes.c` ships in the crate but is not in
this version's build script's common C compilation list. The inventory covers
resolved source packages as well as target-specific binaries, so it is retained.

The [pinned build script](https://github.com/rustpq/pqcrypto/blob/ea05906fb47c99411b21ce3c2ead3fcffe4a365a/pqcrypto-internals/build.rs)
compiles common AES, SHA2, FIPS202, NIST seed expander and SP800-185 files; it also
compiles keccak4x on x86/x86_64 and keccak2x on aarch64 except MSVC. Disabling
`pqcrypto-mldsa` SIMD features does not remove this build-script behavior. This
is build-input evidence, not proof of which archive members survive final linking.

Scanning every published source file for copyright, license, permission,
public-domain, author and redistribution markers found no additional permission
blocks beyond the fourteen listed comments. No such header was found in the
three traits Rust source files. The scan does not prove there is no undisclosed
third-party material.

## Limits that remain separate from notice collection

The original NIST permission has now been located in a specific official
artifact, so this is no longer only an inference from a generic website policy.
The [NIST PQC archive](https://csrc.nist.gov/Projects/post-quantum-cryptography/pqc-archive)
links the [December 2021 source ZIP](https://csrc.nist.gov/CSRC/media/Projects/Post-Quantum-Cryptography/documents/archive/source-code-files-for-kats-dec2021.zip).
It contains `PQC-APIdocs-UpdatedCopyright/rng.c` and `rng.h`, each beginning
with the full NIST use/copy/distribution/modification permission and disclaimer.
The exact `rng.c` first comment, lines 1–7, was copied without rewriting into
`docs/licenses/pqcrypto/NOTICE-NIST.txt`.

| Original NIST artifact | SHA-256 |
| --- | --- |
| December 2021 ZIP | `84b73c1008e9dbb452606ab9eb8d25cdd6b4011c3c88f6fdafaab20957e07234` |
| `PQC-APIdocs-UpdatedCopyright/rng.c` | `2cc42477d95648f6f4900ce9906bb8840990290a410fa5206caed58663b6d5cd` |
| `PQC-APIdocs-UpdatedCopyright/rng.h` | `df44ce6c84abd83fed1f4966fba667856de38227d15fe98f4c0a650b21a0cd5e` |
| Exact permission comment copied from `rng.c` | `35da5dbe3107958ff910ed94250913ec9e73c73504850df9cb53454f04651b00` |

Comparing the two NIST seed-expander functions with the published internals C file
establishes the specific derivation: matching initialization, AES counter-mode
expansion and error handling, with PQClean changes to types/constness, maximum-
length checking, braces/formatting, and the AES backend. NIST's file also contains
other RNG functions that are not in the dependency's seed-expander file. This is
not a byte-identical whole-file claim. The original NIST artifact explicitly
permits modifications; retain its entire notice and acknowledge NIST as the
source, together with the original dependency attribution to Sebastian Verschoor
for PQClean changes. OpenLit did not modify these dependency source files.

The relevant [PQClean introduction](https://github.com/PQClean/PQClean/commit/33232a0343c75947e5479c622930461a800ec7d9)
is dated 2020-04-01 and records seed-expander changes; the
[AES rename](https://github.com/PQClean/PQClean/commit/9944e6a81d3edd2dbc191a818a827086b3385ba8)
is dated 2020-04-06. Rustpq's
[shared-crypto extraction](https://github.com/rustpq/pqcrypto/commit/fb4ea774b1c5e1233a826779cad5304c06a23b93)
is dated 2021-07-28; it is the only commit returned for the internals seed-expander
path through the pinned release revision. These are recorded modification dates
and source lineage, not invented author copyright dates. The pinned PQClean
[README](https://github.com/PQClean/PQClean/blob/d746637a685935d6d960a13bce92b994994b2e80/README.md#license)
also describes the common files as public domain or MIT. The report records these
primary sources without importing implementation excerpts into notices.

### Compiled archive versus final binaries

`Cargo.toml:58` selects ML-DSA with default features disabled and `std` enabled.
All 63 files in the three published ML-DSA clean directories (21 per scheme) were
searched for seed-expander references; none were found. Internals nonetheless
compiles `nistseedexpander.c` into its common static archive unconditionally.

Two existing native release binaries were inspected read-only with `nm`:

| Binary observation | SHA-256 | `nm` result |
| --- | --- | --- |
| Local macOS source-built release | `deb8e7fcde77d80bf759f5b3eb19cce32f435e28b896ec4fd344236854ace01c` | 20,420 symbol lines; clean ML-DSA87 and SHAKE present; no seedexpander symbols |
| host-a Linux source-built release | `c436bb959d59c9b5d0d4ca05b731fd7d2f8b65a6168c44ecbecefb896c4c2998` | 22,413 symbol lines; clean ML-DSA87 and SHAKE present; no seedexpander symbols |

Both commands succeeded and both binaries retain symbol tables. Each corresponding
`libpqclean_common.a` contains `nistseedexpander.o`, `seedexpander`, and
`seedexpander_init`. Archive hashes are
`cab62d49208736298380fef88aafa518fe5b391aae8bf170784a79f67fbf66ff` (macOS)
and `f8516a4a8235291faa21e7042e98eb1c671f5d1e7bebb2d9b8efe4c5abc8e414`
(Linux). The Linux build's Cargo.lock hash is
`69ace3266c9c7427263ef46ecffbc7c3309b8ca3dd21ff313284d15d0d9e4e8b`,
identical to this recovery checkout's lockfile. These observations support that
unused seed-expander functions were not retained in these two final binaries.
They do not certify Windows, other release artifacts, or future build/link flags.
Notices are retained broadly anyway; supplying the actual NIST permission closes
the source-inventory gap without depending on dead-code elimination.

The package declarations supply a genuine Apache alternative; they are not proof
that every contributor could relicense every bundled source. Choosing Apache
for the package does not supersede the embedded MIT/Gladman notices. This report
does not substitute a generic license for those notices, infer an MIT owner from
Git authors, or certify worldwide legal status. The maintenance/security
replacement issue in `docs/DEPENDENCIES.md` remains separate and unchanged.

## Proposed integration

1. Retain the canonical Apache text, all fourteen package comment extracts,
   the original NIST permission comment, and the labeled distribution annotation.
2. Add the two version/revision-bound records below to `supplements.json`.
3. Change the collector to preserve each supplied `origin` (with its existing
   pinned-repository default for older records). Do not falsely label the ASF
   text a file from the dependency repository. Carry `selected_license` into
   generated records and display the chosen Apache alternative.
4. For package-source excerpts, verify `source_sha256` against the installed package,
   verify inclusive line extraction equals the notice file, and retain the
   existing version, revision, path and notice digest checks. This guards both
   source selection and local copy integrity without network access at build time.
5. Regenerate the inventory. Empty `missing_notices` then means every package
   has collected notice text; it must not be presented as complete license
   approval. Record the explicit NIST archive mapping above, instead of the
   superseded missing-permission concern.
6. Ensure binary archives include the generated notices (particularly Gladman's
   binary-documentation notice); source redistribution must also preserve original
   headers and any modification notices.

The following is a proposal, not an applied change to the shared manifest:

The integrated [supplement manifest](../docs/licenses/supplements.json) records
all notice origins, revisions, line ranges, and hashes. The collector verifies
package-relative excerpts against the locked registry source.
