# Signature verification fixtures

These six cases are selected from C2SP Wycheproof, separately licensed under
Apache-2.0. The JSON records the exact revision, original path, original-file
SHA-256, and unchanged case IDs. The selection contains ordinary valid input,
a valid boundary case, two invalid norm violations, and the valid/invalid
missing-reduction cases. The accompanying LICENSE-Wycheproof preserves the license.
No upstream Lit implementation or tests were used.

The Rust harness is independently written and calls OpenLit's public verification
interface. RUSTSEC-2026-0586 describes the affected pqcrypto AVX2 verifier. The
portable backend mitigation is separate from a future dependency replacement.

## Reproduce the dependency comparison

The `probe` directory contains the independent dependency harness used on host-a.
Download the full corpus from the pinned revision into `probe/vectors.json`:

```sh
curl -fLsS https://raw.githubusercontent.com/C2SP/wycheproof/65ca494727a52944c204441c860b039e6786b99b/testvectors_v1/mldsa_87_verify_test.json -o tests/fixtures/cryptography/probe/vectors.json
cargo run --release --manifest-path tests/fixtures/cryptography/probe/Cargo.toml --features affected-avx2
cargo run --locked --release --manifest-path tests/fixtures/cryptography/probe/Cargo.toml
```

The first run needs a non-Windows x86-64 CPU with AVX2 to select the affected
backend. On host-a it exited 1 with 50 mismatches; the portable run exited 0 with
none. It handles each vector's optional context. This dependency-level probe is
separate from the six cases tested through OpenLit's public signature interface.
Use a private target directory if other builds share the host's Cargo target.
