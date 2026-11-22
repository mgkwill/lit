"""Verify reachable privacy projections without publishing the private value list."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evidence/tools'))
from redaction_policy import sanitize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', type=Path, help='Owner-held original replacement policy')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    projection = json.loads((repo/'audits/privacy/history-map.json').read_text())
    policy = json.loads(args.policy.read_text()) if args.policy else None
    if policy:
        assert hashlib.sha256(args.policy.read_bytes()).hexdigest() == projection['private_redaction_policy_sha256']
    def git(*values):
        return subprocess.check_output(['git', '-C', str(repo), *values])
    def check(data):
        if policy:
            assert sanitize(data, policy) == data, 'Identifying data remains; details withheld'
    objects = [line.split(b' ', 1) for line in git('rev-list', '--objects', '--all').splitlines()]
    metadata = subprocess.run(['git', '-C', str(repo), 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)'],
        input=b'\n'.join(row[0] for row in objects)+b'\n', stdout=subprocess.PIPE, check=True).stdout.splitlines()
    process = subprocess.Popen(['git', '-C', str(repo), 'cat-file', '--batch'], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    count = archives = 0
    for row, meta in zip(objects, metadata):
        kind = meta.split()[1]
        if len(row) > 1:
            check(row[1])
        if kind not in (b'blob', b'commit', b'tag'):
            continue
        process.stdin.write(row[0]+b'\n')
        process.stdin.flush()
        header = process.stdout.readline().split()
        data = process.stdout.read(int(header[2]))
        process.stdout.read(1)
        check(data)
        if kind == b'blob':
            count += 1
            if data[:2] == b'\x1f\x8b':
                unpacked = gzip.decompress(data)
                check(unpacked)
                allowed = {'record','utc','type','sha256','payload_type','redaction','role',
                           'policy_mentions','tool','call_id','input_sha256'}
                for line in unpacked.splitlines():
                    item = json.loads(line)
                    assert set(item) <= allowed
                    assert item['redaction'] == 'body-withheld'
                archives += 1
    process.stdin.close()
    assert process.wait() == 0
    commits = projection['original_to_sanitized_commits']
    baseline = projection['original_mit_baseline']
    assert commits[baseline] == baseline
    assert len(set(commits.values())) == len(commits)
    assert int(git('rev-list', '--count', projection['projected_head'])) == len(commits)
    for value in commits.values():
        git('cat-file', '-e', value+'^{commit}')
    subprocess.run(['git', '-C', str(repo), 'fsck', '--full', '--strict'], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    print(json.dumps({'mapped_commits':len(commits),'reachable_blobs':count,
                      'compressed_versions_checked':archives,'git_fsck':'passed',
                      'known_identifying_values':'absent' if policy else 'not checked: private --policy required',
                      'mit_baseline':'unchanged'}))


if __name__ == '__main__':
    main()
