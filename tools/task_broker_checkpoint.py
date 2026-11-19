"""Export or restore a private SQLite checkpoint of stopped broker authority."""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile

from task_broker import private_file
import task_ownership as ownership


def database_readonly(path):
    return closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True))


def verify_checkpoint(path, manifest):
    with database_readonly(path) as db:
        if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:
            raise ValueError('checkpoint integrity check failed')
        tables={name for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required={'metadata','claims','tasks','proposals','actions','events','publications',
                  'publication_jobs','registration_jobs','integrations'}
        if not required<=tables:
            raise ValueError('checkpoint is missing broker state')
        binding=db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()
        if not binding or json.loads(binding[0])!=manifest:
            raise ValueError('checkpoint repository or ruleset binding differs')


def sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as data:
        for chunk in iter(lambda:data.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def staging_file(directory):
    fd, name=tempfile.mkstemp(prefix='.broker-checkpoint-',dir=directory)
    os.close(fd)
    path=Path(name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def install_new(staged, destination):
    with staged.open('rb') as data:
        os.fsync(data.fileno())
    os.link(staged,destination)
    directory=os.open(destination.parent,os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def export_checkpoint(root, path, manifest):
    source=private_file(root/'queue.sqlite')
    with staging_file(path.parent) as staged:
        with database_readonly(source) as db, closing(sqlite3.connect(staged)) as copy:
            db.backup(copy)
        verify_checkpoint(staged,manifest)
        digest=sha256(staged)
        install_new(staged,path)
    return digest


def restore_checkpoint(root, path, manifest, expected):
    destination=root/'queue.sqlite'
    if any(destination.with_name(destination.name+suffix).exists() for suffix in ('','-wal','-shm','-journal')):
        raise ValueError('restore requires an unused authority database path')
    with staging_file(root) as staged:
        shutil.copyfile(private_file(path),staged)
        if sha256(staged)!=expected:
            raise ValueError('checkpoint digest differs')
        verify_checkpoint(staged,manifest)
        install_new(staged,destination)
    return expected


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('export').add_argument('checkpoint',type=Path)
    restore=commands.add_parser('restore')
    restore.add_argument('checkpoint',type=Path)
    restore.add_argument('--sha256',required=True)
    args=parser.parse_args()
    try:
        config=json.loads(private_file(args.config).read_text())
        root=Path(config['state'])
        manifest=config['manifest']
        if args.command=='restore':
            if not re.fullmatch(r'[0-9a-f]{64}',args.sha256):
                raise ValueError('invalid checkpoint digest')
            root.mkdir(mode=0o700,parents=True,exist_ok=True)
        with ownership.broker_instance(root), ownership.state_lock(root,'writer.lock','coordinator owns this state directory'):
            if args.command=='export':
                digest=export_checkpoint(root,args.checkpoint,manifest)
            else:
                digest=restore_checkpoint(root,args.checkpoint,manifest,args.sha256)
        print(json.dumps({'operation':args.command,'sha256':digest,'repository':manifest['repo']}))
    except (ValueError,OSError,KeyError,sqlite3.Error,ownership.AuthorityBusy) as error:
        print(json.dumps({'operation':args.command,'error':type(error).__name__}),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
