# OpenLit dependencies

The Cargo lockfile pins the build inputs. Dependency licensing is separate from
the upstream Lit source boundary: independently licensed dependencies may be
used under their own terms. Post-MIT upstream Lit code remains a black box.

## Security review

The November 20, 2026 RustSec scan found 11 vulnerability advisories and four
unsoundness warnings in the earlier lockfile. Compatible updates to crossbeam-epoch,
rustls, rustls-webpki, webpki-roots, anyhow, memmap2, rand, and rustix removed ten
vulnerability findings and all four unsoundness warnings.

One advisory remains: RUSTSEC-2026-0586 for pqcrypto-mldsa's AVX2 verifier.
The candidate disables default SIMD features and resolves only `std`, using the
portable backend. On host-a, a 241-case Wycheproof comparison found 50 mismatches
with AVX2 and none with the portable backend. Six selected cases run through
OpenLit's own verification interface on each release runner.
[Advisory](https://rustsec.org/advisories/RUSTSEC-2026-0586.html)

`cargo audit` still reports that advisory because the crate version is unchanged;
we do not suppress it or call the scan clean. The five unmaintained-package
warnings also remain, including the PQCrypto family, chrono, and paste. Replacing
unmaintained cryptography needs compatibility and verification tests, not just a
new dependency name. [PQCrypto maintenance status](https://rustsec.org/advisories/RUSTSEC-2026-0166.html)

[Exact findings and probe hashes](../tests/fixtures/cryptography/dependency-review-2026-11-20.json)
record the initial limited probes as well as the corrected full-corpus comparison.
The test data has its own Apache-2.0 license and pinned source record.

## Notice inventory

`THIRD_PARTY_NOTICES.txt` contains the bundled license and notice texts collected
from resolved Cargo packages, grouped by content hash. [The manifest](dependencies.json)
records each package, declared license, notice path, and digest. It includes dev
and non-native-target packages, so it is broader than any one executable.

Regenerate and check it with:

```sh
python3 tools/release_dependency_notices.py
python3 tools/release_dependency_notices.py --check
```

The collector now includes r-efi's `AUTHORS` permission text and pinned repository
licenses for seven WASI/wasm-tools packages. It checks supplemental notice hashes
and source revisions. [Supplement records](licenses/README.md) explain the inputs.

The two PQCrypto notice gaps are resolved for the pinned versions. We select the
Apache-2.0 alternative declared by each package and preserve the separate
embedded MIT, Gladman, public-domain, and NIST notices. The collector checks
source-comment excerpts against their exact registry files and hashes.
[Notice review and limits](../audits/crypto-notices-2026-11-20.md) records the
primary sources and the NIST permission lineage. No copyright holder was invented.

Every resolved package now has collected notice text. That is a completed notice
inventory, not certification of every contributor's rights. Dependency maintenance
and the reported security advisory remain separate issues.
Do not rewrite third-party license text into plain language.
