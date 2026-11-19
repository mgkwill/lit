"""Capture and seal a quorum backup for a supervisor running off the voters."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import zlib

from task_broker import private_file
from task_broker_checkpoint import sha256
from task_broker_quorum import EtcdStore
from task_broker_quorum_backup import capture, decode_head, private_directory
from task_broker_replica import Client, Rejected, ReplicaUnavailable
from task_ownership import AuthorityBusy, state_lock


def capture_confirmed(store, manager, manifest):
    for attempt in range(3):
        before = decode_head(manager.call('/head'),manifest)
        report, archive = capture(store,manifest)
        after = decode_head(manager.call('/head'),manifest)
        if before==report['head']==after and (not report['writer_pending'] or attempt==2):
            return report, archive, after
        if attempt<2:
            time.sleep(.25*(attempt+1))
    raise ReplicaUnavailable('acknowledged head changed during backup')


def seal(openssl, recipient, payload, destination):
    command = [str(openssl),'cms','-encrypt','-binary','-aes-256-gcm','-outform','DER',
               '-out',str(destination),str(recipient)]
    result = subprocess.run(command,input=payload,capture_output=True,timeout=60)
    if result.returncode or private_file(destination).stat().st_size==0:
        raise RuntimeError('archive encryption failed')


def sync_directory(path):
    fd = os.open(path,os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def run_backup(config, store, manager):
    vault = Path(config['vault'])
    private_directory(vault)
    recipient = private_file(config['recipient'])
    openssl = Path(config['openssl'])
    if not openssl.is_absolute():
        raise Rejected('OpenSSL executable path must be absolute')
    with state_lock(vault,'.quorum-backup.lock','quorum backup already running'):
        report, archive, head = capture_confirmed(store,manager,config['manifest'])
        started = datetime.now(timezone.utc)
        directory = vault / (started.strftime('%Y%m%dT%H%M%S.%fZ')+
                             f"-e{head['epoch']}-r{head['revision']}")
        if os.path.lexists(directory):
            raise Rejected('backup artifact directory already exists')
        with tempfile.TemporaryDirectory(prefix='.sealing-',dir=vault) as holding:
            pending = Path(holding)/'publish'
            pending.mkdir(mode=0o700)
            encrypted = pending/'checkpoint.cms'
            seal(openssl,recipient,archive,encrypted)
            receipt = report|{'independent_head':head,'ciphertext_sha256':sha256(encrypted),
                              'recipient_pem_sha256':sha256(recipient),'captured_at':started.isoformat()}
            written = pending/'receipt.json'
            written.write_text(json.dumps(receipt,sort_keys=True)+'\n')
            for path in (encrypted,written):
                with path.open('rb') as handle:
                    os.fsync(handle.fileno())
            sync_directory(pending)
            pending.rename(directory)
            sync_directory(vault)
    return {'event':'quorum_backup_sealed','artifact':directory.name,
            'epoch':head['epoch'],'revision':head['revision'],
            'recovery_blockers':report['recovery_blockers']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = json.loads(private_file(args.config).read_text())
        manifest = config['manifest']
        store = EtcdStore(config['quorum'],manifest['repo'],cluster_id=config['cluster_id'])
        manager = Client(config['manager'])
        result = run_backup(config,store,manager)
    except (KeyError,TypeError,ValueError,OSError,RuntimeError,sqlite3.Error,
            subprocess.TimeoutExpired,zlib.error) as error:
        detail = str(error) if isinstance(error,(Rejected,ReplicaUnavailable,AuthorityBusy)) else type(error).__name__
        print(json.dumps({'event':'quorum_backup_failed','error':detail}),file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__=='__main__':
    sys.exit(main())
