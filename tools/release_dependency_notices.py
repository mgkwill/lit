"""Collect shipped Cargo dependency license texts without fetching upstream Lit."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'COPYRIGHT')


def render(metadata):
    records = []
    texts = {}
    for package in sorted(metadata['packages'], key=lambda p: (p['name'], p['version'])):
        if package['source'] is None:
            continue
        root = Path(package['manifest_path']).parent
        record = {key: package[key] for key in ('name', 'version', 'license', 'repository', 'source')}
        record['notices'] = []
        paths = {path for path in root.rglob('*') if path.is_file()
                 and path.name.upper().startswith(PREFIXES)
                 and path.suffix not in ('.rs', '.c', '.h')}
        if package.get('license_file'):
            paths.add(root / package['license_file'])
        for path in sorted(paths):
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            # Preserve full license text. Do not interpret source examples as code inputs.
            text = data.decode('utf-8')
            texts[digest] = text
            record['notices'].append({'path': path.relative_to(root).as_posix(), 'sha256': digest})
        records.append(record)
    missing = [p['name'] + '@' + p['version'] for p in records if not p['notices']]
    manifest = {'scope': 'All resolved Cargo packages, including dev and non-native targets. '
                         'This is an inventory, not a completed license compliance review.',
                'missing_bundled_notices': missing, 'packages': records}
    lines = ['OpenLit third-party notices', '', manifest['scope'], '',
             'Packages without a bundled notice need review before public distribution:',
             *missing, '', 'Package inventory', '=================']
    for p in records:
        lines.extend(['', p['name'] + ' ' + p['version'], 'Declared license: ' + str(p['license']),
                      'Repository: ' + str(p['repository'])])
        lines.extend('Notice: ' + n['path'] + ' (SHA-256 ' + n['sha256'] + ')' for n in p['notices'])
    lines.extend(['', 'Full notice texts, grouped by SHA-256', '===================================='])
    for digest, body in sorted(texts.items()):
        lines.extend(['', 'SHA-256 ' + digest, '', body])
    return {ROOT / 'THIRD_PARTY_NOTICES.txt': '\n'.join(lines) + '\n',
            ROOT / 'docs/dependencies.json': json.dumps(manifest, indent=2, sort_keys=True) + '\n'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    metadata = json.loads(subprocess.check_output(
        ['cargo', 'metadata', '--locked', '--format-version', '1'], cwd=ROOT, text=True))
    outputs = render(metadata)
    for path, content in outputs.items():
        if args.check:
            if not path.exists() or path.read_bytes() != content.encode():
                sys.exit('Dependency notice inventory is stale: ' + str(path.relative_to(ROOT)))
        else:
            path.write_bytes(content.encode())
    print('Dependency notice inventory matches the lockfile' if args.check else 'Dependency notice inventory generated')


if __name__ == '__main__':
    main()
