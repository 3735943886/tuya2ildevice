"""Apply every registered quirk (handlers 0.0.29) to an empty-schema device; dump the resulting patch.
Complements quirk_oracle.py (real fixtures). Run with the oracle venv."""
import json, pathlib
from types import SimpleNamespace as NS
from tuya_device_handlers import TUYA_QUIRKS_REGISTRY as REG
from tuya_device_handlers.devices import register_tuya_quirks
register_tuya_quirks(None)
out={}
for pid,q in sorted(REG._quirks.items()):
    o=NS(id='x',category='',product_id=pid,local_strategy={},function={},status_range={},status={},support_local=True,name='n',online=True)
    try: REG.initialise_device_quirk(o); err=None
    except Exception as e: err=repr(e)
    out[pid]={'quirk_file':pathlib.Path(q.quirk_file).name,'error':err,'category':o.category,
      'local_strategy':o.local_strategy,
      'function':{k:{'type':v.type,'values':v.values} for k,v in o.function.items()},
      'status_range':{k:{'type':v.type,'values':v.values,'report_type':getattr(v,'report_type',None)} for k,v in o.status_range.items()}}
json.dump(out,open(pathlib.Path(__file__).parent/'quirk_synthetic_golden.json','w'),indent=1,ensure_ascii=False,default=str)
print(len(out),'quirks; errors:',[k for k,v in out.items() if v['error']]); print('empty patch:',[k for k,v in out.items() if not v['status_range'] and not v['function'] and not v['error']])
