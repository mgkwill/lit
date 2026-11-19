"""Prepare an overlap or retirement config for one broker client certificate."""
import argparse
import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import sys

from task_broker import private_file
from task_broker_checkpoint import install_new, staging_file


def fingerprint(path):
    pem=path.read_text()
    if pem.count('-----BEGIN CERTIFICATE-----')!=1 or pem.count('-----END CERTIFICATE-----')!=1:
        raise ValueError('expected one PEM certificate')
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def openssl(*args):
    run=subprocess.run(['openssl',*args],capture_output=True,timeout=15)
    if run.returncode:
        raise ValueError('certificate verification failed')
    return run.stdout.decode().strip()


def expiry(path):
    raw=openssl('x509','-in',str(path),'-noout','-enddate')
    name, value=raw.split('=',1)
    if name!='notAfter':
        raise ValueError('certificate expiration unavailable')
    return ssl.cert_time_to_seconds(value)


def prepare(config, old_cert, new_cert, phase):
    old=fingerprint(old_cert)
    new=fingerprint(new_cert)
    if new==old:
        raise ValueError('renewal requires a distinct certificate')
    principals=config['principals']
    principal=principals.get(old)
    if not isinstance(principal,dict) or principal.get('role') not in ('worker','scheduler','policy_observer'):
        raise ValueError('old certificate has no configured principal')
    existing=principals.get(new)
    if existing is not None and existing!=principal:
        raise ValueError('new certificate belongs to a different principal')
    if phase=='retire' and existing!=principal:
        raise ValueError('install the overlap config first')
    openssl('verify','-purpose','sslclient','-CAfile',str(config['client_ca']),str(new_cert))
    not_after=expiry(new_cert)
    if not_after<=expiry(old_cert):
        raise ValueError('renewed certificate must extend the expiration')
    if phase=='overlap':
        principals[new]=principal
    else:
        del principals[old]
    return {'phase':phase,'old_sha256':old,'new_sha256':new,'role':principal['role'],
            'new_expires_at':not_after}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--old-cert',type=Path,required=True)
    parser.add_argument('--new-cert',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('phase',choices=('overlap','retire'))
    args=parser.parse_args()
    try:
        config=json.loads(private_file(args.config).read_text())
        receipt=prepare(config,args.old_cert,args.new_cert,args.phase)
        with staging_file(args.output.parent) as staged:
            staged.write_text(json.dumps(config,indent=2)+'\n')
            install_new(staged,args.output)
        print(json.dumps(receipt))
    except ValueError as error:
        print(json.dumps({'error':str(error)}),file=sys.stderr)
        return 1
    except (OSError,KeyError,subprocess.TimeoutExpired) as error:
        print(json.dumps({'error':type(error).__name__}),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
