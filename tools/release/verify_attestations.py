"""Verify final bytes against a manifest and an exact GitHub Actions identity.

Expected repository, workflow, ref, and commit are supplied by the reviewer,
not read from an untrusted archive or attestation predicate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

TARGETS = {
    'x86_64-unknown-linux-gnu': '.tar.gz',
    'aarch64-unknown-linux-gnu': '.tar.gz',
    'x86_64-apple-darwin': '.dmg',
    'aarch64-apple-darwin': '.dmg',
    'x86_64-pc-windows-msvc': '.zip',
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def manifest_files(folder):
    """Require all five 0.1.1 distributions, no duplicates or path escapes."""
    manifest = folder / 'SHA256SUMS'
    if manifest.is_symlink():
        raise ValueError('Checksum manifest must not be a symlink')
    expected = {f'openlit-0.1.1-{target}{ext}' for target, ext in TARGETS.items()}
    seen = set()
    for line in manifest.read_text().splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9._-]+)', line)
        if not match:
            raise ValueError('Malformed checksum entry')
        sha, name = match.groups()
        if name not in expected or name in seen:
            raise ValueError('Unexpected or duplicate distribution')
        path = folder / name
        if path.is_symlink() or not path.is_file() or digest(path) != sha:
            raise ValueError('Distribution is missing or its checksum changed: ' + name)
        seen.add(name)
    if seen != expected:
        raise ValueError('All five distributions are required')
    return [folder / name for name in sorted(seen)] + [manifest]


def verify(folder, repository, commit, ref, workflow):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('An exact owner/repository is required')
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('An exact approved commit SHA is required')
    if not re.fullmatch(r'refs/heads/[A-Za-z0-9/_-]+', ref):
        raise ValueError('An exact reviewed branch ref is required')
    if not re.fullmatch(r'\.github/workflows/[A-Za-z0-9_-]+\.yml', workflow):
        raise ValueError('An exact workflow path is required')
    results = {}
    for path in manifest_files(folder):
        # Source and signer digests are enforced by gh against certificate claims.
        # Merely finding a matching subject in JSON is not verification.
        result = subprocess.run([
            'gh', 'attestation', 'verify', str(path), '--repo', repository,
            '--signer-workflow', repository + '/' + workflow,
            '--source-digest', commit, '--signer-digest', commit, '--source-ref', ref,
            '--cert-oidc-issuer', 'https://token.actions.githubusercontent.com',
            '--predicate-type', 'https://slsa.dev/provenance/v1', '--format', 'json',
        ], check=True, capture_output=True, text=True, timeout=180)
        verified = json.loads(result.stdout)
        if not isinstance(verified, list) or not verified:
            raise ValueError('No cryptographically verified attestation returned')
        results[path.name] = verified
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--ref', required=True)
    parser.add_argument('--workflow', default='.github/workflows/platform-signing.yml')
    args = parser.parse_args()
    print(json.dumps(verify(args.folder, args.repository, args.commit, args.ref, args.workflow), indent=2))


if __name__ == '__main__':
    main()
