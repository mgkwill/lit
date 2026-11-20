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
