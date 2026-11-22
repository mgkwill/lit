# OpenLit 0.1.1 release candidate — November 22, 2026

The candidate is prepared and signed, but **publication is not authorized**.
No `v0.1.1` tag or release was created. The signed source is
`777acec2002a69b2429c91a58f9f8371f12724bd`; the later evidence commit is not its
build identity. [Machine-readable evidence](release-0.1.1-2026-11-22.json)
contains archive hashes, qualification receipts, and verification results.

## Copyright and attribution

The MIT license retains Copyright (c) 2025 Nervosys and adds Copyright (c) 2026
Marcus G.K. Williams for his contributions. `NOTICE.md` explains the original
MIT source and the attribution boundary. All five native archives include
`LICENSE`, `NOTICE.md`, and `THIRD_PARTY_NOTICES.txt` beside the executable.
Missing or changed notices are rejected by the archive qualification tests.

Independent checks of the downloaded archives matched those files to the
candidate source. Windows checkout converts `LICENSE` and `NOTICE.md` to CRLF;
their text matches after that conversion. Its dependency notices and all Unix
notices are byte-identical to the source. The JSON records both raw hashes and
the comparison used, rather than calling converted files byte-identical.

## Regression checks

[CI run 45045028170](https://github.com/mgkwill/lit/actions/runs/45045028170)
passed all six jobs at the signed source: Rust checks on Linux, macOS, and
Windows; broker/queue/Pi controller regressions on Linux and macOS; and the
sanitized-evidence checks. Rust results were 504 passed on Linux, 504 on macOS,
and 480 on Windows, with zero failures or ignored tests in those suites.
The local macOS release suite also passed 504 tests. Platform-conditional
coverage differs; these counts are not a claim of identical platform support.

[Source release checks 45045028150](https://github.com/mgkwill/lit/actions/runs/45045028150)
passed on all three operating systems: installer, dependency notices,
provenance export, signing gates, Minisign, CI signing, and archive attribution.
The Windows symlink test was skipped because it requires elevated privileges;
the Linux native platform signature test was skipped because that verifier is
unavailable there. Both behaviors are exercised on other platforms.

## Fresh archives and signing

[Signing run 45045042335](https://github.com/mgkwill/lit/actions/runs/45045042335)
built and qualified all five native targets, then waited for the required owner
review. The owner approved signing only. That approval was submitted through
GitHub's normal environment review API without changing protection rules.

Each archive passed disposable-key signature and tamper tests, attribution
checks, and its native executable tests. Linux x86-64 and arm64 ran on glibc
2.35, with a highest required symbol version of 2.34. The native receipts record
the batch, MCP, and remote checks and platform-specific limits.

The approved job signed the five archives, `SHA256SUMS`, and `RELEASE.json`
with the permanent encrypted Minisign key. The trusted public-key file SHA-256 is
`020727c93e30003cbab9f902459465e55ed6db37e951b2d006b9ec95ac4d77c3`.
The downloaded key matched the committed trusted key. All seven signatures were
independently verified with Minisign, and all 14 bundle file hashes matched CI.
The five archive hashes also matched the pre-signing native qualifications.

CI and independent local verification each rejected six cases: wrong key,
wrong source, altered archive, altered checksums, altered release identity,
and damaged signature. Tests changed copies and preserved the original bundle.
The downloaded publisher-signed Apple Silicon archive passed the full native
archive checks again on the local Mac.

## Scope and remaining approval

Post-MIT upstream code remained a black box. This task changed release metadata,
packaging, attribution tests, and documentation using the existing recovery
sources. It did not import upstream implementation or repeat a full provenance
audit. Previous transcript archive boundaries remain unchanged; this report
is release-check evidence, not a new implementation-transcript archive.

The 0.1.0 assets and signatures remain unchanged historical evidence. This
candidate was freshly built and signed from the current sanitized history.
Minisign does not provide Apple notarization, Windows Authenticode, or a GitHub
build attestation. Existing platform and dependency limits remain documented.

The signed artifact `openlit-0.1.1-publisher-signed` is retained by Actions for
14 days. Publication would use these exact bytes: five archives, seven detached
signatures, two manifests, and the trusted public key, for 15 release assets.
Ask the owner before creating `v0.1.1` at the signed source or publishing assets.
