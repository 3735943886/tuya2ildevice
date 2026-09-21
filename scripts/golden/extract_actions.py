"""Extract write-side goldens (service call -> commands sent) from core's parametrized tests via AST.
Symbolic HA constants (SERVICE_*, ATTR_*, enums) are kept as "$Name" strings."""
import ast, itertools, json, pathlib, sys
CORE=pathlib.Path(sys.argv[1]) if len(sys.argv)>1 else pathlib.Path('core/tests/components/tuya')
def ev(n):
    if isinstance(n,ast.Constant): return n.value
    if isinstance(n,ast.Name): return f'${n.id}'
    if isinstance(n,ast.Attribute): return f'${ast.unparse(n)}'
    if isinstance(n,(ast.List,ast.Tuple)): return [ev(x) for x in n.elts]
    if isinstance(n,ast.Dict): return {(ev(k) if k is not None else '**'):ev(v) for k,v in zip(n.keys,n.values)}
    if isinstance(n,ast.UnaryOp) and isinstance(n.op,ast.USub): return -ev(n.operand)
    if isinstance(n,ast.Call) and ast.unparse(n.func) in('pytest.param','param'):
        return {'$param':[ev(a) for a in n.args],'id':next((ev(k.value) for k in n.keywords if k.arg=='id'),None)}
    return f'$expr:{ast.unparse(n)}'
def names(n):
    v=ev(n); return [x.strip() for x in v.split(',')] if isinstance(v,str) else v
out=[]
for f in sorted(CORE.glob('test_*.py')):
    SRC=f.read_text(); tree=ast.parse(SRC)
    for fn in ast.walk(tree):
        if not isinstance(fn,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
        params=[]
        for d in fn.decorator_list:
            if isinstance(d,ast.Call) and ast.unparse(d.func).endswith('parametrize') and len(d.args)>=2:
                nm=names(d.args[0]); vals=ev(d.args[1])
                nm=[nm] if isinstance(nm,str) else nm
                params.append((nm,vals))
        allnames=[n for p in params for n in p[0]]
        KEYS=('expected_commands','expected_command','command')
        if not any(k in allnames for k in KEYS): continue
        combos=itertools.product(*[p[1] for p in params])
        for combo in combos:
            row={}
            for (nm,_),v in zip(params,combo):
                if isinstance(v,dict) and '$param' in v: v=v['$param']
                if len(nm)==1: row[nm[0]]=v if not (isinstance(v,list) and False) else v
                else: row.update(dict(zip(nm,v)))
            for k in ('expected_command','command'):
                if k in row: row['expected_commands']=[row.pop(k)]
            if 'entity_id' not in row:
                import re
                m=re.search(r'entity_id\s*=\s*"([a-z_]+\.[a-z0-9_]+)"',ast.get_source_segment(SRC,fn) or '')
                if m: row['entity_id']=m.group(1)
            row.update(test=f'{f.stem}::{fn.name}')
            out.append(row)
# hand-transcribed single-case tests (not parametrized in core)
G='$Platform'
out+= [
 dict(test='test_button::test_action',mock_device_code='sd_lr33znaodtyarrrz',entity_id='button.v20_reset_duster_cloth',service='$SERVICE_PRESS',service_data={},expected_commands=[{'code':'reset_duster_cloth','value':True}]),
 dict(test='test_select::test_select_option',mock_device_code='cl_zah67ekd',entity_id='select.kitchen_blinds_motor_mode',service='$SERVICE_SELECT_OPTION',service_data={'$ATTR_OPTION':'forward'},expected_commands=[{'code':'control_back_mode','value':'forward'}]),
 dict(test='test_number::test_set_value',mock_device_code='mal_gyitctrjj1kefxp2',entity_id='number.multifunction_alarm_arm_delay',service='$SERVICE_SET_VALUE',service_data={'$ATTR_VALUE':18},expected_commands=[{'code':'delay_set','value':18}]),
]
json.dump(out,open(pathlib.Path(__file__).parent/'actions_golden.json','w'),indent=1,ensure_ascii=False)
import collections; print(len(out),'action cases'); print(collections.Counter(r['test'].split('::')[0] for r in out))
print({k for r in out for k in r})
