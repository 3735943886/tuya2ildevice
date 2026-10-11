use crate::{adapter::*, data::*, plan::Plan};
use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use serde_json::{Value as V, json};
use std::borrow::Cow;
#[derive(Clone, Serialize, Deserialize)]
pub struct Binding {
    pub plan: usize,
    pub read: String,
    pub action: String,
    pub arg: String,
    pub transform: String,
    pub total: f64,
    pub last_ts: V,
    pub custom: Option<Role>,
    pub send: V,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Assembly {
    pub descriptor: V,
    pub bindings: IndexMap<String, Binding>,
    pub unsupported: Vec<String>,
    pub covers: Vec<(usize, String, Option<String>, bool)>,
}
fn name(base: &str, props: &V) -> String {
    let base: String = base
        .to_lowercase()
        .chars()
        .map(|c| {
            if c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    let base = if base.is_empty() {
        "prop".to_string()
    } else {
        base
    };
    let mut out = base.clone();
    let mut i = 2;
    while props.get(&out).is_some() || out == "available" {
        out = format!("{base}_{i}");
        i += 1;
    }
    out
}
fn substitute(v: &V, p: &Plan) -> V {
    if let Some(text) = v.as_str() {
        if let Some(k) = text.strip_prefix("$identity.") {
            return p.identity[k].clone();
        }
        if let Some(k) = text.strip_prefix("$config.") {
            return p.config[k].clone();
        }
        if let Some(k) = text
            .strip_prefix("$roles.")
            .and_then(|t| t.strip_suffix(".range"))
        {
            return json!(p.role(k).map(Role::range).unwrap_or_default());
        }
    }
    v.clone()
}
fn when(w: &str, p: &Plan, hazard: bool, allow: bool, rules: &V) -> bool {
    if let Some(k) = w.strip_prefix("role:") {
        return p.role(k).is_some();
    }
    if let Some(ks) = w.strip_prefix("any:") {
        return ks.split(',').any(|k| p.role(k).is_some());
    }
    if let Some(k) = w.strip_prefix("nonempty:") {
        return truth(&p.identity[k]);
    }
    if let Some(a) = w.strip_prefix("cover:") {
        let bit = match a {
            "open" => "OPEN",
            "close" => "CLOSE",
            _ => "STOP",
        };
        return !hazard
            && (n(&p.identity["supported_features"]) as i64
                & n(&rules["features"]["cover"][bit]) as i64
                != 0);
    }
    if let Some(raw) = w.strip_prefix("alarm:") {
        return (raw != "disarmed" || allow)
            && p.role("master_mode")
                .is_some_and(|r| r.range().contains(&json!(raw)));
    }
    if let Some(a) = w.strip_prefix("vacuum:") {
        return n(&p.identity["supported_features"]) as i64
            & n(&rules["features"]["vacuum"][a]) as i64
            != 0;
    }
    match w {
        "light_modes" => {
            p.role("color_data").is_some()
                && (p.role("color_temp").is_some()
                    || items(&p.identity["supported_color_modes"]).contains(&json!("white")))
        }
        "climate_mode" => p.role("hvac_mode").is_some() && truth(&p.config["mode_options"]),
        _ => true,
    }
}
pub fn assemble(device: &V, plans: &mut [Plan], info: &V, allow: bool, rules: &V) -> Assembly {
    let mut props = json!({});
    let mut bindings = IndexMap::new();
    let mut groups = json!({});
    let mut kind = V::Null;
    let mut klass = V::Null;
    let mut covers = vec![];
    let mut unsupported = vec![];
    for (idx, p) in plans.iter_mut().enumerate() {
        let platform = p.platform.clone();
        if platform == "camera" {
            unsupported.push(format!("camera:{}", p.key));
            continue;
        }
        if platform == "climate" {
            p.config["mode_options"] = json!(
                arr(&p.identity["hvac_modes"])
                    .into_iter()
                    .filter(|v| *v != "off")
                    .collect::<Vec<_>>()
            );
        }
        let composite = [
            "light",
            "cover",
            "fan",
            "siren",
            "valve",
            "humidifier",
            "climate",
            "alarm_control_panel",
            "vacuum",
        ]
        .contains(&platform.as_str());
        let hazard = platform == "cover"
            && !allow
            && items(&rules["constants"]["hazardous_covers"]).contains(&p.identity["device_class"]);
        let mut extra = json!({});
        if ["config", "diagnostic"].contains(&s(&p.identity["entity_category"])) {
            extra["category"] = p.identity["entity_category"].clone();
        }
        let mut pre = String::new();
        let mut group = None;
        if composite {
            let k = if platform == "alarm_control_panel" {
                "alarm"
            } else {
                &platform
            };
            if kind.is_null() {
                kind = json!(k);
                klass = p.identity["device_class"].clone();
            } else {
                let g = name(&p.key, &props);
                let mut gd = json!({"kind":k});
                if truth(&p.identity["device_class"]) {
                    gd["class"] = p.identity["device_class"].clone();
                }
                groups[&g] = gd;
                extra["group"] = json!(g);
                pre = format!("{g}_");
                group = Some(g);
            }
        }
        if platform == "cover" {
            covers.push((idx, pre.clone(), group, hazard));
        }
        let rows = if platform == "sensor" {
            let mut def = json!({});
            let ty = match s(&p.identity["kind"]) {
                "enum" => "select",
                "text" => "text",
                _ => "number",
            };
            def["type"] = json!(ty);
            if ty == "select" {
                def["options"] = p
                    .identity
                    .get("options")
                    .cloned()
                    .unwrap_or_else(|| json!(p.role("main").unwrap().range()));
            }
            if ty == "number" {
                if let Some(u) = p.identity["native_unit"].as_str().filter(|u| !u.is_empty()) {
                    def["unit"] = json!(u.replace('µ', "μ"));
                }
                match s(&p.identity["state_class"]) {
                    "total" | "total_increasing" => def["series"] = json!("counter"),
                    "measurement" => def["series"] = json!("gauge"),
                    _ => {}
                }
            }
            vec![json!({"name":"$key","definition":def,"read":"native_value"})]
        } else {
            arr(&rules["layouts"][&platform])
        };
        for row in rows {
            if !when(s(&row["when"]), p, hazard, allow, rules) {
                continue;
            }
            let base = if row["name"] == "$key" {
                if p.key.is_empty() {
                    p.role("switch").map(|r| r.code.clone()).unwrap_or_default()
                } else {
                    p.key.clone()
                }
            } else {
                format!("{pre}{}", s(&row["name"]))
            };
            let prop = name(&base, &props);
            let mut def = json!({});
            for (k, v) in obj(&row["definition"]) {
                def[&k] = substitute(&v, p);
            }
            merge(&mut def, &extra);
            if !composite {
                for (from, to) in [("label", "label"), ("device_class", "class")] {
                    if truth(&p.identity[from]) {
                        def[to] = p.identity[from].clone();
                    }
                }
                if platform == "button"
                    && !items(&rules["constants"]["button_classes"]).contains(&def["class"])
                {
                    def.as_object_mut().unwrap().remove("class");
                }
                if platform == "number" && truth(&p.identity["native_unit"]) {
                    def["unit"] = json!(s(&p.identity["native_unit"]).replace('µ', "μ"));
                }
            }
            if platform == "climate"
                && p.identity["temperature_unit"] != "°C"
                && ["on", "mode", "target_temperature", "current_temperature"]
                    .contains(&s(&def["role"]))
            {
                def.as_object_mut().unwrap().remove("role");
            }
            let mut action = s(&row["action"]).to_string();
            if platform == "cover" {
                if row["name"] == "position" {
                    if p.role("set_position").is_some() && !hazard {
                        def["rw"] = json!(true)
                    } else {
                        action.clear()
                    }
                }
                if row["name"] == "tilt" {
                    def["rw"] = json!(!hazard);
                }
            }
            def["src"] = json!(p.depends_on.first().unwrap_or(&p.key));
            props[&prop] = def;
            bindings.insert(
                prop,
                Binding {
                    plan: idx,
                    read: s(&row["read"]).into(),
                    action,
                    arg: s(&row["arg"]).into(),
                    transform: s(&row["transform"]).into(),
                    total: 0.0,
                    last_ts: V::Null,
                    custom: None,
                    send: V::Null,
                },
            );
        }
    }
    let mut available = json!({"available":{"type":"binary","role":"available"}});
    merge(&mut available, &props);
    let mut descriptor = json!({"il":0,"id":device["id"],"source":"tuya","props":available,"identifiers":{"tuya_id":device["id"]}});
    if truth(&device["product_id"]) {
        descriptor["identifiers"]["tuya_product_id"] = device["product_id"].clone();
    }
    for (k, v) in [
        ("kind", kind),
        ("class", klass),
        ("label", device["name"].clone()),
        ("vendor", info["manufacturer"].clone()),
        ("model", info["model"].clone()),
    ] {
        if truth(&v) {
            descriptor[k] = v;
        }
    }
    if truth(&groups) {
        descriptor["groups"] = groups;
    }
    Assembly {
        descriptor,
        bindings,
        unsupported,
        covers,
    }
}
impl Binding {
    pub fn read(&self, p: &Plan, st: &V, rules: &V, definition: &V) -> V {
        if let Some(r) = &self.custom {
            let raw = &st[&r.code];
            return match s(&definition["type"]) {
                "text" => {
                    if raw.is_null() {
                        V::Null
                    } else if raw.is_string() {
                        raw.clone()
                    } else {
                        json!(raw.to_string())
                    }
                }
                "select" => {
                    let v = r.read(st);
                    if items(&definition["options"]).contains(&v) {
                        v
                    } else {
                        V::Null
                    }
                }
                _ => r.read(st),
            };
        }
        if self.read.starts_with('@') {
            return p.get(&self.read[1..], st);
        }
        let mut copied = Cow::Borrowed(st);
        if self.transform == "climate_mode"
            && let Some(r) = p.role("switch")
        {
            copied.to_mut()[&r.code] = json!(true);
        }
        let v = p.read(&copied, self.total, rules)[&self.read].clone();
        if v.is_null() {
            return v;
        }
        let output = match self.transform.as_str() {
            "brightness" => number((n(&v) * 100.0 / 255.0).round_ties_even().clamp(1.0, 100.0)),
            "kelvin" => number(n(&v).clamp(
                n(&p.identity["min_color_temp_kelvin"]),
                n(&p.identity["max_color_temp_kelvin"]),
            )),
            "not" => json!(!truth(&v)),
            "color_mode" => {
                if v == "hs" {
                    json!("color")
                } else if v == "white" || v == "color_temp" {
                    json!("white")
                } else {
                    V::Null
                }
            }
            "color" => {
                let rgb = hsv_rgb(
                    n(&v[0]).rem_euclid(360.0) / 360.0,
                    n(&v[1]).clamp(0.0, 100.0) / 100.0,
                    1.0,
                );
                json!(format!(
                    "#{:02x}{:02x}{:02x}",
                    (rgb[0] * 255.0).round_ties_even() as i64,
                    (rgb[1] * 255.0).round_ties_even() as i64,
                    (rgb[2] * 255.0).round_ties_even() as i64
                ))
            }
            "climate_on" => json!(v != "off"),
            "climate_mode" => {
                if items(&definition["options"]).contains(&v) {
                    v
                } else {
                    V::Null
                }
            }
            _ => {
                if definition["type"] == "select" && !items(&definition["options"]).contains(&v) {
                    V::Null
                } else {
                    v
                }
            }
        };
        if output.is_number() {
            number(n(&output))
        } else {
            output
        }
    }
    pub fn write(&self, p: &Plan, value: &V, st: &V, rules: &V) -> Result<Vec<V>, String> {
        if let Some(r) = &self.custom {
            let v = if self.action == "trigger" {
                &self.send
            } else {
                value
            };
            return Ok(vec![json!({"code":r.code,"value":r.write(v)?})]);
        }
        let mut v = value.clone();
        if self.transform == "brightness" {
            v = number((n(value) * 255.0 / 100.0).round_ties_even().max(1.0))
        }
        if self.transform == "color" {
            let bytes = hexbytes(s(value).strip_prefix('#').ok_or("not a #rrggbb colour")?)
                .filter(|b| b.len() == 3)
                .ok_or("not a #rrggbb colour")?;
            let h = rgb_hsv(
                bytes[0] as f64 / 255.0,
                bytes[1] as f64 / 255.0,
                bytes[2] as f64 / 255.0,
            );
            v = json!([h[0] * 360.0, h[1] * 100.0]);
        }
        let mut action = self.action.clone();
        let mut args = json!({});
        if action == "@switch" || action == "@valve" {
            action = if action == "@valve" {
                if truth(&v) { "open" } else { "close" }
            } else if truth(&v) {
                "turn_on"
            } else {
                "turn_off"
            }
            .into()
        } else if action == "@swing" {
            let axis = self.arg.as_str();
            let mode = if axis == "on" {
                if truth(&v) { "on" } else { "off" }
            } else {
                let h = if axis == "horizontal" {
                    truth(&v)
                } else {
                    truth(&p.get("swing_h", st))
                };
                let vv = if axis == "vertical" {
                    truth(&v)
                } else {
                    truth(&p.get("swing_v", st))
                };
                if h && vv {
                    "both"
                } else if h {
                    "horizontal"
                } else if vv {
                    "vertical"
                } else {
                    "off"
                }
            };
            action = "set_swing_mode".into();
            args["swing_mode"] = json!(mode);
        } else if !self.arg.is_empty() {
            args[&self.arg] = v;
        }
        p.write(&action, &args, st, rules)
    }
}
