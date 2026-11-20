"""Check active guide links and execute the documented local quick start."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def guide_links():
    guides = list(ROOT.glob('*.md')) + list((ROOT / 'docs').rglob('*.md'))
    guides += [ROOT / 'tools/README.md', ROOT / 'tools/deployment/README.md',
               ROOT / 'tests/commands/README.md']
    checked = 0
    for guide in guides:
        for target in re.findall(r'\]\(([^\s)]+)\)', guide.read_text()):
            if '://' in target or target.startswith(('#', 'mailto:')):
                continue
            path = unquote(target.split('#', 1)[0])
            assert (guide.parent / path).exists(), (guide.relative_to(ROOT), target)
            checked += 1
    return checked


def run(binary):
    manifest = tomllib.loads((ROOT / 'Cargo.toml').read_text())
    package = manifest['package']
    assert package['name'] == 'openlit' and package['publish'] is False
    assert manifest['lib']['name'] == 'lit'
    assert [item['name'] for item in manifest['bin']] == ['lit']
    with tempfile.TemporaryDirectory(prefix='openlit-docs-') as directory:
        root = Path(directory)
        home = root / 'home'
        home.mkdir()
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                   XDG_CONFIG_HOME=str(home / '.config'),
                   PATH=str(binary.parent) + os.pathsep + os.environ['PATH'],
                   GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0', LIT_OUTPUT='json')
        for key in list(env):
            if key.startswith('LIT_') and key != 'LIT_OUTPUT':
                del env[key]

        def lit(*args, cwd=root):
            result = subprocess.run([str(binary), *args], cwd=cwd, env=env,
                                    capture_output=True, text=True, timeout=30, check=True)
            return result.stdout.strip()

        (home / '.litconfig').write_bytes((ROOT / '.litconfig.example').read_bytes())
        json.loads(lit('config', 'show'))
        version = lit('--version')
        assert version == 'lit ' + package['version'], version
        assert 'OpenLit' in lit('--help')
        ontology = json.loads(lit('ontology'))['ontology']
        assert ontology['name'] == 'OpenLit' and ontology['version'] == package['version']
        assert 'ML-KEM' not in json.dumps(ontology)
        json.loads(lit('schema', '--command', 'add'))
        quickstart = (ROOT / 'QUICKSTART.md').read_text()
        blocks = re.findall(r'```sh\n(.*?)\n```', quickstart, re.S)
        assert blocks[0].startswith('mkdir example')
        assert blocks[1].startswith('lit checkout -b greeting')
        subprocess.run(['sh', '-eu', '-c', blocks[0] + '\n' + blocks[1]],
                       cwd=root, env=env, capture_output=True, text=True, timeout=60, check=True)
        repo = root / 'example'
        assert (repo / 'hello.txt').read_text() == 'hello, team\n'
        status = json.loads(lit('status', cwd=repo))
        assert status['branch'] == 'main' and status['clean'] is True, status
        examples = (ROOT / 'EXAMPLES.md').read_text()
        jsonl = re.search(r'```jsonl\n(.*?)\n```', examples, re.S).group(1)
        (repo / 'hello.txt').write_text('batch edit\n')
        for mode in (['--dry-run', '--fail-on-error'], ['--rollback-on-error']):
            result = subprocess.run([str(binary), 'batch', *mode], input=jsonl+'\n',
                                    cwd=repo, env=env, capture_output=True, text=True,
                                    timeout=30, check=True)
            assert json.loads(result.stdout)['failed'] == 0, result.stdout
        transaction = next(block for block in re.findall(r'```sh\n(.*?)\n```', examples, re.S)
                           if block.startswith('lit tx begin'))
        (repo / 'hello.txt').write_text('transaction edit\n')
        subprocess.run(['sh', '-eu', '-c', transaction], cwd=repo, env=env,
                       capture_output=True, text=True, timeout=30, check=True)
        assert json.loads(lit('status', cwd=repo))['clean'] is True
        lit('verify', cwd=repo)
        return {'version': version, 'package': package['name'],
                'quickstart_branch_and_merge': True, 'batch_example': True,
                'transaction_example': True, 'discovery': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=lambda value: Path(value).resolve())
    args = parser.parse_args()
    result = {'local_guide_links': guide_links()}
    result.update(run(args.binary))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
