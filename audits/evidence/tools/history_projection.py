"""Resolve original evidence identities into the privacy-redacted Git history."""
import argparse
import hashlib
import json
from pathlib import Path


def load(repo):
    path = Path(repo) / 'audits/privacy/history-map.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    assert data['schema_version'] == 1
    baseline = data['original_mit_baseline']
    assert data['original_to_sanitized_commits'][baseline] == baseline
    return data


def commit(repo, original):
    return load(repo).get('original_to_sanitized_commits', {}).get(original, original)


def blob(repo, original):
    return load(repo).get('changed_blobs', {}).get(original, original)


def verify_binding(repo, manifest):
    binding = manifest.get('history_projection')
    if binding:
        path = Path(repo) / binding['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding['sha256']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('identity')
    parser.add_argument('--blob', action='store_true')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    print((blob if args.blob else commit)(repo, args.identity))
