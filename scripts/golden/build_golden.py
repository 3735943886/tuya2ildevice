import json, pathlib, re, collections
from ambr import parse
ROOT=pathlib.Path(__file__).parent.parent/'core/tests/components/tuya'
fx=sorted(p.stem for p in (ROOT/'fixtures').glob('*.json'))
dev_id={c:c.replace('_','')[::-1] for c in fx}
def fixture_of(uid):
    best=None
    for c,d in dev_id.items():
        if uid.startswith('tuya.'+d) and (best is None or len(d)>len(dev_id[best])): best=c
    return best
def plain(x):
    if isinstance(x,dict):
        if '$enum' in x: return x['v']
        if '$set' in x: return sorted(map(plain,x['$set']),key=str)
        if '$type' in x: return plain(x['v'])
        return {k:plain(v) for k,v in x.items()}
    if isinstance(x,list): return [plain(i) for i in x]
    return x
SKIP=('config_flow','init','diagnostics','services')
gold=collections.defaultdict(lambda: collections.defaultdict(list)); orphan=[]
for f in sorted((ROOT/'snapshots').glob('test_*.ambr')):
    plat=f.stem[5:]
    if plat in SKIP: continue
    ents={}
    for name,val in parse(f).items():
        m=re.match(r"(test_\w+)\[(.+)-(entry|state)\]$",name)
        if not m: orphan.append((plat,name)); continue
        ents.setdefault((m.group(1),m.group(2)),{})[m.group(3)]=plain(val)
    for (test,eid),d in ents.items():
        e=d.get('entry'); s=d.get('state')
        if not e: orphan.append((plat,eid,'noentry')); continue
        e=e['v'] if 'v' in e and '$type' not in e else e
        st=(s or {}).get('attributes') if s else None
        rec={'entity_id':e['entity_id'],'domain':e['domain'],'unique_id':e['unique_id'],
             'translation_key':e['translation_key'],'device_class':e['original_device_class'],
             'entity_category':e['entity_category'],'disabled_by':e['disabled_by'],
             'supported_features':e['supported_features'],'capabilities':e['capabilities'],
             'unit':e['unit_of_measurement'],'original_name':e['original_name'],'name':e['name'],
             'state':(s or {}).get('state'),'attributes':st,'test':test}
        c=fixture_of(e['unique_id'])
        if c is None: orphan.append((plat,e['unique_id'],'nofixture')); continue
        rec['key']=e['unique_id'][len('tuya.'+dev_id[c]):]
        gold[c][plat].append(rec)
out=pathlib.Path(__file__).parent/'golden.json'
out.write_text(json.dumps({c:gold[c] for c in gold},indent=1,ensure_ascii=False,default=str))
n=sum(len(v) for c in gold for v in gold[c].values())
print('fixtures with entities',len(gold),'/',len(fx),'entities',n,'orphans',len(orphan)); print(orphan[:8])
print(collections.Counter(p for c in gold for p,v in gold[c].items() for _ in v))
