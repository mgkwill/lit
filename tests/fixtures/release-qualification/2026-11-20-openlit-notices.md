# Final OpenLit 0.1.0 preparation candidate

Recovery source `83b2fe963e8bc6d2d1fa68e6354e9e6a15bedad4` adds verified
third-party notices to the earlier OpenLit candidate. It remains unsigned by a
publisher and unpublished. The command remains `lit 0.1.0`.

[All five native jobs passed](https://github.com/mgkwill/lit-release-qualification/actions/runs/44817526987)
using private projection `a75052eb13aded4b1a10db4bfe3a0553f7333670`. The projection
omits the same three unrelated workflows as the earlier qualification; all other
tracked files match. [Exact receipts and archive hashes](2026-11-20-openlit-notices.json)
cover Linux x86-64/arm64, macOS Intel/Apple Silicon, and Windows x86-64.

The jobs rebuilt the binaries, ran signature vectors, checked all five notice
integrity tests, regenerated and compared the notice inventory, and tested the
unpacked archives. Package tests cover identity, batch rollback, MCP, native and
Git remotes, and Windows strict-mode refusal. Unix runners also checked the guide
examples. GitHub artifact ZIP hashes were checked again after downloading.

The current macOS ARM archive passed the archive tests on the local Mac. The
current Linux archives passed batch/MCP and authenticated HTTPS/SSH remote tests
on host-a, host-e, and host-f. Four rebuilt executable hashes match the earlier
candidate. The Windows executable has a new hash and passed its native package
tests; we make no claim of bit-for-bit reproducibility on Windows.

Rust source, Cargo.toml, Cargo.lock, and LICENSE are unchanged from `53a23cc`.
The [earlier full Rust suite](2026-11-20-openlit.md) passed 487 tests locally and
on each lab host, including host-f's separately recorded doctest retry. It was not
rerun for notice-only changes. The earlier three-platform installer checks also
remain applicable; installer scripts did not change.

## Notice work

The collector now includes r-efi's existing AUTHORS permission and copyright
text, plus repository license files for seven WASI/wasm-tools crates. Supplemental
files are tied to the crate's published source revision and checked by digest.
Tests reject changed text, wrong revisions, obsolete versions, and paths outside
the notice directory. Original license bytes are preserved across checkout
platforms. A preliminary run was canceled after a local check found Git had
normalized embedded notice line endings; the final source corrects that issue.

Two notice gaps remain: pqcrypto-internals 0.2.11 and pqcrypto-traits 0.3.5. Their
published declarations alone do not supply the missing notice text. This is not
a completed license review. See [dependency status](../../../docs/DEPENDENCIES.md).

## What remains

- Resolve the two dependency-notice gaps and review the remaining cryptography
  maintenance risks. One mitigated vulnerability advisory and five maintenance
  warnings remain reported; the audit is not clean.
- Select Apple Developer ID and Windows Authenticode publisher identities, wire
  signing and attestations, and test the final signed bytes and clean downloads.
- Finish source-provenance review and archive reviewed implementation records
  with the required manifest. The earlier audit does not cover later work by
  implication.
- Obtain owner approval for the exact release before tagging or publishing.

The current ARM lab policy still prevents positive Bubblewrap launch. Strict
refusal was verified in the earlier round; this packaging run does not establish
successful ARM filesystem or network confinement. No host policy was relaxed.

The sandbox's Actions-only rule remains in place. This preparation added no
manual statuses, bypass actors, public release, tag, registry package, or hosted
repository rename. The five final unsigned archives and raw receipts are retained
under the original verification directory in
`release-candidate-openlit-0.1.0-83b2fe9/`. Their checksum manifest is an integrity
record, not a publisher signature.
