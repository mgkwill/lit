"""TLS-authenticated broker client. Send JSON requests on stdin, never argv."""
import argparse
import json
from pathlib import Path
import ssl
import sys
import time
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, Request, build_opener
from github_queue_coordinator import NoRedirect


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--cert',type=Path,required=True)
    parser.add_argument('--key',type=Path,required=True)
    parser.add_argument('--ca',type=Path,required=True)
    parser.add_argument('--wait-seconds',type=float,default=600)
    parser.add_argument('--no-wait',action='store_true')
    parser.add_argument('--claim')
    parser.add_argument('route',choices=('claims','health','status','assign','dependencies','register','recover-removed','propose','publication','registration','repository-token'))
    args=parser.parse_args()
    try:
        parsed=urlsplit(args.url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('expected HTTPS origin')
        if not math.isfinite(args.wait_seconds) or not 0<args.wait_seconds<=86400:
            raise ValueError('invalid wait deadline')
        context=ssl.create_default_context(cafile=str(args.ca))
        context.load_cert_chain(str(args.cert),str(args.key))
        payload=None
        if args.route in ('assign','dependencies','register','recover-removed','propose'):
            raw=sys.stdin.buffer.read(65537)
            if not 0<len(raw)<=65536:raise ValueError('invalid request length')
            payload=json.dumps(json.loads(raw)).encode()
        route=args.route
        if route in ('publication','registration'):
            if not args.claim or not re.fullmatch(r'[0-9a-f]{32}',args.claim):raise ValueError('claim required')
            route+='?claim='+args.claim
        request=Request(args.url+'/'+route,payload,{'Content-Type':'application/json'})
        opener=build_opener(NoRedirect(),HTTPSHandler(context=context))
        deadline=time.monotonic()+args.wait_seconds
        pending={}
        while True:
            try:
                with opener.open(request,timeout=max(.01,min(35,deadline-time.monotonic()))) as response:
                    result=json.load(response);code=response.status
                    retry_after=response.headers.get('Retry-After','1')
            except HTTPError as error:
                with error:
                    result=json.load(error);code=error.code
                    retry_after=error.headers.get('Retry-After','1')
            if args.no_wait or not (code==202 or (code in (429,503) and result.get('retryable') is True)):
                print(json.dumps(result));return 0 if code in (200,202) else 1
            if code==202:
                pending=result
                path=result['status_path']
                if not re.fullmatch(r'/(?:publication|registration)\?claim=[0-9a-f]{32}',path):raise ValueError('invalid status path')
                request=Request(args.url+path)
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
