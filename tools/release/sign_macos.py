"""Sign a CLI and notarize a stapled DMG using an existing keychain identity.

Secrets stay in the keychain. Neither ad-hoc signing nor Apple Development
certificates are accepted. Run only on a reviewed, dedicated signing machine.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def run(*args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=1800)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed ({result.returncode}): {result.stderr}')
    return result.stdout + result.stderr


def require_identity(team, certificate):
    if not re.fullmatch(r'[A-Z0-9]{10}', team):
        raise ValueError('A ten-character Apple Team ID is required')
    if not re.fullmatch(r'[A-Fa-f0-9]{40}', certificate):
        raise ValueError('An exact Developer ID certificate SHA-1 fingerprint is required')
    identities = run('security', 'find-identity', '-v', '-p', 'codesigning')
    if not re.search(r'\b' + certificate.upper() + r' "Developer ID Application: [^"\n]+ \(' + team + r'\)"', identities):
        raise ValueError('The requested Developer ID Application identity is unavailable')


def verify(path, team, certificate, runtime=True):
    if not re.fullmatch(r'[A-Z0-9]{10}', team) or not re.fullmatch(r'[A-Fa-f0-9]{40}', certificate):
        raise ValueError('Expected Apple Team ID and certificate fingerprint are required')
    requirement = (f'anchor apple generic and certificate leaf[subject.OU] = "{team}" '
                   'and certificate leaf[field.1.2.840.113635.100.6.1.13] exists '
                   f'and certificate leaf = H"{certificate}"')
    run('codesign', '--verify', '--strict', '-R', requirement, path)
    details = run('codesign', '-d', '--verbose=4', path)
    if not re.search(r'^Timestamp=.+$', details, re.M):
        raise ValueError('Apple secure timestamp missing')
    if runtime and not re.search(r'^CodeDirectory .*flags=.*\bruntime\b', details, re.M):
        raise ValueError('Hardened runtime missing')
    return details


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--team', required=True)
    parser.add_argument('--certificate', required=True)
    parser.add_argument('--notary-profile', required=True)
    args = parser.parse_args()
    if args.output.suffix != '.dmg' or args.output.exists():
        raise ValueError('Use a new .dmg output path')
    require_identity(args.team, args.certificate)
    with tempfile.TemporaryDirectory(prefix='openlit-sign-') as temp:
        work = Path(temp)
        payload = work / 'payload'
        payload.mkdir()
        binary = payload / 'lit'
        shutil.copy2(args.binary, binary)
        for name in ('LICENSE', 'NOTICE.md', 'THIRD_PARTY_NOTICES.txt'):
            shutil.copy2(ROOT / name, payload / name)
        run('codesign', '--force', '--sign', args.certificate, '--options', 'runtime',
            '--timestamp', '--identifier', 'org.openlit.lit', binary)
        verify(binary, args.team, args.certificate)
        dmg = work / 'candidate.dmg'
        run('hdiutil', 'create', '-quiet', '-format', 'UDZO', '-volname', 'OpenLit',
            '-srcfolder', payload, dmg)
        run('codesign', '--sign', args.certificate, '--timestamp',
            '--identifier', 'org.openlit.distribution', dmg)
        submission = json.loads(run('xcrun', 'notarytool', 'submit', dmg, '--wait',
                                    '--keychain-profile', args.notary_profile, '--output-format', 'json'))
        if submission.get('status') != 'Accepted':
            raise ValueError(f'Notarization was not accepted: {submission.get("status")}')
        run('xcrun', 'stapler', 'staple', dmg)
        run('xcrun', 'stapler', 'validate', dmg)
        verify(dmg, args.team, args.certificate, runtime=False)
        if run('spctl', '--status').strip() != 'assessments enabled':
            raise ValueError('Gatekeeper assessment is disabled')
        run('spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', dmg)
        # Only expose a distribution after every signing gate succeeds.
        with args.output.open('xb') as dest, dmg.open('rb') as source:
            shutil.copyfileobj(source, dest)
        receipt = {'notarization_id': submission['id'], 'status': submission['status'],
                   'team': args.team, 'certificate_sha1': args.certificate.upper(),
                   }
        with args.output.open('rb') as stream:
            receipt['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        args.output.with_suffix('.notary.json').write_text(json.dumps(receipt, indent=2) + '\n')


if __name__ == '__main__':
    main()
