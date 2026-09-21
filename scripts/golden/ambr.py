"""Minimal parser for syrupy AmberSerializer (.ambr) snapshot files -> plain JSON-able data."""
import re, json, sys, pathlib

TOK = re.compile(r"""\s*(?:
 (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
|(?P<enum><[^<>]*?:\s*[^<>]*>)
|(?P<any><ANY>)
|(?P<num>-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)
|(?P<name>[A-Za-z_][A-Za-z_0-9.]*)
|(?P<p>[()\[\]{},:])
)""", re.X)

def _unq(x):
    try: return eval(x) if x[:1] in "'\"" else x
    except Exception: return x

class P:
    def __init__(s, text): s.t=text; s.i=0
    def nxt(s):
        m=TOK.match(s.t,s.i)
        if not m or m.end()==s.i: raise ValueError(f"tok@{s.i}: {s.t[s.i:s.i+60]!r}")
        s.i=m.end(); k=m.lastgroup; return k,m.group(k)
    def peek(s):
        i=s.i; r=s.nxt(); s.i=i; return r
    def val(s):
        k,v=s.nxt()
        if k=='str': return eval(v)
        if k=='num': return float(v) if ('.' in v or 'e' in v) else int(v)
        if k=='any': return '<ANY>'
        if k=='enum':
            m=re.match(r"<(.*?):\s*(.*)>$",v,re.S); return {'$enum':m.group(1).strip(),'v':_unq(m.group(2).strip())}
        if k=='name':
            if v in('None','True','False'): return {'None':None,'True':True,'False':False}[v]
            if s.peek()==('p','('):  # Container(...)
                s.nxt(); inner=s.container(')'); 
                if v in('list','tuple'): return inner if isinstance(inner,list) else ([] if inner is None else list(inner.values()))
                if v=='set': return {'$set': inner if isinstance(inner,list) else list(inner.values())}
                return inner if v in('dict','ReadOnlyDict') else {'$type':v,'v':inner}
            return {'$name':v}
        raise ValueError((k,v))
    def container(s,close):
        # after 'X(' : either '{...}' / '[...]' then ')'
        if close==')' and s.peek() not in (('p','{'),('p','['),('p',')')):
            a=[]
            while s.peek()!=('p',')'):
                a.append(s.val())
                if s.peek()==('p',','): s.nxt()
            s.nxt(); return a
        k,v=s.nxt()
        if v=='{': r=s.mapping()
        elif v=='[': r=s.seq()
        elif v==close: return None
        else: raise ValueError(v)
        assert s.nxt()==('p',close); return r
    def mapping(s):
        d={}
        while True:
            if s.peek()==('p','}'): s.nxt(); return d
            k=s.val(); assert s.nxt()==('p',':'); v=s.val()
            d[k if isinstance(k,str) else (k['v'] if isinstance(k,dict) and 'v' in k else json.dumps(k))]=v
            if s.peek()==('p',','): s.nxt()
    def seq(s):
        a=[]
        while True:
            if s.peek()==('p',']'): s.nxt(); return a
            a.append(s.val())
            if s.peek()==('p',','): s.nxt()

def parse(path):
    txt=pathlib.Path(path).read_text()
    out={}
    for blk in re.split(r"^# name: ", txt, flags=re.M)[1:]:
        name,_,body=blk.partition("\n")
        body=body.split("\n# ---")[0]
        out[name.strip()]=P(body).val() if body.strip() else None
    return out

if __name__=='__main__':
    r=parse(sys.argv[1]); print(len(r)); k=next(iter(r)); print(k, json.dumps(r[k],indent=1)[:800])
