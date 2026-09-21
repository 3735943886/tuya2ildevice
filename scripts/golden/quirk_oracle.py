"""Dump post-quirk schemas (handlers 0.0.29 = oracle) for every fixture whose product_id has a quirk."""
import json, sys, copy, pathlib
from types import SimpleNamespace as NS
from tuya_sharing import DeviceFunction, DeviceStatusRange
from tuya_device_handlers import TUYA_QUIRKS_REGISTRY as REG
from tuya_device_handlers.devices import register_tuya_quirks
import fixtures
register_tuya_quirks(None)
print('quirks registered:', len(REG._quirks))
def mk(d):
    o=NS(id=d['id'],category=d['category'],product_id=d['product_id'],local_strategy=copy.deepcopy(d['local_strategy']),
         function={k:DeviceFunction(code=k,type=v['type'],values=v['values']) for k,v in d['function'].items()},
         status_range={k:DeviceStatusRange(code=k,report_type=v['report_type'],type=v['type'],values=v['values']) for k,v in d['status_range'].items()},
         status=dict(d['status']),support_local=d['support_local'],name=d['name'],online=d['online'])
    return o
def dump(o):
    return {'category':o.category,'local_strategy':o.local_strategy,
            'function':{k:{'type':v.type,'values':v.values} for k,v in o.function.items()},
            'status_range':{k:{'type':v.type,'values':v.values,'report_type':getattr(v,'report_type',None)} for k,v in o.status_range.items()},
            'status':o.status}
out={}; changed=0
for c in fixtures.all_codes():
    d=fixtures.load(c)
    q=REG.get_quirk_for_device(SimpleNamespace(product_id=d['product_id'])) if False else REG._quirks.get(d['product_id'])
    if not q: continue
    o=mk(d); before=dump(o)
    try: REG.initialise_device_quirk(o)
    except Exception as e: out[c]={'error':repr(e)}; continue
    after=dump(o); changed+= before!=after
    out[c]={'before':before,'after':after,'quirk_file':pathlib.Path(q.quirk_file).name,
            'type_overrides':{}, 'has_type_information_cls':None}
json.dump(out,open('quirk_golden.json','w'),indent=1,ensure_ascii=False,default=str)
print('fixtures with quirk',len(out),'changed schema',changed,'errors',sum('error' in v for v in out.values()))
