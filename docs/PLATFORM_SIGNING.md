# Optional platform signing and attestations

This is an optional future path, not a requirement for OpenLit 0.1.0.
The first release uses [Minisign](RELEASE_CREDENTIALS.md). Apple notarization,
Windows Authenticode, and GitHub attestations can be added separately.
This workflow remains disabled until its credentials and workers are configured.
It has no release or tag creation step.

## What the owner needs to provide

Provide **identity names and secure-store references**, not keys or passwords in
chat, Git, or receipts.

| Purpose | Required identity and access |
| --- | --- |
| macOS executable and disk image | An Apple **Developer ID Application** certificate with its private key, exact certificate SHA-1 fingerprint, and ten-character Team ID. An Apple Development certificate is insufficient. |
| Apple notarization | A `notarytool` keychain profile on each signing worker. Provision it securely using an App Store Connect API key (key ID, issuer ID, private `.p8`) or the account's supported notarization credentials. Give the workflow the profile name only. |
| Windows executable | A trusted code-signing publisher certificate, exact thumbprint, and its hardware or hosted key provider. The prepared adapter uses Windows `CurrentUser\\My` plus SignTool; the selected provider must expose the key there. Supply the provider's HTTPS RFC 3161 timestamp URL. Providers that only offer a remote signing API need a provider-specific adapter before enabling the workflow. |
| GitHub provenance | An approved repository supporting artifact attestations. Private attestations require Enterprise Cloud. Actions uses its own short-lived `GITHUB_TOKEN` and OIDC identity; no personal signing token is required. |

The November 20 check found one local Apple Development identity, no Developer
ID Application identity, and no Actions secrets or environments in
`mgkwill/lit-release-qualification`. That repository is private and owned by a
personal account. Its present configuration does not establish private
attestation availability. Choose an eligible repository; do not change visibility
to get around this gate.

This signing setup does not request GitHub App writer tokens or change the
100latent sandbox's merge rules. **Actions-only** still means the required merge
check must come from GitHub Actions, not a manually posted status or bypass actor.

## Configure the prepared workflow

`.github/workflows/platform-signing.yml` is manual only. It rejects a run unless all
configuration below is present and the approved source equals the dispatched
workflow's exact SHA. No PR receives publisher credentials.

The initial adapter expects dedicated **ephemeral** signing workers: macOS Intel,
macOS Apple Silicon, and Windows x86-64, each carrying the `openlit-signing` label
and its normal OS/architecture labels. Provisioning those workers is still
pending. Do not label existing shared lab machines or the management Mac as
signing workers. If the publisher chooses a hosted signing service instead,
finish and review that adapter first. GitHub-hosted workers perform Linux builds
and download and test all five final distributions without publisher keys.

Create a protected `release-signing` environment in the selected private
repository, with a required reviewer, no self-approval or administrator bypass, and deployment restricted
to the reviewed release branch. Verify those protection features are actually
available before adding credentials or registering signing workers. Restrict the
runner group to the reviewed workflow. Install the key providers, SignTool,
Rustup, Git, Python prerequisites, and the notarization profile securely. Disable
shared build caches and dispose of each signing worker after its one job.

Set these environment variables (not raw secret values):

- `OPENLIT_SIGNING_REPOSITORY`: exact `owner/repository`.
- `OPENLIT_SIGNING_APPROVED_SOURCE`: the reviewed full commit SHA.
- `OPENLIT_APPLE_TEAM`, `OPENLIT_APPLE_CERTIFICATE`: Team ID and certificate fingerprint.
- `OPENLIT_NOTARY_PROFILE`: existing keychain profile name.
- `OPENLIT_WINDOWS_CERTIFICATE`, `OPENLIT_TIMESTAMP_URL`: certificate fingerprint and timestamp service.
- `OPENLIT_SIGNING_ENABLED`: set to `true` **last**, after the configuration review.

The workflow pins its actions and Rust 1.100.0, builds with the lockfile, checks
signature vectors and notices, then signs and packages. macOS gets a signed,
notarized, stapled `.dmg`; Windows gets a ZIP containing the Authenticode-signed
`lit.exe`; Linux gets a `.tar.gz`. License and dependency notices accompany each
executable. Only the attestation job receives `id-token: write` and
`attestations: write`; no job has repository content write permission.

The download jobs verify publisher identity, timestamps, macOS notarization and
Gatekeeper, then run batch, MCP, and remote checks on the extracted executable.
The macOS disk image receives a quarantine attribute before assessment. This is
automated Gatekeeper evidence, not proof of every interactive download experience
or Windows SmartScreen reputation. Keep a manual clean-machine download check as
a publication gate.

After all native jobs pass, the workflow computes `SHA256SUMS`, attests the five
final archives and that manifest, and verifies each attestation against the exact
repository, workflow path, source ref, source SHA, signer SHA, and GitHub OIDC
issuer. An unavailable attestation service fails the run. It never substitutes
unsigned JSON for a verified attestation.

## Local commands and review

The native helpers can also run on an already provisioned signing machine:

```sh
python3 tools/release/sign_macos.py /path/to/fresh/lit /path/to/new-candidate.dmg \
  --team "$APPLE_TEAM" --certificate "$APPLE_CERT" --notary-profile "$NOTARY_PROFILE"
```

```powershell
./tools/release/sign_windows.ps1 -Binary C:\candidate\lit.exe `
  -Thumbprint $CertificateThumbprint -Mode Sign -TimestampUrl $TimestampUrl
```

A local signature does not establish a GitHub Actions build identity. Use the
reviewed Actions workflow for the complete candidate. Do not upload arbitrary
local archives and describe their attestation as a verified Actions build.

Download `attested-candidate` and the five native verification receipts, then run:

```sh
python3 tools/release/verify_attestations.py /path/to/downloaded-candidate \
  --repository "$REVIEWED_REPOSITORY" --commit "$REVIEWED_SOURCE_SHA" \
  --ref "$REVIEWED_SOURCE_REF"
```

Provide the expected identity from the approved run, not from a candidate's own
metadata. For an isolated qualification projection, record both the recovery
commit and the projection commit, and verify the exact allowed file differences.
The attestation authenticates the projection SHA; it does not authenticate a
different recovery SHA by implication.

## Remaining gate

Credential setup, protected-environment configuration, ephemeral worker setup,
private attestation availability, and successful live signed-archive qualification
are **pending**. Negative regression tests prove refusal paths, not successful
signing. Ask the owner before publishing, with exact source, signatures, hashes,
checks, destination, and known limitations. Signing approval and publication
approval are different decisions.

Sources: [Apple signing and notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution),
[Microsoft SignTool](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/signtool),
[GitHub attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations),
[GitHub workflow security](https://docs.github.com/en/actions/reference/security/secure-use).
