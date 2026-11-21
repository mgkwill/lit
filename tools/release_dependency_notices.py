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
    supplements = json.loads((ROOT / 'docs/licenses/supplements.json').read_text())['packages']
    supplements = {(p['name'], p['version']): p for p in supplements}
    used_supplements = set()
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
        # r-efi carries its complete MIT permission and copyright in AUTHORS.
        if package['name'] == 'r-efi':
            paths.add(root / 'AUTHORS')
        for path in sorted(paths):
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            # Preserve full license text. Do not interpret source examples as code inputs.
            text = data.decode('utf-8')
            texts[digest] = text
            record['notices'].append({'path': path.relative_to(root).as_posix(), 'sha256': digest})
        key = (package['name'], package['version'])
        if key in supplements:
            supplement = supplements[key]
            vcs = json.loads((root / '.cargo_vcs_info.json').read_text())
            if (vcs['git']['sha1'] != supplement['revision']
                    or vcs['path_in_vcs'] != supplement['path_in_vcs']):
                raise ValueError('Supplement revision does not match package: ' + str(key))
            if 'selected_license' in supplement:
                # These selections use simple SPDX OR alternatives, not a general parser.
                alternatives = (package['license'] or '').split(' OR ')
                selected = supplement['selected_license']
                if selected not in alternatives or any(' AND ' in x for x in alternatives):
                    raise ValueError('Selected license is not a declared alternative: ' + str(key))
                record['selected_license'] = selected
            for notice in supplement['notices']:
                path = (ROOT / notice['path']).resolve()
                path.relative_to((ROOT / 'docs/licenses').resolve())
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                if digest != notice['sha256']:
                    raise ValueError('Supplement digest mismatch: ' + notice['path'])
                if 'source_path' in notice:
                    source = (root / notice['source_path']).resolve()
                    source.relative_to(root.resolve())
                    original = source.read_bytes()
                    if hashlib.sha256(original).hexdigest() != notice['source_sha256']:
                        raise ValueError('Notice source digest mismatch: ' + notice['source_path'])
                    lines = original.splitlines(keepends=True)
                    start, end = notice['source_start_line'], notice['source_end_line']
                    if not (1 <= start <= end <= len(lines)) or b''.join(lines[start-1:end]) != data:
                        raise ValueError('Notice excerpt mismatch: ' + notice['path'])
                texts[digest] = data.decode('utf-8')
                record['notices'].append(dict(notice, origin=notice.get('origin', 'pinned dependency repository'),
                                              revision=supplement['revision']))
            used_supplements.add(key)
        records.append(record)
    if used_supplements != set(supplements):
        raise ValueError('Notice supplements contain packages absent from the lockfile')
    missing = [p['name'] + '@' + p['version'] for p in records if not p['notices']]
    manifest = {'scope': 'All resolved Cargo packages, including dev and non-native targets. '
                         'This is an inventory, not a completed license compliance review.',
                'missing_notices': missing, 'packages': records}
    lines = ['OpenLit third-party notices', '', manifest['scope'], '',
             'Packages without a collected notice need review before public distribution:',
             *missing, '', 'Package inventory', '=================']
    for p in records:
        lines.extend(['', p['name'] + ' ' + p['version'], 'Declared license: ' + str(p['license']),
                      'Repository: ' + str(p['repository'])])
        if 'selected_license' in p:
            lines.append('Selected package license: ' + p['selected_license'] +
                         '; separate bundled notices remain applicable.')
        lines.extend('Notice: ' + n['path'] + ' (SHA-256 ' + n['sha256'] + ')' for n in p['notices'])
        lines.extend('Notice source: ' + n['url'] for n in p['notices'] if 'url' in n)
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
