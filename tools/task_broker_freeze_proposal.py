"""Trusted scheduler freezes one broker-proven proposal; it never edits main rules."""
import argparse
import json
from pathlib import Path
import re
import ssl
import subprocess
import sys
from urllib.request import HTTPSHandler, Request, build_opener
from urllib.parse import urlsplit
from github_queue_coordinator import NoRedirect


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('url','cert','key','ca','manifest'):parser.add_argument('--'+key,required=True)
    parser.add_argument('--gh',default='gh')
    args=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536:raise ValueError('receipt too large')
        receipt=json.loads(raw);config=json.loads(Path(args.manifest).read_text())
        origin=urlsplit(args.url)
        if origin.scheme!='https' or origin.username or origin.path or origin.query or origin.fragment:
            raise ValueError('expected HTTPS origin')
        if (receipt['author']!=config['broker_author']
                or not re.fullmatch(r'agents/broker/attempt/[0-9a-f]{32}',receipt['branch'])
                or not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+',config['repo'])):
            raise ValueError('invalid broker receipt')
        context=ssl.create_default_context(cafile=args.ca);context.load_cert_chain(args.cert,args.key)
        opener=build_opener(NoRedirect(),HTTPSHandler(context=context))
        with opener.open(Request(args.url+'/status'),timeout=20) as response:status=json.load(response)
        if not any(p['phase']=='published' and p['receipt']==receipt for p in status['publications']):
            raise ValueError('receipt not proven by the authenticated broker')
        def gh(path,payload=None):
            command=[args.gh,'api',path]
            if payload is not None:command+=['-X','PUT','--input','-']
            result=subprocess.run(command,input=None if payload is None else json.dumps(payload).encode(),capture_output=True,timeout=30)
            if result.returncode:raise RuntimeError('GitHub freeze policy request failed')
            return json.loads(result.stdout)
        repo=config['repo'];pr=gh(f"repos/{repo}/pulls/{receipt['number']}")
        if (pr['head']['sha']!=receipt['head'] or pr['head']['ref']!=receipt['branch']
                or pr['user']['login']!=config['broker_author'] or pr['base']['ref']!='main'
                or pr['base']['repo']['full_name']!=repo or pr['state']!='open' or pr['merged']):
            raise ValueError('proposal identity differs')
        rule=gh(f"repos/{repo}/rulesets/{config['freeze_ruleset']}")
        refs=rule['conditions']['ref_name']
        if (rule['target']!='branch' or rule['enforcement']!='active' or rule.get('bypass_actors')!=[]
                or refs['exclude'] or any('*' in ref or ref.startswith('~') for ref in refs['include'])
                or not {'update','deletion'}<={r['type'] for r in rule['rules']}):
            raise ValueError('unsafe freeze policy')
        ref='refs/heads/'+receipt['branch']
        if ref not in refs['include']:
            body={k:rule[k] for k in ('name','target','enforcement','conditions','rules','bypass_actors')}
            body['conditions']['ref_name']['include'].append(ref)
            gh(f"repos/{repo}/rulesets/{config['freeze_ruleset']}",body)
        print(json.dumps({'frozen':receipt['number'],'head':receipt['head'],'branch':receipt['branch']}))
        return 0
    except Exception as error:
        print(json.dumps({'error':'freeze_rejected','type':type(error).__name__}),file=sys.stderr)
        return 1


if __name__=='__main__':sys.exit(main())
