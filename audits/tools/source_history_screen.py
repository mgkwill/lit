#!/usr/bin/env python3
"""Audit-only historical source screening. Matches require manual review.

Source-bearing outputs stay outside Git and are never implementation inputs.
The lexer is heuristic, not a parser or a semantic derivation detector.
"""
import subprocess,re,json,hashlib,collections,pathlib,sys,time,argparse,bisect
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo',type=pathlib.Path,default=pathlib.Path(__file__).resolve().parents[2])
parser.add_argument('--private-output',type=pathlib.Path,required=True)
parser.add_argument('--baseline',default='98c59bb2dbebcf7f575993a4ad89e1300a578c83')
parser.add_argument('--upstream',required=True)
parser.add_argument('--head',required=True)
args=parser.parse_args()
REPO=str(args.repo.resolve())
OUT=args.private_output.resolve()
if OUT.is_relative_to(pathlib.Path(REPO)):
 parser.error('Source-bearing comparison output must stay outside the repository')
OUT.mkdir(parents=True,exist_ok=True)
BASE=args.baseline
UP=args.upstream
HEAD=args.head
for key,value in [('BASE',BASE),('UP',UP),('HEAD',HEAD)]:
 if not re.fullmatch('[0-9a-f]{40}',value):parser.error(key+' must be a full immutable object ID')
def git(*args):return subprocess.check_output(['git','-C',REPO,*args])
def objects(ref,exclude=None):
 args=['rev-list','--objects',ref]
 if exclude:args+=['--not',exclude]
 rows=git(*args).decode().splitlines()
 ids=[r.split(' ',1) for r in rows]
 meta=subprocess.run(['git','-C',REPO,'cat-file','--batch-check=%(objectname) %(objecttype) %(objectsize)'],input=('\n'.join(r[0] for r in ids)+'\n').encode(),stdout=subprocess.PIPE,check=True).stdout.decode().splitlines()
 return [(r[0],r[1],int(m.split()[2])) for r,m in zip(ids,meta) if len(r)>1 and m.split()[1]=='blob']
