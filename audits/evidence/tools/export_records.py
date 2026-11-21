"""Create minimized, hash-bound session exports without exposing tool payloads.

Inputs are private frozen JSONL files. This is evidence processing, never a source
input for OpenLit implementation. The manifest contains no original local paths.
"""
import argparse
import collections
import gzip
import hashlib
import json
from pathlib import Path
import re

POLICIES = {'mit-baseline': r'98c59bb|MIT.baseline|MIT snapshot',
            'upstream-black-box': r'black.box|post.MIT',
            'publication-approval': r'ask.{0,30}publish|before publishing'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(data):
    return (json.dumps(data, sort_keys=True, ensure_ascii=True) + '\n').encode()


def safe_atom(value):
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_./:-]{1,160}', value):
        return value
    return None


def project(raw, number):
    record = json.loads(raw)
    payload = record.get('payload', {})
    if not isinstance(payload, dict):
        payload = {}
    result = {'record': number, 'utc': record.get('timestamp'),
              'type': safe_atom(record.get('type')), 'sha256': digest(raw),
              'payload_type': safe_atom(payload.get('type')),
              'redaction': 'body-withheld'}
    # Only fixed structural fields are exported. Never export arbitrary text,
    # tool arguments/results, reasoning, private paths, URLs, tokens or images.
    if record.get('type') == 'response_item':
        kind = payload.get('type')
        if kind == 'message':
            result['role'] = safe_atom(payload.get('role'))
            text = '\n'.join(c.get('text', '') for c in payload.get('content', [])
                             if isinstance(c, dict))
            result['policy_mentions'] = [name for name, regex in POLICIES.items()
                                         if re.search(regex, text, re.I)]
        elif kind in ('custom_tool_call', 'function_call'):
            result['tool'] = safe_atom(payload.get('name'))
            result['call_id'] = safe_atom(payload.get('call_id'))
            args = payload.get('input', payload.get('arguments', ''))
            raw_args = args.encode() if isinstance(args, str) else encode(args)
            result['input_sha256'] = digest(raw_args)
        elif kind in ('custom_tool_call_output', 'function_call_output'):
            result['call_id'] = safe_atom(payload.get('call_id'))
    return result


def export(source, destination, session, classification):
    raw = source.read_bytes()
    lines = raw.splitlines(keepends=True)
    assert lines and all(line.endswith(b'\n') for line in lines), 'Incomplete JSONL boundary'
    assert json.loads(lines[0])['payload']['id'] == session
    rows = [project(line, i) for i, line in enumerate(lines, 1)]
    data = b''.join(encode(row) for row in rows)
    packed = gzip.compress(data, mtime=0)
    destination.write_bytes(packed)
    return {'session_id': session, 'classification': classification,
            'original_name': source.name, 'original_records': len(lines),
            'original_bytes': len(raw), 'original_sha256': digest(raw),
            'first_utc': rows[0]['utc'], 'last_utc': rows[-1]['utc'],
            'export_file': destination.name, 'export_bytes': len(packed),
            'export_sha256': digest(packed), 'uncompressed_sha256': digest(data),
            'record_types': dict(collections.Counter(row['type'] for row in rows)),
            'redactions': {'body-withheld': len(rows)}}


def verify(source, destination, receipt):
    raw = source.read_bytes()
    packed = destination.read_bytes()
    assert digest(raw) == receipt['original_sha256']
    assert len(raw) == receipt['original_bytes']
    assert digest(packed) == receipt['export_sha256']
    data = gzip.decompress(packed)
    assert digest(data) == receipt['uncompressed_sha256']
    lines = raw.splitlines(keepends=True)
    assert len(lines) == receipt['original_records']
    assert data == b''.join(encode(project(line, i)) for i, line in enumerate(lines, 1))
    # Verify actual projections rather than relying on a secret-pattern scan.
    return len(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--session', required=True)
    parser.add_argument('--classification', required=True)
    args = parser.parse_args()
    receipt = export(args.source, args.destination, args.session, args.classification)
    verify(args.source, args.destination, receipt)
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
