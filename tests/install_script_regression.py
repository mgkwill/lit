"""Check installer paths, failure propagation, and preservation of user settings."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    windows = os.name == 'nt'
    with tempfile.TemporaryDirectory(prefix='openlit-install-test-') as directory:
        root = Path(directory)
        checkout = root / 'checkout with spaces'
        home = root / 'home'
        fake = root / 'bin'
        outside = root / 'outside'
        for path in [checkout, home, fake, outside]:
            path.mkdir()
        script = 'install.ps1' if windows else 'install.sh'
        shutil.copyfile(ROOT / script, checkout / script)
        for name in ['.profile', '.zshrc', '.litconfig']:
            (home / name).write_text('existing settings\n')
        original = {p.name: p.read_bytes() for p in home.iterdir()}
        log = root / 'cargo-args.json'
        helper = fake / 'cargo_mock.py'
        helper.write_text('import json,os,sys\nfrom pathlib import Path\n'
                          'Path(os.environ["OPENLIT_TEST_LOG"]).write_text(json.dumps(sys.argv[1:]))\n'
                          'sys.exit(int(os.environ.get("OPENLIT_TEST_EXIT", "0")))\n')
        if windows:
            (fake / 'cargo.cmd').write_text(f'@"{sys.executable}" "{helper}" %*\r\n')
        else:
            cargo = fake / 'cargo'
            cargo.write_text('#!/bin/sh\nexec "' + sys.executable + '" "' + str(helper) + '" "$@"\n')
            cargo.chmod(0o755)
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                   PATH=str(fake) + os.pathsep + os.environ['PATH'], OPENLIT_TEST_LOG=str(log))
        install_root = root / 'install with spaces'
        command = (['pwsh', '-NoProfile', '-NonInteractive', '-File', str(checkout / script)]
                   if windows else ['bash', str(checkout / script)])
        expected = ['install', '--locked', '--path', str(checkout), '--bin', 'lit']
        for explicit_root in [False, True]:
            options = (['-InstallRoot' if windows else '--root', str(install_root)]
                       if explicit_root else [])
            cargo_options = ['--root', str(install_root)] if explicit_root else []
            for code in [0, 23]:
                result = subprocess.run(command + options, cwd=outside,
                                        env=dict(env, OPENLIT_TEST_EXIT=str(code)),
                                        capture_output=True, text=True, timeout=20)
                assert result.returncode == code, (result.returncode, result.stdout, result.stderr)
                observed = json.loads(log.read_text())
                wanted = expected + cargo_options
                assert len(observed) == len(wanted), (observed, wanted)
                for actual, intended in zip(observed, wanted):
                    if intended in (str(checkout), str(install_root)):
                        # PowerShell can expand the runner's short (8.3) temp path.
                        assert Path(actual).resolve() == Path(intended).resolve(), (actual, intended)
                    else:
                        assert actual == intended, (observed, wanted)
                assert {p.name: p.read_bytes() for p in home.iterdir()} == original
        print(json.dumps({'script': script, 'checkout_with_spaces': True,
                          'locked_local_package': True, 'failure_propagation': True,
                          'home_settings_preserved': True}, sort_keys=True))


if __name__ == '__main__':
    main()
