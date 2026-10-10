use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value as V, json};
pub type Obj = Map<String, V>;
pub type Roles = IndexMap<String, Role>;
pub fn s(v: &V) -> &str {
    v.as_str().unwrap_or("")
}
pub fn n(v: &V) -> f64 {
    v.as_f64()
        .or_else(|| v.as_str().and_then(|s| s.parse::<f64>().ok()))
        .unwrap_or_else(|| if v == true { 1.0 } else { 0.0 })
}
pub fn truth(v: &V) -> bool {
    match v {
        V::Null => false,
        V::Bool(b) => *b,
        V::Number(_) => n(v) != 0.0,
        V::String(x) => !x.is_empty(),
        V::Array(a) => !a.is_empty(),
        V::Object(o) => !o.is_empty(),
    }
}
pub fn eq(a: &V, b: &V) -> bool {
    if (a.is_number() || a.is_boolean()) && (b.is_number() || b.is_boolean()) {
        n(a) == n(b)
    } else {
        a == b
    }
}
pub fn arr(v: &V) -> Vec<V> {
    v.as_array().cloned().unwrap_or_default()
}
pub fn obj(v: &V) -> Obj {
    v.as_object().cloned().unwrap_or_default()
}
pub fn codes(v: &V) -> Vec<String> {
    if v.is_string() {
        vec![s(v).into()]
    } else {
        arr(v).iter().map(|x| s(x).into()).collect()
    }
}
pub fn number(x: f64) -> V {
    if !x.is_finite() {
        V::Null
    } else if x.fract() == 0.0 && x.abs() < 9.22e18 {
        json!(x as i64)
    } else {
        json!(x)
    }
}
pub fn parse(v: &V) -> V {
    if let Some(x) = v.as_str() {
        serde_json::from_str(x).unwrap_or(V::Null)
    } else {
        v.clone()
    }
}
pub fn normalized(t: &str) -> &str {
    match t {
        "Integer" | "value" => "Integer",
        "Boolean" | "bool" => "Boolean",
        "Enum" | "enum" => "Enum",
        "Bitmap" | "bitmap" => "Bitmap",
        "Json" | "json" => "Json",
        "Raw" | "raw" => "Raw",
        "String" | "string" => "String",
        _ => "",
    }
}
pub fn merge(dst: &mut V, src: &V) {
    if let (Some(d), Some(sr)) = (dst.as_object_mut(), src.as_object()) {
        for (k, v) in sr {
            if d.get(k).is_some_and(V::is_object) && v.is_object() {
                merge(d.get_mut(k).unwrap(), v)
            } else {
                d.insert(k.clone(), v.clone());
            }
        }
    }
}
#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Role {
    pub code: String,
    pub kind: String,
    pub spec: V,
    pub report_type: V,
}
impl Role {
    pub fn lo(&self) -> f64 {
        n(&self.spec["min"])
    }
    pub fn hi(&self) -> f64 {
        n(&self.spec["max"])
    }
    pub fn scale(&self) -> f64 {
        10f64.powi(n(&self.spec["scale"]) as i32)
    }
    pub fn range(&self) -> Vec<V> {
        arr(&self.spec["range"])
    }
    pub fn read(&self, st: &V) -> V {
        let raw = &st[&self.code];
        match self.kind.as_str() {
            "Boolean" => {
                if !raw.is_null() && (eq(raw, &json!(true)) || eq(raw, &json!(false))) {
                    raw.clone()
                } else {
                    V::Null
                }
            }
            "Enum" => {
                if self.range().contains(raw) {
                    raw.clone()
                } else {
                    V::Null
                }
            }
            "Integer" => {
                if (raw.is_i64() || raw.is_u64() || raw.is_boolean())
                    && n(raw) >= self.lo()
                    && n(raw) <= self.hi()
                {
                    json!(if self.spec["inverted"] == true {
                        self.hi() / self.scale() - n(raw) / self.scale()
                    } else {
                        n(raw) / self.scale()
                    })
                } else {
                    V::Null
                }
            }
            _ => raw.clone(),
        }
    }
    pub fn write(&self, v: &V) -> Result<V, String> {
        match self.kind.as_str() {
            "Boolean" => {
                if v.is_boolean() {
                    Ok(v.clone())
                } else {
                    Err("invalid boolean value".into())
                }
            }
            "Enum" => {
                if v.is_string() && self.range().contains(v) {
                    Ok(v.clone())
                } else {
                    Err("invalid enum value".into())
                }
            }
            "Integer" => {
                if !v.is_number() && !v.is_boolean() {
                    return Err("invalid numeric value".into());
                }
                let input = if self.spec["inverted"] == true {
                    self.hi() / self.scale() - n(v)
                } else {
                    n(v)
                };
                let x = (input * self.scale()).round_ties_even();
                if x < self.lo() || x > self.hi() {
                    Err("value out of range".into())
                } else {
                    Ok(number(x))
                }
            }
            _ => Ok(v.clone()),
        }
    }
    pub fn pct(&self, st: &V, lo: f64, hi: f64) -> V {
        let v = self.read(st);
        if v.is_null() {
            V::Null
        } else {
            number(
                remap(
                    n(&v),
                    self.lo() / self.scale(),
                    self.hi() / self.scale(),
                    lo,
                    hi,
                )
                .round_ties_even(),
            )
        }
    }
    pub fn pct_write(&self, v: &V, lo: f64, hi: f64) -> Result<V, String> {
        self.write(&number(remap(
            n(v),
            lo,
            hi,
            self.lo() / self.scale(),
            self.hi() / self.scale(),
        )))
    }
}
pub fn remap(v: f64, a: f64, b: f64, c: f64, d: f64) -> f64 {
    (v - a) / (b - a) * (d - c) + c
}
pub fn resolve(
    device: &V,
    candidates: &[String],
    kind: &str,
    ff: bool,
) -> Result<Option<Role>, String> {
    for code in candidates {
        for table in if ff {
            ["function", "status_range"]
        } else {
            ["status_range", "function"]
        } {
            let e = &device[table][code];
            if normalized(s(&e["type"])) != kind {
                continue;
            }
            let mut spec = parse(&e["values"]);
            if matches!(kind, "Integer" | "Enum" | "Bitmap") && !truth(&spec) {
                continue;
            }
            if kind == "Integer"
                && ["min", "max", "scale", "step"]
                    .iter()
                    .any(|k| spec[*k].is_null())
            {
                return Err(format!("schema {code}: missing integer field"));
            }
            if kind == "Integer" {
                for field in ["min", "max", "scale", "step"] {
                    let v = &spec[field];
                    let integer = if let Some(text) = v.as_str() {
                        text.trim().parse::<i64>().ok()
                    } else if v.is_number() || v.is_boolean() {
                        Some(n(v).trunc() as i64)
                    } else {
                        None
                    };
                    spec[field] = json!(
                        integer.ok_or_else(|| format!("schema {code}: invalid integer {field}"))?
                    );
                }
            }
            return Ok(Some(Role {
                code: code.clone(),
                kind: kind.into(),
                spec,
                report_type: device["status_range"][code]["report_type"].clone(),
            }));
        }
    }
    Ok(None)
}
pub fn has(d: &V, c: &str) -> bool {
    ["function", "status_range", "status"]
        .iter()
        .any(|t| d[*t].get(c).is_some())
}
pub fn identity(d: &V) -> V {
    let mut o = obj(d);
    o.retain(|k, _| !k.starts_with('$'));
    o.entry("entity_registry_enabled_default")
        .or_insert(json!(true));
    V::Object(o)
}
pub fn cond(c: &V, status: &V) -> V {
    let Some(o) = c.as_object() else {
        return c.clone();
    };
    let Some((k, v)) = o.iter().next() else {
        return V::Null;
    };
    match k.as_str() {
        "status" => status[s(v)].clone(),
        "is_int" => {
            let x = cond(v, status);
            json!(x.is_i64() || x.is_u64() || x.is_boolean())
        }
        "ge" => {
            let a = cond(&v[0], status);
            let b = cond(&v[1], status);
            json!(
                (a.is_number() || a.is_boolean())
                    && (b.is_number() || b.is_boolean())
                    && n(&a) >= n(&b)
            )
        }
        "and" => json!(arr(v).iter().all(|x| truth(&cond(x, status)))),
        "or" => json!(arr(v).iter().any(|x| truth(&cond(x, status)))),
        "not" => json!(!truth(&cond(v, status))),
        _ => V::Null,
    }
}
pub fn patch_op(device: &mut V, dpmap: &mut V, op: &V) {
    let code = s(&op["code"]);
    match s(&op["op"]) {
        "SetCategory" => device["category"] = op["category"].clone(),
        "DefineDp" => {
            for (t, m) in [("function", "W"), ("status_range", "R")] {
                if s(&op["mode"]).contains(m) {
                    let mut spec = json!({"type":op["type"],"values":op["values"]});
                    if t == "status_range" {
                        spec["report_type"] = op["report_type"].clone();
                    }
                    device[t][code] = spec;
                } else {
                    device[t].as_object_mut().unwrap().remove(code);
                }
            }
            let id = if op["dpid"].is_string() {
                s(&op["dpid"]).to_string()
            } else {
                op["dpid"].to_string()
            };
            dpmap[&id] = json!(code);
        }
        "TypeOverride" => {
            device["type_overrides"][code] = op["as"].clone();
        }
        "RemoveDp" => {
            for t in ["function", "status_range", "status"] {
                device[t].as_object_mut().unwrap().remove(code);
            }
            let id = op["dpid"].to_string();
            dpmap.as_object_mut().unwrap().remove(id.trim_matches('"'));
        }
        _ => {}
    }
}
