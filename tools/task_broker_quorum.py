"""Durable quorum for broker checkpoints and revocable GitHub writer turns."""
import base64
from dataclasses import dataclass, field
import json
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, Request, build_opener
import zlib

import github_queue_coordinator as queue
from task_broker_replica import Authority, MAX_SNAPSHOT, Rejected, ReplicaUnavailable, digest


MAX_COMPRESSED = 8 * 1024 * 1024
MAX_REPLY = 16 * 1024 * 1024


def encode(value):
    return base64.b64encode(value).decode('ascii')


def checked_cluster_id(value):
    if (not isinstance(value,str) or not re.fullmatch(r'[1-9][0-9]{0,19}',value)
            or int(value)>=2**64):
        raise ValueError('expected an etcd cluster ID')
    return value


@dataclass(frozen=True)
class Record:
    revision: int
    data: bytes = field(repr=False)


class EtcdStore:
    """Use linearizable reads and compare-and-swap over the etcd v3 gateway."""
    def __init__(self, config, repo, cluster_id=None):
        self.urls = tuple(config['urls'])
        self.cluster_id = checked_cluster_id(cluster_id) if cluster_id is not None else None
        if len(set(self.urls)) != len(self.urls) or len(self.urls) < 3:
            raise ValueError('quorum requires separate configured etcd members')
        for url in self.urls:
            origin = urlsplit(url)
            if (origin.scheme != 'https' or not origin.hostname or origin.username is not None
                    or origin.path or origin.query or origin.fragment):
                raise ValueError('quorum requires pinned HTTPS origins')
        context = ssl.create_default_context(cafile=config['ca'])
        context.load_cert_chain(config['cert'],config['key'])
        self.opener = build_opener(queue.NoRedirect(), HTTPSHandler(context=context))
        self.prefix = '/lit/broker/'+repo+'/'

    def key(self, name):
        return encode((self.prefix+name).encode())

    def post(self, url, data):
        request = Request(url+'/v3/kv/txn',data,{'Content-Type':'application/json'})
        with self.opener.open(request,timeout=2) as result:
            raw = result.read(MAX_REPLY+1)
            if len(raw)>MAX_REPLY:
                raise ValueError('quorum response is too large')
            response = json.loads(raw)
        if self.cluster_id is not None and response['header']['cluster_id']!=self.cluster_id:
            raise ValueError('quorum endpoint belongs to another cluster')
        return response

    def transaction(self, compares, operations):
        data = queue.encoded({'compare':compares,'success':operations}).encode()
        if len(data)>MAX_REPLY:
            raise Rejected('quorum request is too large')
        for url in self.urls:
            try:
                if self.cluster_id is not None and compares:
                    # Verify this origin before writing, including on retries.
                    self.post(url,b'{"compare":[],"success":[]}')
                return self.post(url,data)
            except HTTPError as error:
                with error:
                    if error.code not in (500,502,503,504):
                        raise ReplicaUnavailable('quorum rejected the request') from None
            except URLError as error:
                if isinstance(error.reason,ssl.SSLError):
                    raise ReplicaUnavailable('quorum TLS verification failed') from None
            except (TimeoutError,ConnectionError):
                pass
            except (ValueError,OSError):
                raise ReplicaUnavailable('invalid quorum response') from None
        raise ReplicaUnavailable('quorum is unavailable')

    def read_with_cluster(self, *names):
        response = self.transaction([], [{'request_range':{'key':self.key(name)}} for name in names])
        ranges = response.get('responses',[])
        if len(ranges)!=len(names):
            raise ReplicaUnavailable('missing quorum range results')
        records = {}
        for name, result in zip(names,ranges):
            for row in result['response_range'].get('kvs',[]):
                if name in records or row['key']!=self.key(name):
                    raise ReplicaUnavailable('unexpected quorum range result')
                records[name] = Record(int(row['mod_revision']),base64.b64decode(row['value'],validate=True))
        return records, checked_cluster_id(response['header']['cluster_id'])

    def read(self, *names):
        records, _ = self.read_with_cluster(*names)
        return records

    def change(self, records, changes):
        compare = [{'key':self.key(name),'target':'MOD','result':'EQUAL',
                    'mod_revision':str(record.revision if record is not None else 0)}
                   for name,record in records.items()]
        operations = []
        for name,value in changes.items():
            key = self.key(name)
            operation = ({'request_delete_range':{'key':key}} if value is None else
                         {'request_put':{'key':key,'value':encode(value)}})
            operations.append(operation)
        return self.transaction(compare,operations).get('succeeded') is True


def revoke_installation_token(token):
    """Fence a writer at GitHub, including one that can still reach GitHub."""
    opener = build_opener(queue.NoRedirect())
    headers = {'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
               'User-Agent':'lit-broker-quorum'}

    def status(path, method):
        try:
            with opener.open(Request('https://api.github.com/'+path,headers=headers,method=method),
                             timeout=10) as response:
                return response.status
        except HTTPError as error:
            with error:
                return error.code
        except OSError:
            raise ReplicaUnavailable('GitHub writer revocation unavailable') from None

    if (status('installation/token','DELETE') not in (204,401)
            or status('installation/repositories','GET')!=401):
        raise ReplicaUnavailable('GitHub did not confirm revocation of the previous writer')


def snapshot_bytes(compressed):
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed,MAX_SNAPSHOT+1)
    if len(raw)>MAX_SNAPSHOT or not decoder.eof or decoder.unused_data:
        raise Rejected('invalid compressed checkpoint')
    return raw


