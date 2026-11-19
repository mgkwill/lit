"""Copy broker authority to private storage and recover it into a new quorum."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import zlib

import github_queue_coordinator as queue
from task_broker import private_file
from task_broker_checkpoint import install_new, staging_file, verify_database
from task_broker_quorum import EtcdStore, MAX_COMPRESSED, checked_cluster_id, snapshot_bytes
from task_broker_replica import Rejected, ReplicaUnavailable, digest


KEYS = ('head','snapshot','writer')
HEAD_FIELDS = ('holder','epoch','revision','sha256','binding')
MAX_HEADER = 4096


def decode_head(head, manifest):
    if (set(head)!=set(HEAD_FIELDS) or head['binding']!=manifest
            or head['holder'] not in ('primary','standby')
            or type(head['epoch']) is not int or head['epoch']<1
            or type(head['revision']) is not int or head['revision']<0
            or not re.fullmatch(r'[0-9a-f]{64}',head['sha256'])):
        raise Rejected('backup authority binding or version differs')
    return head


def validate_sqlite(compressed, head):
    if len(compressed)>MAX_COMPRESSED:
        raise Rejected('backup checkpoint is too large')
    snapshot = snapshot_bytes(compressed)
    if digest(snapshot)!=head['sha256']:
        raise Rejected('backup checkpoint digest differs')
    states = (
        ('publication_jobs','phase',('done','rejected')),
        ('registration_jobs','phase',('done','rejected')),
        ('publications','phase',('published',)),
        ('actions','status',('denied','observed','fenced_by_closure')),
        ('proposals','phase',sorted(queue.TERMINAL)),
    )
    blockers = []
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('PRAGMA trusted_schema=OFF')
        db.deserialize(snapshot)
        db.execute('PRAGMA query_only=ON')
        verify_database(db,head['binding'])
        for table, column, terminal in states:
            placeholders = ','.join('?' for _ in terminal)
            query = f'SELECT 1 FROM {table} WHERE {column} IS NULL OR {column} NOT IN ({placeholders}) LIMIT 1'
            if db.execute(query,terminal).fetchone():
                blockers.append(table)
        if db.execute("SELECT 1 FROM claims WHERE phase='draining' LIMIT 1").fetchone():
            blockers.append('draining_claims')
    return blockers


def private_directory(path):
    mode = path.lstat().st_mode
    if not stat.S_ISDIR(mode) or mode & 0o077:
        raise Rejected('backup directory must be private')


def capture(store, manifest):
    records, cluster, writer_pending = store.read_backup_state()
    if not {'head','snapshot'}<=records.keys():
        raise Rejected('quorum has no recoverable broker authority')
    current = json.loads(records['head'].data)
    head = decode_head({field:current[field] for field in HEAD_FIELDS},manifest)
    header = {'version':1,'cluster_id':cluster,'head':head,'writer_pending':writer_pending}
    compressed = records['snapshot'].data
    blockers = validate_sqlite(compressed,head)
    if writer_pending:
        blockers.append('writer')
    metadata = queue.encoded(header).encode()+b'\n'
    if len(metadata)>MAX_HEADER:
        raise Rejected('backup metadata is too large')
    raw = metadata+compressed
    return header|{'archive_sha256':digest(raw),'recovery_blockers':blockers}, raw


def backup(store, manifest, path):
    private_directory(path.parent)
    report, raw = capture(store,manifest)
    with staging_file(path.parent) as staged:
        staged.write_bytes(raw)
        install_new(staged,path)
    return report


def read_backup(path, manifest, expected_digest=None):
    private_directory(path.parent)
    with private_file(path).open('rb') as stream:
        meta = stream.readline(MAX_HEADER+1)
        if len(meta)>MAX_HEADER or not meta.endswith(b'\n'):
            raise Rejected('invalid backup header')
        compressed = stream.read(MAX_COMPRESSED+1)
    if len(compressed)>MAX_COMPRESSED:
        raise Rejected('backup checkpoint is too large')
    checksum = digest(meta+compressed)
    if expected_digest is not None and checksum!=expected_digest:
        raise Rejected('backup archive digest differs')
    header = json.loads(meta)
    if (set(header)!={'version','cluster_id','head','writer_pending'} or type(header['version']) is not int
            or header['version']!=1 or type(header['writer_pending']) is not bool):
        raise Rejected('unsupported backup header')
    checked_cluster_id(header['cluster_id'])
    head = decode_head(header['head'],manifest)
    blockers = validate_sqlite(compressed,head)
    if header['writer_pending']:
        blockers.append('writer')
    return header|{'archive_sha256':checksum,'recovery_blockers':blockers}, compressed


def restore(store, manifest, path, expected_head, expected_digest, holder):
    if not isinstance(expected_digest,str) or not re.fullmatch(r'[0-9a-f]{64}',expected_digest):
        raise Rejected('restore requires the independently recorded backup digest')
    captured, compressed = read_backup(path,manifest,expected_digest)
    if captured['head']!=decode_head(expected_head,manifest):
        raise Rejected('backup does not match the independent authority watermark')
    if captured['recovery_blockers']:
        raise Rejected('backup contains unresolved broker or provider work')
    if store.cluster_id is None or store.cluster_id==captured['cluster_id']:
        raise Rejected('restore requires a pinned new quorum')
    if holder not in ('primary','standby'):
        raise Rejected('invalid restored broker holder')
    head = captured['head']|{'holder':holder,'epoch':captured['head']['epoch']+1}
    current, cluster = store.read_with_cluster(*KEYS)
    if cluster!=store.cluster_id:
        raise Rejected('restore target belongs to a different quorum')
    changes = {'head':queue.encoded(head|{'handoff':None}).encode(),'snapshot':compressed}
    if current:
        if {key:record.data for key,record in current.items()}!=changes:
            raise Rejected('restore destination already holds different broker authority')
    elif not store.change(dict.fromkeys(KEYS),changes):
        raise ReplicaUnavailable('target quorum changed during restore')
    return {'cluster_id':cluster,'head':head,'archive_sha256':captured['archive_sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    commands = parser.add_subparsers(dest='operation',required=True)
    for name in ('backup','inspect'):
        commands.add_parser(name).add_argument('archive',type=Path)
    command = commands.add_parser('restore')
    command.add_argument('archive',type=Path)
    command.add_argument('--expected-head',required=True,type=Path)
    command.add_argument('--sha256',required=True)
    command.add_argument('--target-cluster-id',required=True)
    command.add_argument('--holder',required=True,choices=('primary','standby'))
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = json.loads(private_file(args.config).read_text())
        manifest = config['manifest']
        if args.operation=='backup':
            result = backup(EtcdStore(config['quorum'],manifest['repo']),manifest,args.archive)
        elif args.operation=='inspect':
            result, _ = read_backup(args.archive,manifest)
        else:
            expected = json.loads(private_file(args.expected_head).read_text())
            store = EtcdStore(config['quorum'],manifest['repo'],cluster_id=args.target_cluster_id)
            result = restore(store,manifest,args.archive,expected,args.sha256,args.holder)
        print(json.dumps(result))
    except (KeyError,TypeError,ValueError,OSError,sqlite3.Error,zlib.error) as error:
        detail = str(error) if isinstance(error,Rejected) else type(error).__name__
        print(json.dumps({'operation':args.operation,'error':detail}),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
