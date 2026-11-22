# Sign OpenLit downloads with Minisign

OpenLit 0.1.1 uses detached Minisign signatures. Each archive, `SHA256SUMS`, and
`RELEASE.json` gets a matching `.minisig` file. Signing does not change the archive.
The executable is `lit` (`lit.exe` on Windows).

You need Minisign and Python 3.12 or newer. There is no Apple certificate,
Windows publisher certificate, paid signing service, or GitHub Enterprise
requirement for this release path. See the [Minisign instructions](https://jedisct1.github.io/minisign/)
for installation and key handling.

## Create the publisher key once

Run this yourself in a private terminal. Use a strong password and keep an
independent secure backup of the key and password. Do not put either in chat,
Git, a workflow artifact, or the download directory.

```sh
umask 077
mkdir -p "$HOME/.config/openlit-signing"
minisign -G -s "$HOME/.config/openlit-signing/openlit.key" \
  -p "$HOME/.config/openlit-signing/openlit.pub"
chmod 600 "$HOME/.config/openlit-signing/openlit.key"
```

On Windows, use an owner-only directory outside the checkout and run the same
`minisign -G -s ... -p ...` command with Windows paths. Protect the directory
with your account's Windows access controls.

Keep the public key available separately through the project's trusted website
or reviewed repository. Users must obtain that trusted key independently of
an untrusted download. The reviewed public key is in
[`keys/openlit.pub`](../keys/openlit.pub). Its key ID is `E22218B425ADC29C`;
the SHA-256 of that public-key file is
`020727c93e30003cbab9f902459465e55ed6db37e951b2d006b9ec95ac4d77c3`.
The private key must never enter Git. Automated regression tests generate and
delete their own disposable keys.

## Sign in GitHub Actions

Run **Publisher signing (approval required; no release)** from the Actions tab
on `main`. It builds and tests all five platforms, then waits for approval of
the `release-signing` environment. Review the workflow commit and native build
results before approving. Approval lets the job use the publisher key; it does
not approve publication.

The environment must have these settings:

- Required reviewer: `mgkwill` only.
- Allowed ref: the `main` branch only, with no tag rule.
- Administrator bypass disabled.
- Self-review allowed so the owner can approve a run they started.
- Environment secrets `OPENLIT_MINISIGN_SECRET_KEY` (the encrypted key file)
  and `OPENLIT_MINISIGN_PASSWORD` (its password).

These are environment secrets, not repository-wide secrets. Build jobs and
PR checks receive neither. GitHub releases them to the signing job only after
approval. Changing or deleting the environment's protection rules changes that
security boundary; the repository owner must preserve them. The password and
encrypted key remain backed up in the owner's vault.

The signing job verifies the five same-run qualification artifacts and their
source commit before it reads either secret. It writes the encrypted key to
an owner-only temporary file outside Git, sends the password through standard
input to Minisign, and removes the temporary file on success or failure. It
never puts secrets in command arguments, retained files, or logs.

It then verifies all seven signatures and checks rejection of a wrong key,
wrong source, altered archive, altered manifests, and broken signature. Only
a fully verified result is uploaded as `openlit-0.1.1-publisher-signed`, retained
for 14 days. Inside are `bundle/` with the five archives, two manifests, and
their signatures; the public key; and assembly, signing, and verification
receipts. The artifact's source commit identifies the candidate being signed.

This workflow has read-only repository permissions and cannot create a release
or tag. Publishing `0.1.1` remains a separate, explicitly approved action.
Minisign signatures do not provide Apple notarization or Windows Authenticode.

## Sign the approved archives

Put only the five fresh archives in a new directory outside the checkout:

- `openlit-0.1.1-x86_64-unknown-linux-gnu.tar.gz`
- `openlit-0.1.1-aarch64-unknown-linux-gnu.tar.gz`
- `openlit-0.1.1-x86_64-apple-darwin.tar.gz`
- `openlit-0.1.1-aarch64-apple-darwin.tar.gz`
- `openlit-0.1.1-x86_64-pc-windows-msvc.zip`

Replace `FULL_APPROVED_COMMIT_SHA` below with the reviewed 40-character commit.
The source identity is the publisher's signed statement, not an independent
build attestation. Retain the build receipts that support it.

```sh
python3 tools/release/minisign_release.py sign /path/to/candidate \
  --secret-key "$HOME/.config/openlit-signing/openlit.key" \
  --public-key "$HOME/.config/openlit-signing/openlit.pub" \
  --commit FULL_APPROVED_COMMIT_SHA
```

Minisign prompts for the password locally. The helper refuses a key inside a
Git checkout or the candidate directory, and refuses permissive Unix key modes.
It creates the checksum and release manifests, signs all seven files, then
verifies them with the supplied public key. A failed signing attempt removes
its generated metadata and signatures while preserving the original archives.
It does not publish, upload, create a tag, or change branch rules.

## Verify before extracting or running

Use the approved source SHA and an independently trusted public key:

```sh
python3 tools/release/minisign_release.py verify /path/to/candidate \
  --public-key /path/to/trusted/openlit.pub --commit FULL_APPROVED_COMMIT_SHA
```

This checks every signature, exact filenames, all five targets, checksums,
version, source SHA, and candidate purpose. Missing files, extra files, symlinks,
altered content, and a different key are rejected. No private key is needed.

For one downloaded archive, Minisign alone verifies its detached signature:

```sh
minisign -V -H -p /path/to/trusted/openlit.pub \
  -m openlit-0.1.1-aarch64-apple-darwin.tar.gz
```

The full helper additionally verifies the release metadata and complete target
set. Do not obtain a new public key solely from the same download you are trying
to authenticate.

Run native smoke tests on the signed final bytes on each target. For example,
on Apple Silicon, with the full candidate directory available:

```sh
python3 tests/qualify_release_archive.py \
  /path/to/candidate/openlit-0.1.1-aarch64-apple-darwin.tar.gz \
  aarch64-apple-darwin --minisign-public-key /path/to/trusted/openlit.pub \
  --source-commit FULL_APPROVED_COMMIT_SHA
```

## Automated qualification uses test keys

`.github/workflows/release.yml` builds all five targets and uses disposable
keys outside the checkout to sign fresh archives. It verifies signatures before
unpacking, rejects tampered copies and wrong keys, then exercises the extracted
binary. Public test keys and receipts are retained; private test keys are deleted.

These test candidates say `qualification` in their signed metadata. The normal
release verifier rejects them unless `--qualification` is explicitly passed.
`--target` allows an explicit subset for local tests; a full release uses the
default five-target requirement. Qualification artifacts are not publisher-signed
releases. The separate approval-protected publisher workflow converts the
qualified archive bytes into a publisher-signed candidate; neither workflow
publishes a release.

## What this signature means

Minisign proves that the bytes were approved by the holder of the trusted key.
It does not provide Apple notarization, Windows Authenticode, SmartScreen
reputation, or independent build provenance. OS warnings can remain. Do not
label these archives as notarized or platform-signed. The
[optional platform workflow](PLATFORM_SIGNING.md) is separate.

Before publishing, review the actual publisher key, exact source, final archive
hashes, native smoke results, and release notes with the owner. Ask for publication
approval. Creating signatures is not permission to publish.
