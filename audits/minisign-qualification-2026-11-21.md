# Minisign qualification — November 21, 2026

OpenLit 0.1.0 now uses detached Minisign signatures for release archives,
`SHA256SUMS`, and `RELEASE.json`. The publisher key is supplied from outside Git.
The helper verifies signatures, exact filenames, checksums, source SHA, version,
target set, and candidate purpose before archive extraction or execution.

## Source and evidence boundaries

Implementation: `d6195afdda70152c124943dff92b4b162d7da37c`.
Intel tool correction: `ec260ccf6b12d0e08aff45fa4a835b8030935e18`.
Final tested source: `a1459c7f3865495eeda83c065859363b14130e60`.
Private qualification projection: `9f308bd0a8fb2bb2103b3ab27953af5bc4f7e0e4`.
The projection removes unrelated workflow files only. Every other tracked file,
including the release workflow, matches the source. Signed CI metadata names
the projection SHA; it does not attest a different SHA by implication.

Source inputs were our MIT-derived recovery code, independently authored release
tools, and the separately licensed Minisign tool and documentation. No post-MIT
upstream Lit implementation or tests were read. The original MIT license remains
unchanged. This record does not expand the earlier historical provenance audit.
Implementation session: `01a1e52b-d71a-7fd0-9edc-6d82560ebd8f`. Original session
records remain outside Git. The accompanying JSON records source identities and
hashes of the retained test receipts and adverse logs, not transcript exports.

## Verified results

[Native qualification run](https://github.com/mgkwill/lit-release-qualification/actions/runs/44931789832)
passed on Linux x86-64/arm64, macOS Intel/Apple Silicon, and Windows x86-64.
Each worker built a fresh archive, signed it with a disposable external key,
verified signatures before extraction, rejected modified archive/manifest/signature
copies and a wrong key, and ran batch rollback, MCP, and remote tests on the
extracted binary. Version, MIT license, and dependency notices were checked.
Linux qualification retained the glibc 2.35 runtime floor. Windows additionally
checked its sandbox refusal contract. Unix workers ran the documented quick start.

All five jobs ran the 12 real-Minisign regression cases, including encrypted-key
signing without password arguments, wrong identities, metadata mismatch,
missing/extra files, and key-location refusal. Windows skips the symlink case
because creating symlinks needs additional privileges; the Unix workers cover it.
The ten existing optional platform-signing gate tests and dependency notice
inventory passed locally. Changed workflows passed actionlint.

The downloaded Linux archives also passed verification, tamper rejection,
signature regressions, batch/MCP smoke, and remote checks on host-a, host-e, and
host-f. Tests used isolated temporary directories without service changes.

All five final native archives were then combined into one complete bundle and
signed locally with one encrypted disposable key. The default full-set verifier
accepted it. Changed archive bytes, a wrong public key, and presenting the test
candidate as a publisher release were rejected. The restored bundle verified
again, and its macOS archive passed native smoke testing after verification of
the complete five-target signature set. Exact hashes are in the JSON receipt.

## Failures found and retained

The first run selected Minisign's upstream macOS archive on Intel. That archive
is arm64-only, so execution failed with `Bad CPU type`. Intel CI now installs the
native Homebrew build and requires Minisign 0.12. The direct download helper
rejects Intel macOS instead of supplying an incompatible executable. Other
platforms use checksum-pinned upstream 0.12 downloads; their upstream signatures
were verified before pinning the hashes. See [Minisign](https://jedisct1.github.io/minisign/).

Windows passed signature tests but hit a smoke-harness readiness race: a
connection timeout escaped a loop that retried only connection refusal. The
loop now handles both within the same five-second deadline. Local fault
injection reproduced the old failure, then verified a successful retry and
rejection of persistent timeouts at the deadline. The superseded intermediate
run was cancelled; it is not counted as passing evidence.

## Key custody and publication

Only disposable qualification keys were used. They were generated outside Git,
never uploaded, and deleted by test cleanup. The complete-bundle key was encrypted;
its random password was passed through stdin and was absent from captured output.
Only public test keys, signatures, manifests, archives, and receipts were retained
outside the repository. No private key is included in the commits or artifacts.
No permanent publisher key was created, and no release or tag was published.

These are cryptographic signing and native test results, not publisher identity,
Apple notarization, Authenticode, SmartScreen reputation, or build-attestation
proof. The former platform-signing workflow remains optional and disabled until
its own credentials are configured. Actions-only merge rules are unchanged.

Next: the owner creates and backs up the encrypted publisher key, reviews its
public identity, and uses it for the approved final archives. Follow
[key setup and verification](../docs/RELEASE_CREDENTIALS.md). Review exact source,
hashes, tests, and destination with the owner and ask before publishing.
