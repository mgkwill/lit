"""TLS-authenticated broker client. Send JSON requests on stdin, never argv."""
import argparse
import json
from pathlib import Path
import ssl
import sys
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
    parser.add_argument('route',choices=('claims','health','status','assign','register','recover-removed','propose','repository-token'))
    args=parser.parse_args()
    try:
        parsed=urlsplit(args.url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('expected HTTPS origin')
        context=ssl.create_default_context(cafile=str(args.ca))
        context.load_cert_chain(str(args.cert),str(args.key))
        payload=None
        if args.route in ('assign','register','recover-removed','propose'):
            raw=sys.stdin.buffer.read(65537)
            if not 0<len(raw)<=65536:raise ValueError('invalid request length')
            payload=json.dumps(json.loads(raw)).encode()
        request=Request(args.url+'/'+args.route,payload,{'Content-Type':'application/json'})
        opener=build_opener(NoRedirect(),HTTPSHandler(context=context))
        try:
            with opener.open(request,timeout=35) as response:result=json.load(response)
            code=200
        except HTTPError as error:
            with error:result=json.load(error);code=error.code
        print(json.dumps(result))
        return 0 if code==200 else 1
    except (OSError,ValueError,URLError,ssl.SSLError):
        print(json.dumps({'error':'broker_request_failed'}),file=sys.stderr)
        return 1


if __name__=='__main__':sys.exit(main())