CODE={'.rs','.py','.sh','.ps1','.yml','.yaml','.toml','.js','.jsx','.ts','.tsx','.c','.h','.css','.html','.json'}
def relevant(p):return (pathlib.Path(p).suffix in CODE or p.endswith('.md')) and not any(x in p for x in ['package-lock.json','Cargo.lock','/gen/schemas/'])
sets={k:[(o,p,n) for o,p,n in objects(r,e) if relevant(p) and n<2000000] for k,r,e in [('baseline',BASE,None),('upstream',UP,BASE),('ours',HEAD,BASE)]}
allids=list(dict.fromkeys(o for rows in sets.values() for o,p,n in rows))
proc=subprocess.Popen(['git','-C',REPO,'cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
blobs={}
for oid in allids:
 proc.stdin.write((oid+'\n').encode());proc.stdin.flush()
 h=proc.stdout.readline().split();data=proc.stdout.read(int(h[2]));proc.stdout.read(1)
 blobs[oid]=data.decode(errors='replace')
proc.stdin.close();proc.wait()
# Strings remain literal; comments are dropped. Canonical mode anonymizes identifiers,
# retaining language keywords and primitive types. This detects many renamed adaptations,
# but neither proves derivation nor detects arbitrary semantic rewrites.
TOKEN=re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|\#[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\\n])\'|[A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?|[^\s]')
KEEP=set('as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while abstract become box do final macro override priv typeof unsized virtual yield try union bool str char u8 u16 u32 u64 u128 usize i8 i16 i32 i64 i128 isize f32 f64 def class import from with except finally raise lambda pass and or not None True False'.split())
def sequence(text,mode):
 if mode=='lines':
  seq=[];loc=[]
  for i,l in enumerate(text.splitlines(),1):
   s=re.sub(r'\s+','',l)
   if len(s)>=12 and not s.startswith(('//','/*','*','#')):seq.append(s);loc.append(i)
  return seq,loc
 seq=[];loc=[]
 newlines=[m.start() for m in re.finditer('\\n',text)]
 for m in TOKEN.finditer(text):
  s=m.group()
  if s.startswith(('//','/*','#')):continue
  if mode in ('renamed','shape') and re.fullmatch('[A-Za-z_][A-Za-z_0-9]*',s) and s not in KEEP:s='ID'
  if mode=='shape' and (s.startswith(('\"',"'")) or s[0].isdigit()):s='LITERAL'
  seq.append(s);loc.append(bisect.bisect_left(newlines,m.start())+1)
 return seq,loc
cache={}
def seq(oid,mode):
 key=(oid,mode)
 if key not in cache:cache[key]=sequence(blobs[oid],mode)
 return cache[key]
def grams(s,k):
 for i in range(len(s)-k+1):yield i,hashlib.blake2b('\x00'.join(s[i:i+k]).encode(),digest_size=12).digest()
results={}; stats={}
for mode,k in [('lines',4),('tokens',40),('renamed',70),('shape',100)]:
 print('Screening',mode,flush=True)
 baseline=set()
 for o,p,n in sets['baseline']:
  s,_=seq(o,mode);baseline.update(h for _,h in grams(s,k))
 index=collections.defaultdict(list)
 for o,p,n in sets['upstream']:
  if mode!='lines' and pathlib.Path(p).suffix=='.md':continue
  s,_=seq(o,mode)
  for i,h in grams(s,k):
   if h not in baseline:index[h].append((o,p,i))
 candidates=[]
 for o,p,n in sets['ours']:
  if mode!='lines' and pathlib.Path(p).suffix=='.md':continue
  s,loc=seq(o,mode);pairs=collections.defaultdict(list)
  for i,h in grams(s,k):
   if h in index:
    for u,up,j in index[h]:pairs[(u,up,j-i)].append(i)
  for (u,up,delta),positions in pairs.items():
   positions=sorted(set(positions));groups=[]
   for i in positions:
    if not groups or i>groups[-1][-1]+k:groups.append([i])
    else:groups[-1].append(i)
   us,uloc=seq(u,mode)
   for g in groups:
    a,b=g[0],g[-1]+k;c=a+delta;d=b+delta
    candidates.append(dict(ours_path=p,ours_blob=o,ours_lines=[loc[a],loc[b-1]],upstream_path=up,upstream_blob=u,upstream_lines=[uloc[c],uloc[d-1]],units=b-a,matching_windows=len(g),ours_excerpt='\n'.join(blobs[o].splitlines()[loc[a]-1:loc[b-1]]),upstream_excerpt='\n'.join(blobs[u].splitlines()[uloc[c]-1:uloc[d-1]])))
 candidates.sort(key=lambda x:(-x['units'],x['ours_path']))
 results[mode]=candidates
 stats[mode]={'window':k,'baseline_windows':len(baseline),'novel_upstream_windows':len(index),'candidate_pairs':len(candidates)}
 print(stats[mode],flush=True)
 cache={key:v for key,v in cache.items() if key[1]!=mode}
manifest={'schema_version':2,'repository':REPO,'baseline':BASE,'upstream':UP,'head':HEAD,'corpora':{k:[{'oid':o,'path':p,'bytes':n} for o,p,n in rows] for k,rows in sets.items()},'stats':stats}
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
(OUT/'candidates.json').write_text(json.dumps(results,indent=2)+'\n')
summary=[]
for mode,rows in results.items():
 summary.append(f'## {mode}: {len(rows)} candidate pairs\n')
 # Different upstream revisions often contain the same matching code; show distinct ours ranges once.
 seen=set()
 for r in rows:
  key=(r['ours_blob'],tuple(r['ours_lines']),r['upstream_excerpt'])
  if key in seen:continue
  seen.add(key)
  summary.append(f"### {r['ours_path']} {r['ours_blob'][:10]}:{r['ours_lines']} / {r['upstream_path']} {r['upstream_blob'][:10]}:{r['upstream_lines']} ({r['units']} units)\n\nOurs:\n```\n{r['ours_excerpt']}\n```\nUpstream:\n```\n{r['upstream_excerpt']}\n```\n")
(OUT/'candidate-review.md').write_text('\n'.join(summary))
print(json.dumps({'corpus_counts':{k:len(v) for k,v in sets.items()},'stats':stats}),flush=True)

# Supplement windows with individual prose/code-line and literal checks.
def entries(oid, mode):
 text=blobs[oid]
 if mode=='long_lines':
  for number,line in enumerate(text.splitlines(),1):
   value=re.sub(r'\s+',' ',line.strip())
   if len(value)>=60:yield number,value
 else:
  newlines=[m.start() for m in re.finditer('\n',text)]
  for match in re.finditer(r'"(?:\\.|[^"\\])*"',text):
   if len(match.group())>=22:
    yield bisect.bisect_left(newlines,match.start())+1,match.group()

additional={}
for mode in ['long_lines','literals']:
 baseline={value for oid,path,size in sets['baseline'] for _,value in entries(oid,mode)}
 index=collections.defaultdict(list)
 for oid,path,size in sets['upstream']:
  for number,value in entries(oid,mode):
   if value not in baseline:index[value].append({'path':path,'oid':oid,'line':number})
 matches={}
 for oid,path,size in sets['ours']:
  for number,value in entries(oid,mode):
   if value in index:
    entry=matches.setdefault(value,{'ours':[],'upstream':index[value]})
    entry['ours'].append({'path':path,'oid':oid,'line':number})
 additional[mode]=matches
 print(mode,len(matches),flush=True)
baseline_ids={oid for oid,path,size in sets['baseline']}
upstream_ids={oid for oid,path,size in sets['upstream']}
additional['whole_blobs']=[{'oid':oid,'path':path} for oid,path,size in sets['ours'] if oid in upstream_ids-baseline_ids]
(OUT/'additional-matches.json').write_text(json.dumps(additional,indent=2)+'\n')
print('whole_blobs',len(additional['whole_blobs']),flush=True)
