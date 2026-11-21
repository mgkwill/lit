# Approval-protected publisher signing — November 21, 2026

The permanent OpenLit Minisign key successfully signed the five 0.1.0 archives,
`SHA256SUMS`, and `RELEASE.json` in GitHub Actions. The downloaded bundle passed
independent signature verification and six rejection checks. No release or tag
was created; publication still requires the owner's separate approval.

## Exact candidate and identity

- Build and signing source: `67cbd625a372800b7f626de66a43310255c82b82`.
- [Successful publisher workflow](https://github.com/mgkwill/lit/actions/runs/44958839171), attempt 1.
- Artifact: `openlit-0.1.0-publisher-signed`, ID `14730013365`.
- Public key: [`keys/openlit.pub`](../keys/openlit.pub), key ID `E22218B425ADC29C`.
- Public-key file SHA-256: `020727c93e30003cbab9f902459465e55ed6db37e951b2d006b9ec95ac4d77c3`.

Both the publisher workflow and its reusable native qualification workflow
resolved to that exact source commit. There was no test projection. This
candidate supersedes the earlier locally signed `ca57a8f` candidate for the
proposed release. A later evidence-only commit does not change the source
identity signed into these archives' release manifest.

The accompanying [JSON evidence](ci-publisher-signing-2026-11-21.json) contains
the archive and signature hashes, native receipts, test results, run identities,
and a SHA-256 manifest of 41 retained external evidence files. GitHub's artifact
ZIP digest is labeled as GitHub-reported; the individual signed file hashes were
independently checked after download.

## Approval and key access

Before approval, all five qualification jobs had succeeded and the publisher
job was visibly waiting without starting its steps. The pending deployment
named only `mgkwill` as reviewer. GitHub settings were read back before and
after signing: `release-signing` requires that reviewer, accepts only the `main`
branch, and disables administrator bypass. Self-review is allowed so the owner
can approve a run they started. The key and password are environment secrets,
not repository-wide secrets, and are not passed to native build jobs or PR tests.

When the owner explicitly approved the signing-only run in chat, GitHub still
showed a pending review. The assistant recorded that authorization through the
normal deployment-review API as `mgkwill`, without changing protection rules or
using bypass. GitHub's review history records approval for this environment
and a comment limiting authorization to signing and verification at `67cbd62`.
This was not a browser-submitted review and did not authorize publication.

The signing job started only afterward. It verified same-run qualification
signatures, source identities, hashes, and receipts before consuming the
publisher credentials. It used an encrypted temporary key outside Git, passed
the password through standard input, and uploaded only the verified bundle,
public key, and non-secret receipts. The workflow has read-only repository
permissions and contains no release or tag creation step.

## What passed

1. Native qualification on Linux x86-64 and arm64, macOS Intel and Apple Silicon,
   and Windows x86-64. Each archive passed disposable-key signature and tamper
   checks, package checks, and native batch, MCP, and remote smoke tests before
   publisher signing. The archive hashes match exactly after publisher signing.
2. Publisher signing and verification of all seven files in CI. The workflow
   rejected a wrong public key, wrong source commit, changed archive, changed
   checksum manifest, changed release identity, and broken signature.
3. Independent verification of the downloaded publisher bundle with the public
   key already pinned in the reviewed repository, rather than trusting a key
   supplied only by the artifact. All seven signatures also passed direct
   Minisign checks. The six rejection cases were repeated on temporary copies;
   the original signed files remained unchanged.
4. The downloaded macOS arm64 archive passed local native package, batch rollback,
   MCP, and remote smoke tests after verification of the complete publisher-signed
   bundle. Other targets' native evidence is the qualification run on identical
   archive bytes; they were not rerun locally after publisher signing.
5. [Main CI](https://github.com/mgkwill/lit/actions/runs/44958884026) and
   [source-release checks](https://github.com/mgkwill/lit/actions/runs/44958883950)
   passed at the candidate commit. Local regression evidence records 13 new
   CI-signing cases and 12 existing Minisign cases, plus actionlint on the changed
   workflows. The existing Windows symlink test retains its documented skip.

The regression cases cover wrong or missing credentials, refusal of an
unencrypted key, preserving an existing candidate, removing credential variables
from child environments and arguments, timeout cleanup, wrong-source artifacts,
modified archives and receipts, and missing artifacts. During implementation
review, the CI adapter was changed to invoke Minisign directly so a timeout can
terminate the signer rather than leave a grandchild process running.

The retained artifact files and signer log were checked for the encrypted
private-key file and its encoded payload; there were no matches. This check
did not inspect the password, which was not available to the verifier. Cleanup
and secret-handling claims are bounded to the reviewed code, regression tests,
successful workflow, and retained outputs, not a forensic memory inspection.

## Source, retention, and remaining approval

Inputs were the MIT-derived recovery implementation and our independently
authored release tools. No post-MIT upstream Lit implementation or tests were
read or imported. The 100latent Actions-only merge rules were not changed.
This report does not extend the historical provenance audit or claim that a
new transcript archive has been completed. Original session records and raw
verification records remain outside Git; the committed evidence contains no
private key, password, raw job log, or private local filesystem path.

The GitHub artifact expires on December 5, 2026 at 23:36:55 UTC. A verified local
copy and the original receipts have also been retained outside Git. The vault
backup is owner-confirmed; an independent restore comparison remains unverified.
Minisign does not provide Apple notarization, Windows Authenticode, SmartScreen
reputation, or independent build attestations.

Next: ask the owner whether to publish `v0.1.0` in `mgkwill/lit` from the exact
tested source `67cbd625a372800b7f626de66a43310255c82b82`, using these five archives,
their signatures, both signed manifests, the reviewed public key, and the
0.1.0 release notes. Signing approval is not publication approval.
