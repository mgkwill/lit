"""Watch a primary broker and transfer authority to a prepared systemd standby."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shlex
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, build_opener

from github_queue_coordinator import NoRedirect
from task_broker import private_file
from task_broker_checkpoint import export_checkpoint, install_new, restore_checkpoint, sha256, staging_file
import task_ownership as ownership


def execute(args, payload=None, seconds=90):
    process=subprocess.run(args,input=payload,capture_output=True,timeout=seconds)
    if process.returncode:
        raise RuntimeError('management command failed')
    return process.stdout


def sync_json(path, value, replace=False):
    with staging_file(path.parent) as temporary:
        temporary.write_text(json.dumps(value,sort_keys=True)+'\n')
        if replace:
            with temporary.open('rb') as data:
                os.fsync(data.fileno())
            os.replace(temporary,path)
            directory=os.open(path.parent,os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        else:
            install_new(temporary,path)


class SystemdHost:
    """The trusted host adapter executes only the pinned service operations."""
    def __init__(self, unit, config):
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service',unit) or unit.startswith('-'):
            raise ValueError('invalid service unit')
        self.unit=unit
        self.root=Path(config['state'])
        self.binding=config['manifest']
        self.replica=config.get('replica')
        self.dropdir=Path('/etc/systemd/system')/(unit+'.d')
        self.marker=self.dropdir/'lit-failover.marker'
        self.condition=self.dropdir/'90-lit-failover.conf'
        self.restored=self.dropdir/'lit-failover-restored.json'

    def fence_rule(self):
        return f'[Unit]\nConditionPathExists=!{self.marker}\n'

    def service(self, *args):
        return execute(['systemctl',*args])

    def properties(self, allow_pending_reload=False):
        keys=('LoadState','ActiveState','MainPID','ControlPID','KillMode','ConditionResult','NeedDaemonReload','User')
        lines=self.service('show',self.unit,*(f'--property={k}' for k in keys)).decode().splitlines()
        values=dict(line.split('=',1) for line in lines)
        if (values['LoadState']!='loaded' or (not allow_pending_reload and values['NeedDaemonReload']!='no')
                or values['KillMode']!='control-group'):
            raise RuntimeError('service is not ready for fencing')
        return values

    def stopped(self):
        values=self.properties()
        if values['ActiveState'] not in ('inactive','failed') or values['MainPID']!='0' or values['ControlPID']!='0':
            raise RuntimeError('service has not stopped')
        return values

    def identity(self):
        self.properties(allow_pending_reload=True)
        machine=Path('/etc/machine-id').read_bytes().strip()
        if not re.fullmatch(rb'[0-9a-f]{32}',machine):
            raise RuntimeError('machine identity unavailable')
        result={'machine':hashlib.sha256(machine).hexdigest(),'binding':self.binding,
                'unit':self.unit,'state':str(self.root.resolve())}
        if self.replica:
            result.update(replica_actor=self.replica['actor'],replica_url=self.replica['url'])
        return result

    def ready(self):
        self.stopped()
        if self.marker.exists() or self.restored.exists() or (self.root/'queue.sqlite').exists():
            raise RuntimeError('standby is already bound or fenced')
        return self.identity()

    def fence(self, handoff):
        rule=self.fence_rule()
        if self.condition.exists():
            if self.condition.read_text()!=rule:
                raise RuntimeError('configured fence conflicts with existing drop-in')
            if not self.marker.exists():
                self.service('daemon-reload')
        if not self.marker.exists():
            self.stopped()
        self.dropdir.mkdir(mode=0o755,parents=True,exist_ok=True)
        if not self.condition.exists():
            with staging_file(self.dropdir) as temporary:
                temporary.write_text(rule)
                temporary.chmod(0o644)
                install_new(temporary,self.condition)
        if not self.marker.exists():
            sync_json(self.marker,{'handoff':handoff})
        self.require_marker(handoff)
        self.service('daemon-reload')
        self.service('stop',self.unit)
        if self.stopped()['ActiveState']=='failed':
            self.service('reset-failed',self.unit)
        # Exercise the installed start condition before any peer can start.
        self.service('start',self.unit)
        self.verify_fenced(handoff)

    def require_marker(self, handoff):
        if json.loads(private_file(self.marker).read_text())!={'handoff':handoff}:
            raise RuntimeError('service is fenced for another handoff')

    def verify_fenced(self, handoff):
        self.require_marker(handoff)
        if self.condition.read_text()!=self.fence_rule() or self.stopped()['ConditionResult']!='no':
            raise RuntimeError('systemd did not prove the persistent start fence')

    @contextmanager
    def state_lock(self):
        with ownership.broker_instance(self.root), ownership.state_lock(self.root,'writer.lock','coordinator is active'):
            yield

    def checkpoint(self, handoff):
        self.verify_fenced(handoff)
        with self.state_lock(), tempfile.TemporaryDirectory(dir=self.root) as temporary:
            path=Path(temporary)/'authority.sqlite'
            export_checkpoint(self.root,path,self.binding)
            self.verify_fenced(handoff)
            return path.read_bytes()

    def restore(self, handoff, digest, data):
        user=self.stopped()['User']
        if not user or self.marker.exists() or hashlib.sha256(data).hexdigest()!=digest:
            raise RuntimeError('standby state or checkpoint digest differs')
        account=pwd.getpwnam(user)
        if account.pw_uid==0:
            raise RuntimeError('standby requires a dedicated non-root account')
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.dropdir.mkdir(mode=0o755,parents=True,exist_ok=True)
        desired={'handoff':handoff,'sha256':digest}
        destination=self.root/'queue.sqlite'
        with self.state_lock(), staging_file(self.root) as incoming:
            if self.restored.exists():
                if json.loads(private_file(self.restored).read_text())!=desired:
                    raise RuntimeError('standby belongs to another handoff')
            else:
                if destination.exists():
                    raise RuntimeError('standby database path is occupied')
                sync_json(self.restored,desired)
            if destination.exists():
                if sha256(destination)!=digest:
                    raise RuntimeError('existing standby checkpoint differs')
            else:
                incoming.write_bytes(data)
                restore_checkpoint(self.root,incoming,self.binding,digest)
            for path in (self.root,destination,self.root/'broker-service.lock',self.root/'writer.lock'):
                os.chown(path,account.pw_uid,account.pw_gid)

    def activate(self, handoff, digest):
        expected={'handoff':handoff,'sha256':digest}
        if self.marker.exists() or json.loads(private_file(self.restored).read_text())!=expected:
            raise RuntimeError('standby receipt does not authorize activation')
        if self.properties()['ActiveState']!='active':
            self.stopped()
            if sha256(self.root/'queue.sqlite')!=digest:
                raise RuntimeError('standby database changed before activation')
            self.service('start',self.unit)
        if self.properties()['ActiveState']!='active':
            raise RuntimeError('standby service did not activate')

    def start_replica(self):
        if not self.replica or self.replica.get('actor')!='standby' or self.marker.exists():
            raise RuntimeError('replicated standby is not provisioned')
        if self.properties()['ActiveState']!='active':
            self.stopped()
            self.service('start',self.unit)
        if self.properties()['ActiveState']!='active':
            raise RuntimeError('replicated standby did not activate')


class Remote:
    def __init__(self, settings):
        self.settings=settings
        alias=settings['host']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]+',alias):
            raise ValueError('invalid management host')
        self.ssh=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8',alias]

    def __call__(self, action, *args, data=None):
        s=self.settings
        remote=['sudo','-n','python3','-B',s['adapter'],'host','--config',s['config'],
                '--unit',s['unit'],action,*args]
        process=subprocess.run(self.ssh+[shlex.join(remote)],input=data,capture_output=True,timeout=90)
        if process.returncode:
            diagnostic='management connection or host operation failed'
            try:
                detail=json.loads(process.stderr)
                diagnostic=detail.get('reason',detail.get('error',diagnostic))
            except (ValueError,AttributeError):
                pass
            raise RuntimeError(f'{action}: {diagnostic}')
        output=process.stdout
        if action=='checkpoint':
            return output
        return json.loads(output)


def transfer(primary, standby, journal):
    checkpoint=journal.with_name(journal.name+'.sqlite')
    if journal.exists():
        intent=json.loads(private_file(journal).read_text())
    else:
        if checkpoint.exists():
            raise RuntimeError('checkpoint has no matching handoff journal')
        source=primary('identity')
        target=standby('ready')
        if source['machine']==target['machine'] or source['binding']!=target['binding']:
            raise RuntimeError('handoff requires distinct hosts and the same repository policy')
        primary('offline')
        intent={'handoff':secrets.token_hex(16),'source':source,'target':target,'stage':'fence'}
        sync_json(journal,intent)
    if primary('identity')!=intent['source'] or standby('identity')!=intent['target']:
        raise RuntimeError('handoff management identities changed')
    handoff=intent['handoff']
    if intent['stage']=='fence':
        primary('fence',handoff)
        if not checkpoint.exists():
            with staging_file(checkpoint.parent) as temporary:
                temporary.write_bytes(primary('checkpoint',handoff))
                install_new(temporary,checkpoint)
        digest=sha256(private_file(checkpoint))
        intent.update(stage='restore',sha256=digest)
        sync_json(journal,intent,replace=True)
    if intent['stage']=='restore':
        primary('verify-fence',handoff)
        data=private_file(checkpoint).read_bytes()
        if hashlib.sha256(data).hexdigest()!=intent['sha256']:
            raise RuntimeError('stored authority checkpoint differs')
        standby('restore',handoff,intent['sha256'],data=data)
        intent['stage']='activate'
        sync_json(journal,intent,replace=True)
    if intent['stage']=='activate':
        primary('verify-fence',handoff)
        standby('activate',handoff,intent['sha256'])
        intent['stage']='promoted'
        sync_json(journal,intent,replace=True)
    if intent['stage']!='promoted':
        raise RuntimeError('unknown handoff journal phase')
    checkpoint.unlink(missing_ok=True)
    return intent


def transfer_replica(authority, standby, journal):
    if journal.exists():
        intent=json.loads(private_file(journal).read_text())
        if intent.get('mode')!='replica':
            raise RuntimeError('different handoff journal mode')
    else:
        source=authority.call('/head')
        if source['holder']!='primary':
            raise RuntimeError('replica does not belong to the primary')
        target=standby('ready')
        if (target.get('replica_actor')!='standby' or target.get('replica_url')!=authority.url
                or target['binding']!=source['binding']):
            raise RuntimeError('standby is bound to a different replica or repository')
        request={'from':'primary','to':'standby','epoch':source['epoch'],'request':secrets.token_hex(16)}
        intent={'mode':'replica','request':request,'target':target}
        sync_json(journal,intent)
    if standby('identity')!=intent['target']:
        raise RuntimeError('standby management identity changed')
    # The independent holder switch is the fence. It serializes against
    # in-flight provider requests and revokes the old actor at the witness.
    receipt=authority.call('/promote',intent['request'])
    if receipt['holder']!='standby' or receipt['epoch']!=intent['request']['epoch']+1:
        raise RuntimeError('independent fencing was not confirmed')
    standby('start-replica')
    return {'handoff':intent['request']['request'],'sha256':receipt['sha256']}


def reachable(url, context):
    endpoint=urlsplit(url)
    if endpoint.scheme!='https' or not endpoint.hostname or endpoint.username or endpoint.path or endpoint.query or endpoint.fragment:
        raise ValueError('expected a pinned HTTPS broker origin')
    opener=build_opener(NoRedirect(),HTTPSHandler(context=context))
    try:
        with opener.open(url+'/health',timeout=8) as response:
            result=json.load(response)
    except HTTPError:
        raise RuntimeError('broker probe authorization or response failed') from None
    except URLError as error:
        # A stopped listener behind an SSH tunnel can close the TCP connection
        # before there is any peer certificate to verify.
        if isinstance(error.reason,ssl.SSLError) and not isinstance(error.reason,ssl.SSLEOFError):
            raise RuntimeError('broker probe TLS verification failed') from None
        return False
    except (TimeoutError,ConnectionError):
        return False
    if result.get('service')!='lit-task-broker' or result.get('api_version')!=3:
        raise RuntimeError('broker probe identity differs')
    return result


def watch(config, journal):
    context=ssl.create_default_context(cafile=config['ca'])
    context.load_cert_chain(config['cert'],config['key'])
    primary=Remote(config['primary']) if 'replica' not in config else None
    standby=Remote(config['standby'])
    url=config['primary']['url']
    wait=config.get('poll_seconds',5)
    threshold=config.get('failure_threshold',3)
    if not isinstance(wait,(int,float)) or not 0<wait<=60 or type(threshold) is not int or not 1<=threshold<=60:
        raise ValueError('invalid failure observation interval')
    if not journal.exists():
        if not reachable(url,context):
            raise RuntimeError('start the watcher with an authenticated primary baseline')
        print(json.dumps({'event':'watching_primary'}),flush=True)
        misses=0
        while misses<threshold:
            time.sleep(wait)
            misses=0 if reachable(url,context) else misses+1
    if 'replica' in config:
        from task_broker_replica import Client
        receipt=transfer_replica(Client(config['replica']),standby,journal)
    else:
        receipt=transfer(primary,standby,journal)
    deadline=time.monotonic()+75
    while time.monotonic()<deadline:
        health=reachable(config['standby']['url'],context)
        if (health and health.get('last_error') is None and health.get('last_cycle')
                and time.time()-45<health['last_cycle']<=time.time()+5):
            break
        time.sleep(1)
    else:
        raise RuntimeError('standby has not completed a fresh controller cycle')
    print(json.dumps({'event':'standby_promoted','handoff':receipt['handoff'],'sha256':receipt['sha256']}),flush=True)


def host(args):
    adapter=SystemdHost(args.unit,json.loads(private_file(args.config).read_text()))
    action=args.action
    if action in ('identity','ready','offline','start-replica'):
        if action=='start-replica':
            adapter.start_replica()
            print(json.dumps({'operation':action}))
            return
        if action=='offline':
            adapter.stopped()
            print(json.dumps({'source_service_stopped':True}))
            return
        print(json.dumps(getattr(adapter,action)()))
        return
    if not args.handoff or not re.fullmatch(r'[0-9a-f]{32}',args.handoff):
        raise ValueError('invalid handoff ID')
    if action in ('restore','activate') and (not args.sha256 or not re.fullmatch(r'[0-9a-f]{64}',args.sha256)):
        raise ValueError('invalid checkpoint digest')
    if action=='checkpoint':
        sys.stdout.buffer.write(adapter.checkpoint(args.handoff))
        return
    if action=='restore':
        adapter.restore(args.handoff,args.sha256,sys.stdin.buffer.read())
    elif action=='activate':
        adapter.activate(args.handoff,args.sha256)
    elif action=='fence':
        adapter.fence(args.handoff)
    else:
        adapter.verify_fenced(args.handoff)
    print(json.dumps({'operation':action,'handoff':args.handoff}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    use=parser.add_subparsers(dest='mode',required=True)
    watcher=use.add_parser('watch')
    watcher.add_argument('--config',type=Path,required=True)
    seed=use.add_parser('seed-replica')
    seed.add_argument('--config',type=Path,required=True)
    seed.add_argument('--checkpoint',type=Path,required=True)
    manager=use.add_parser('host')
    manager.add_argument('--config',type=Path,required=True)
    manager.add_argument('--unit',required=True)
    manager.add_argument('action',choices=('identity','ready','offline','fence','verify-fence','checkpoint','restore','activate','start-replica'))
    manager.add_argument('handoff',nargs='?')
    manager.add_argument('sha256',nargs='?')
    args=parser.parse_args()
    try:
        if args.mode=='host':
            host(args)
        elif args.mode=='seed-replica':
            from task_broker_replica import Client
            config=json.loads(private_file(args.config).read_text())
            result=Client(config).call('/bootstrap',b'{"holder":"primary"}\n'+private_file(args.checkpoint).read_bytes())
            print(json.dumps(result))
        else:
            config=json.loads(private_file(args.config).read_text())
            journal=Path(config['journal'])
            with ownership.state_lock(journal.parent,journal.name+'.lock','failover watcher already owns this journal'):
                watch(config,journal)
    except Exception as error:
        status={'event':'failover_blocked','error':type(error).__name__}
        if isinstance(error,RuntimeError):
            status['reason']=str(error)
        print(json.dumps(status),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    sys.exit(main())
