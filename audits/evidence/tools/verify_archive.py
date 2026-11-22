"""Verify minimized exports; use --originals for full reproduction and commit links."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess

from export_records import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--originals', type=Path)
    args = parser.parse_args()
    base = Path(__file__).resolve().parents[1]
    repo = base.parents[1]
    manifest = json.loads((base / 'manifest.json').read_text())
    commits = json.loads((base / manifest['commit_record_index']).read_text())
    expected = subprocess.check_output(['git', 'rev-list',
        '98c59bb2dbebcf7f575993a4ad89e1300a578c83..' + manifest['source_boundary']], cwd=repo, text=True)
    assert set(expected.splitlines()) == {c['commit'] for c in commits}
    sessions = {}
    count = 0
    for receipt in manifest['sessions']:
        archive = (base / receipt['export_file']).resolve()
        archive.relative_to(base)
        packed = archive.read_bytes()
        assert hashlib.sha256(packed).hexdigest() == receipt['export_sha256']
        data = gzip.decompress(packed)
        assert hashlib.sha256(data).hexdigest() == receipt['uncompressed_sha256']
        rows = [json.loads(line) for line in data.splitlines()]
        assert len(rows) == receipt['original_records']
        assert [x['record'] for x in rows] == list(range(1, len(rows) + 1))
        assert all(x['redaction'] == 'body-withheld' for x in rows)
        count += len(rows)
        if args.originals:
            original = args.originals / receipt['original_name']
            assert original.name == receipt['original_name']
            verify(original, archive, receipt)
            sessions[receipt['session_id']] = original.read_bytes().splitlines(keepends=True)
    assert count == manifest['original_records']
    if args.originals:
        for commit in commits:
            assert commit['records']
            for reference in commit['records']:
                raw = sessions[reference['session_id']][reference['line'] - 1]
                item = json.loads(raw)
                assert item['timestamp'] == reference['utc']
                assert item['type'] == 'response_item'
                assert item['payload']['type'] in ('custom_tool_call_output', 'function_call_output')
                assert re.search(r'\[(?:codex/mit-lit-recovery|main) ' + commit['commit'][:7] + r'\]',
                                 json.dumps(item['payload']))
        for receipt in manifest['sessions']:
            previous = receipt.get('previous_prefix')
            if previous:
                old = b''.join(sessions[receipt['session_id']][:previous['records']])
                assert len(old) == previous['bytes']
                assert hashlib.sha256(old).hexdigest() == previous['sha256']
        routes_file = base / 'receipts/development-input-routes-2026-11-21.json'
        if routes_file.exists():
            routes = json.loads(routes_file.read_text())
            groups = routes['independent_edit_routes'] + [routes['dependency_downloads']]
            for group in groups:
                for reference in group['original_records']:
                    raw = sessions[group['session_id']][reference['record'] - 1]
                    assert hashlib.sha256(raw).hexdigest() == reference['sha256']
                    assert json.loads(raw)['timestamp'] == reference['utc']
        prefix = manifest['prior_prefix']
        data = b''.join(sessions[prefix['session_id']][:prefix['records']])
        assert len(data) == prefix['bytes']
        assert hashlib.sha256(data).hexdigest() == prefix['sha256']
        for excerpt in json.loads((base / 'reviewed-excerpts.json').read_text()):
            payload = json.loads(sessions[excerpt['session_id']][excerpt['record'] - 1])['payload']
            if payload['type'] == 'message':
                text = '\n'.join(c.get('text', '') for c in payload.get('content', [])
                                 if isinstance(c, dict))
            else:
                text = payload['input']
            assert hashlib.sha256(text.encode()).hexdigest() == excerpt['payload_text_sha256']
            if excerpt['record'] in (4744, 4865):
                start = text.index("python3 - <<'PY'\n") + len("python3 - <<'PY'\n")
                text = text[start:text.index('\nPY', start)]
            text = re.sub(r'/Users/[^\s`<>"\']+', '<private-local-path>', text)
            assert text == excerpt['text']
    for filename in ['reviewed-excerpts.json', 'source-selector-review.json', 'commit-records.json']:
        data = (base / filename).read_text()
        assert not re.search(r'(?:gh[opusr]_|github_pat_)[A-Za-z0-9]{15,}|-----BEGIN .*PRIVATE KEY|/Users/|10\.10\.', data), filename
    print(json.dumps({'sessions': len(manifest['sessions']), 'records': count,
                      'commits': len(commits), 'export_hashes': 'passed',
                      'original_reproduction': 'passed' if args.originals else 'not requested',
                      'commit_record_links': 'passed' if args.originals else 'not requested'}))


if __name__ == '__main__':
    main()
