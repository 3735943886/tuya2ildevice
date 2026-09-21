"""Load core test fixtures the way tests/components/tuya/__init__.py::create_device does."""
import json, pathlib
FIX=pathlib.Path(__file__).parent/'core_fixtures'   # copied from HA core tests/components/tuya/fixtures (Apache-2.0)
def load(code):
    d=json.load(open(FIX/f'{code}.json'))
    dumps=lambda v: json.dumps(v,separators=(',',':'))  # core json_dumps (orjson) is compact
    def vals(v): return v if isinstance(v,str) else dumps(v)
    func={k:{'code':k,'type':v['type'],'values':vals(v['value'])} for k,v in d['function'].items()}
    rng={k:{'code':k,'type':v['type'],'report_type':v.get('report_type'),'values':vals(v['value'])} for k,v in d['status_range'].items()}
    status=dict(d['status'])
    for k,v in list(status.items()):
        if (k in rng and rng[k]['type']=='Json') or (k in func and func[k]['type']=='Json'):
            status[k]=dumps(v)
        if v=='**REDACTED**': status[k]=''
    return {'id':code.replace('_','')[::-1],'name':d['name'],'category':d['category'],'product_id':d['product_id'],
            'product_name':d['product_name'],'online':d['online'],'local_strategy':d.get('local_strategy') or {},
            'function':func,'status_range':rng,'status':status,'support_local':d.get('support_local')}
def all_codes(): return sorted(p.stem for p in FIX.glob('*.json'))