class QuorumAuthority(Authority):
    def __init__(self, root, manifest, store, new_writer, reader, broker_reader,
                 github, revoke=revoke_installation_token, writer_timeout=75):
        self.root = root
        self.manifest = manifest
        self.store = store
        self.new_writer = new_writer
        self.reader = reader
        self.broker_reader = broker_reader
        self.github = github
        self.revoke = revoke
        self.writer_timeout = writer_timeout

    def commit(self, compare, changes):
        if not self.store.change(compare,changes):
            raise ReplicaUnavailable('quorum state changed during the request')

    def checkpoint(self, data):
        self.validate(data)
        result = zlib.compress(data,level=1)
        if len(result)>MAX_COMPRESSED:
            raise Rejected('checkpoint exceeds quorum storage limit')
        return result

    def bootstrap(self, actor, data, records):
        if actor!='manager' or records:
            raise Rejected('quorum initialization requires an unused authority')
        raw_header, separator, snapshot = data.partition(b'\n')
        if not separator:
            raise Rejected('checkpoint header missing')
        header = json.loads(raw_header)
        holder = header['holder']
        epoch, revision = header.get('epoch',1), header.get('revision',0)
        if (holder not in ('primary','standby') or type(epoch) is not int or epoch<1
                or type(revision) is not int or revision<0):
            raise Rejected('invalid initial holder or checkpoint version')
        row = {'holder':holder,'epoch':epoch,'revision':revision,'sha256':digest(snapshot),
               'binding':self.manifest,'handoff':None}
        self.commit(dict.fromkeys(('head','snapshot','writer')),
                    {'head':queue.encoded(row).encode(),'snapshot':self.checkpoint(snapshot)})
        return self.head(row)

    def promote(self, row, data, linear):
        change = json.loads(data)
        handoff = queue.encoded(change)
        if row['handoff']==handoff:
            return self.head(row)
        if (set(change)!={'from','to','epoch','request'} or change['from']!=row['holder']
                or change['epoch']!=row['epoch'] or change['to'] not in ('primary','standby')
                or change['to']==row['holder'] or not re.fullmatch(r'[0-9a-f]{32}',change['request'])):
            raise Rejected('authority promotion precondition differs')
        ticket = linear['writer']
        if ticket is not None:
            self.revoke(json.loads(ticket.data)['token'])
        row.update(holder=change['to'],epoch=row['epoch']+1,handoff=handoff)
        changes = {'head':queue.encoded(row).encode()}
        if ticket is not None:
            changes['writer'] = None
        self.commit(linear,changes)
        return self.head(row)

    def require_free_writer(self, linear):
        ticket = linear['writer']
        if ticket is None:
            return
        info = json.loads(ticket.data)
        if time.time()-info['started']>=self.writer_timeout:
            self.revoke(info['token'])
            self.commit({'writer':ticket},{'writer':None})
        raise ReplicaUnavailable('previous provider turn requires reconciliation')

    def write_provider(self, data, linear):
        self.require_free_writer(linear)
        token = self.new_writer()
        self.commit(linear,{'writer':queue.encoded({'token':token,'started':time.time()}).encode()})
        # Read back the exact turn before sending. If that read is lost, the
        # durable reservation survives until fencing, and no write starts.
        records = self.store.read('head','writer')
        ticket = records.get('writer')
        if (records.get('head')!=linear['head'] or ticket is None
                or json.loads(ticket.data)['token']!=token):
            raise ReplicaUnavailable('provider turn was fenced before submission')
        try:
            return self.provider(json.loads(data),github=self.github(token))
        finally:
            # This token is never reused for another provider turn. A lost
            # acknowledgement remains an unknown provider outcome to the broker.
            self.commit({'writer':ticket},{'writer':None})

    def request(self, actor, route, data, epoch, revision):
        names = ['head','writer']
        if route in ('/bootstrap','/snapshot'):
            names.append('snapshot')
        records = self.store.read(*names)
        if route=='/bootstrap':
            return self.bootstrap(actor,data,records)
        head = records.get('head')
        if head is None:
            raise Rejected('quorum authority is not initialized')
        row = json.loads(head.data)
        if row['binding']!=self.manifest:
            raise Rejected('quorum repository binding differs')
        linear = {'head':head,'writer':records.get('writer')}
        if route=='/head':
            return self.head(row)
        if route=='/promote' and actor=='manager':
            return self.promote(row,data,linear)
        if actor!=row['holder'] or epoch!=row['epoch']:
            raise Rejected('actor is fenced')
        if route=='/checkpoint':
            checksum = digest(data)
            if revision==row['revision']-1 and checksum==row['sha256']:
                return self.head(row)
            if revision!=row['revision']:
                raise Rejected('checkpoint revision differs')
            if checksum!=row['sha256']:
                self.require_free_writer(linear)
                row.update(revision=row['revision']+1,sha256=checksum)
                self.commit(linear,{'head':queue.encoded(row).encode(),'snapshot':self.checkpoint(data)})
            return self.head(row)
        if revision!=row['revision']:
            raise Rejected('provider or snapshot revision differs')
        if route=='/snapshot':
            data = snapshot_bytes(records['snapshot'].data)
            if digest(data)!=row['sha256']:
                raise Rejected('quorum checkpoint digest differs')
            return data
        if route in ('/reader','/broker-reader'):
            token, expires = (self.reader if route=='/reader' else self.broker_reader)()
            return {'token':token,'expires':expires}
        if route=='/provider':
            return self.write_provider(data,linear)
        raise Rejected('unknown replica operation')
