"""TLS-authenticated broker client. Send JSON requests on stdin, never argv."""
import argparse
import errno
import json
from pathlib import Path
import ssl
import socket
import sys
import time
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, Request, build_opener
from github_queue_coordinator import NoRedirect


def origin(url):
    parsed=urlsplit(url)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username is not None
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('expected HTTPS origin')
    return url


def can_use_standby(error, pending_read):
    reason=error.reason if isinstance(error,URLError) else error
    if isinstance(reason,ssl.SSLError) and not isinstance(reason,ssl.SSLEOFError):
        return False
    if isinstance(reason,(ConnectionRefusedError,socket.gaierror)):
        return True
    return pending_read and (
        isinstance(reason,(ConnectionError,TimeoutError,ssl.SSLEOFError))
        or isinstance(reason,OSError) and reason.errno in (errno.ENETUNREACH,errno.EHOSTUNREACH))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--standby-url')
    parser.add_argument('--cert',type=Path,required=True)
    parser.add_argument('--key',type=Path,required=True)
    parser.add_argument('--ca',type=Path,required=True)
    parser.add_argument('--wait-seconds',type=float,default=600)
    parser.add_argument('--no-wait',action='store_true')
    parser.add_argument('--claim')
    parser.add_argument('--workspace',type=Path)
    parser.add_argument('--lit',type=Path)
    parser.add_argument('route',choices=('claims','health','status','assign','dependencies','path-hints','register','recover-removed','reconcile-publication','propose','publication','registration','repository-token'))
    args=parser.parse_args()
    try:
        endpoint=origin(args.url)
        standby=origin(args.standby_url) if args.standby_url is not None else None
        if endpoint==standby:
            raise ValueError('standby URL must be distinct')
        if not math.isfinite(args.wait_seconds) or not 0<args.wait_seconds<=86400:
            raise ValueError('invalid wait deadline')
        context=ssl.create_default_context(cafile=str(args.ca))
        context.load_cert_chain(str(args.cert),str(args.key))
        if (args.workspace is None)!=(args.lit is None) or (args.workspace is not None and args.route!='propose'):
            raise ValueError('workspace options require a Lit binary and the propose route')
        payload=None
        if args.route in ('assign','dependencies','path-hints','register','recover-removed','reconcile-publication','propose'):
            raw=sys.stdin.buffer.read(65537)
            if not 0<len(raw)<=65536:raise ValueError('invalid request length')
            body=json.loads(raw)
            if args.workspace is not None:
                from task_broker_workspace import proposal_from_workspace
                binding={'task','generation','claim','base','capability','request'}
                if not isinstance(body,dict) or set(body)!=binding:
                    raise ValueError('workspace proposal requires only the assignment binding and request ID')
                proof=proposal_from_workspace(args.lit.resolve(strict=True),args.workspace,body['base'])
                body['files']=proof['files']
            payload=json.dumps(body).encode()
            if len(payload)>65536:raise ValueError('publication exceeds broker request limit')
        route=args.route
        if route in ('publication','registration'):
            if not args.claim or not re.fullmatch(r'[0-9a-f]{32}',args.claim):raise ValueError('claim required')
            route+='?claim='+args.claim
        path='/'+route
        opener=build_opener(NoRedirect(),HTTPSHandler(context=context))
        deadline=time.monotonic()+args.wait_seconds
        pending={}
        while True:
            request=Request(endpoint+path,payload,{'Content-Type':'application/json'})
            try:
                with opener.open(request,timeout=max(.01,min(35,deadline-time.monotonic()))) as response:
                    result=json.load(response);code=response.status
                    retry_after=response.headers.get('Retry-After','1')
            except HTTPError as error:
                with error:
                    result=json.load(error);code=error.code
                    retry_after=error.headers.get('Retry-After','1')
            except OSError as error:
                if not standby or time.monotonic()>=deadline or not can_use_standby(error,payload is None):
                    raise
                endpoint,standby=standby,None
                continue
            if (standby and payload is None and time.monotonic()<deadline and code==503
                    and result.get('error')=='authority_unavailable' and result.get('retryable') is True):
                endpoint,standby=standby,None
                continue
            if args.no_wait or not (code==202 or (code in (429,503) and result.get('retryable') is True)):
                print(json.dumps(result));return 0 if code in (200,202) else 1
            if code==202:
                pending=result
                path=result['status_path']
                if not re.fullmatch(r'/(?:publication|registration)\?claim=[0-9a-f]{32}',path):raise ValueError('invalid status path')
                payload=None
            delay=float(retry_after)
            if not math.isfinite(delay):raise ValueError('invalid retry delay')
            delay=max(.1,min(30,delay))
            if deadline-time.monotonic()<=delay:
                print(json.dumps({'error':'broker_wait_timeout','retryable':False,
                    'operation':pending.get('operation'),'status_path':pending.get('status_path')}))
                return 1
            time.sleep(delay)
    except (OSError,ValueError,URLError,ssl.SSLError):
        print(json.dumps({'error':'broker_request_failed','retryable':False}),file=sys.stderr)
        return 1


if __name__=='__main__':sys.exit(main())
