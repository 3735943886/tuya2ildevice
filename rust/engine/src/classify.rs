use crate::{adapter::*, data::*, plan::Plan};
use serde_json::{Value as V, json};
fn add(
    roles: &mut Roles,
    d: &V,
    key: &str,
    c: Vec<String>,
    kind: &str,
    ff: bool,
) -> Result<(), String> {
    if let Some(r) = resolve(d, &c, kind, ff)? {
        roles.insert(key.into(), r);
    }
    Ok(())
}
#[allow(clippy::too_many_arguments)] // Unit policy takes its explicit inputs from the language-neutral spec.
pub fn unit(p: &str, d: &V, env: &V, desc: &V, id: &mut V, dc: V, dp: V, suggested: V, rules: &V) {
    let allowed = &env["allowed_units"][p][s(&dc)];
    let fallback = &desc["native_unit_of_measurement"];
    let mut dc = dc;
    let mut u = dp.clone();
    let mut sug = if truth(&desc["suggested_unit_of_measurement"]) {
        desc["suggested_unit_of_measurement"].clone()
    } else {
        suggested
    };
    if dc == "enum" {
        u = V::Null
    } else if !dc.is_null() && !arr(allowed).contains(&dp) {
        let alias = rules["units"][s(&dc)][s(&dp)].clone();
        let alias = if alias.is_null() {
            rules["units"][s(&dc)][s(&dp).to_lowercase()].clone()
        } else {
            alias
        };
        if dc == "temperature"
            && !truth(&dp)
            && (d["status"]["temp_unit_convert"] == "c" || d["status"]["temp_unit_convert"] == "f")
        {
            u = json!(if d["status"]["temp_unit_convert"] == "c" {
                "°C"
            } else {
                "°F"
            });
        } else if !alias.is_null() {
            u = alias
        } else if !fallback.is_null() {
            u = fallback.clone()
        } else {
            dc = V::Null;
            sug = V::Null;
        }
    }
    id["native_unit"] = u;
    id["device_class"] = dc;
    id["suggested_unit"] = sug;
}
pub fn classify(d: &V, env: &V, rules: &V, only: &V) -> Result<Vec<Plan>, String> {
    let mut out = vec![];
    for group in arr(&rules["platforms"]) {
        let p = s(&group["platform"]);
        if only.is_array() && !arr(only).contains(&json!(p)) {
            continue;
        }
        let descs = &group["categories"][s(&d["category"])];
        for desc in if descs.is_object() {
            vec![descs.clone()]
        } else {
            arr(descs)
        } {
            if let Some(plan) = build(p, d, env, &desc, rules)? {
                out.push(plan)
            }
        }
    }
    Ok(out)
}
pub fn build(p: &str, d: &V, env: &V, desc: &V, rules: &V) -> Result<Option<Plan>, String> {
    let key = s(&desc["key"]).to_string();
    let mut id = identity(desc);
    let mut roles = Roles::new();
    let mut config = json!({});
    let mut deps = vec![];
    let mut slot = None;
    let mut all = false;
    let find = |c: Vec<String>, kind: &str, ff: bool| resolve(d, &c, kind, ff);
    let opt = |field: &str, kind: &str, ff: bool| find(codes(&desc[field]), kind, ff);
    match p {
        "switch" | "button" | "siren" | "valve" | "select" | "number" => {
            let kind = match p {
                "select" => "Enum",
                "number" => "Integer",
                _ => "Boolean",
            };
            let Some(r) = find(vec![key.clone()], kind, true)? else {
                return Ok(None);
            };
            if p == "number" {
                unit(
                    p,
                    d,
                    env,
                    desc,
                    &mut id,
                    desc["device_class"].clone(),
                    r.spec["unit"].clone(),
                    V::Null,
                    rules,
                );
                id["native_min_value"] = number(r.lo() / r.scale());
                id["native_max_value"] = number(r.hi() / r.scale());
                id["native_step"] = number(n(&r.spec["step"]) / r.scale());
            }
            roles.insert("main".into(), r);
        }
        "binary_sensor" => {
            let code = if truth(&desc["dpcode"]) {
                s(&desc["dpcode"]).to_string()
            } else {
                key.clone()
            };
            if !desc["bitmap_key"].is_null() {
                let Some(r) = find(vec![code.clone()], "Bitmap", false)? else {
                    return Ok(None);
                };
                let labels = arr(&r.spec["label"]);
                let Some(bit) = labels.iter().position(|v| *v == desc["bitmap_key"]) else {
                    return Ok(None);
                };
                config = json!({"read":"bitmap","code":code,"bit":bit});
                roles.insert("main".into(), r);
            } else if let Some(r) = find(vec![code.clone()], "Boolean", false)? {
                roles.insert("main".into(), r);
            } else {
                if !has(d, &code) {
                    return Ok(None);
                }
                let on = desc.get("on_value").cloned().unwrap_or(json!(true));
                config = json!({"read":"set","code":code,"on":if on.is_object(){on["$set"].clone()}else{json!([on])}});
                deps.push(code);
            }
        }
        "sensor" => {
            let code = if truth(&desc["dpcode"]) {
                s(&desc["dpcode"]).to_string()
            } else {
                key.clone()
            };
            if truth(&desc["wrapper_class"]) {
                let mut selected = false;
                for wrapper in arr(&desc["wrapper_class"]) {
                    let w = s(&wrapper).trim_start_matches('$');
                    if w == "WindDirectionEnumWrapper" {
                        if let Some(r) = find(vec![code.clone()], "Enum", false)? {
                            roles.insert("main".into(), r);
                            config["read"] = json!("wind");
                            id["kind"] = json!("wind_direction");
                            unit(
                                p,
                                d,
                                env,
                                desc,
                                &mut id,
                                desc["device_class"].clone(),
                                V::Null,
                                V::Null,
                                rules,
                            );
                            selected = true;
                            break;
                        }
                        continue;
                    }
                    let form = if w.ends_with("RawWrapper") {
                        "Raw"
                    } else if w.ends_with("JsonWrapper") {
                        "Json"
                    } else {
                        "HexString"
                    };
                    let what = w
                        .trim_start_matches("Electricity")
                        .trim_end_matches(&format!("{form}Wrapper"));
                    let Some(r) = find(
                        vec![code.clone()],
                        if form == "HexString" { "String" } else { form },
                        false,
                    )?
                    else {
                        continue;
                    };
                    let row =
                        &rules["electricity"][if form == "Json" { "json" } else { "raw" }][what];
                    if row.is_null() {
                        return Err(format!("unknown electricity wrapper {w}"));
                    }
                    let first = &d["status"][&r.code];
                    if form == "Json" {
                        let payload = parse(first);
                        if !payload.is_null() && payload.get(s(&row[0])).is_none() {
                            continue;
                        }
                    } else if truth(first) {
                        let e = if form == "Raw" {
                            b64(first).map(|b| electricity(&b))
                        } else {
                            hexbytes(s(first)).map(|b| electricity(&b))
                        };
                        if e.is_none() || e.unwrap()[s(&row[0])].is_null() {
                            continue;
                        }
                    }
                    config = json!({"read":"electricity","form":form,"attribute":row[0]});
                    id["kind"] = json!(format!("electricity_{}", form.to_lowercase()));
                    unit(
                        p,
                        d,
                        env,
                        desc,
                        &mut id,
                        desc["device_class"].clone(),
                        row[1].clone(),
                        row[2].clone(),
                        rules,
                    );
                    roles.insert("main".into(), r);
                    selected = true;
                    break;
                }
                if !selected {
                    return Ok(None);
                }
            } else if let Some(r) = find(vec![code.clone()], "Integer", false)? {
                let delta = r.report_type == "sum";
                id["kind"] = json!(if delta { "delta" } else { "integer" });
                if delta {
                    if id.get("state_class").is_none() {
                        id["state_class"] = json!("total_increasing");
                    }
                    slot = Some("delta".into());
                    config["read"] = json!("delta");
                }
                unit(
                    p,
                    d,
                    env,
                    desc,
                    &mut id,
                    desc["device_class"].clone(),
                    r.spec["unit"].clone(),
                    V::Null,
                    rules,
                );
                roles.insert("main".into(), r);
            } else if let Some(r) = find(vec![code], "Enum", false)? {
                let dc = desc.get("device_class").cloned().unwrap_or(json!("enum"));
                if dc == "enum" {
                    id["options"] = json!(r.range());
                }
                id["kind"] = json!("enum");
                unit(p, d, env, desc, &mut id, dc, V::Null, V::Null, rules);
                roles.insert("main".into(), r);
            } else {
                return Ok(None);
            }
        }
        "event" => {
            let wrapper = s(&desc["wrapper_class"]);
            let simple = wrapper.is_empty() || wrapper == "$SimpleEventEnumWrapper";
            let kind = if simple {
                "Enum"
            } else if wrapper == "$Base64Utf8StringEventWrapper" {
                "String"
            } else {
                "Raw"
            };
            let Some(r) = find(vec![key.clone()], kind, false)? else {
                return Ok(None);
            };
            id["event_types"] = if simple {
                json!(r.range())
            } else {
                json!(["triggered"])
            };
            roles.insert("main".into(), r);
            config["simple"] = json!(simple);
            slot = Some("event".into());
        }
        "camera" => {
            add(
                &mut roles,
                d,
                "motion_switch",
                vec!["motion_switch".into()],
                "Boolean",
                true,
            )?;
            add(
                &mut roles,
                d,
                "record_switch",
                vec!["record_switch".into()],
                "Boolean",
                false,
            )?;
            all = true;
        }
        "cover" => {
            if d["function"].get(&key).is_none() && d["status_range"].get(&key).is_none() {
                return Ok(None);
            }
            for (k, f, kind, ff) in [
                ("current_position", "current_position", "Integer", false),
                ("set_position", "set_position", "Integer", true),
                (
                    "current_state",
                    "current_state",
                    if desc["current_state_wrapper"] == "$DPCodeInvertedBooleanWrapper" {
                        "Boolean"
                    } else {
                        "Enum"
                    },
                    false,
                ),
            ] {
                if let Some(r) = opt(f, kind, ff)? {
                    roles.insert(k.into(), r);
                }
            }
            add(
                &mut roles,
                d,
                "tilt",
                vec!["angle_horizontal".into(), "angle_vertical".into()],
                "Integer",
                true,
            )?;
            let table = rules["maps"][if desc["instruction_wrapper"]
                == "$CoverInstructionSpecialEnumWrapper"
            {
                "_COVER_ENUM_SPECIAL"
            } else {
                "_COVER_ENUM"
            }]
            .clone();
            let mut options = vec![];
            if let Some(r) = find(vec![key.clone()], "Enum", true)? {
                for (k, v) in obj(&table) {
                    if r.range().contains(&v) {
                        options.push(json!(k));
                    }
                }
                roles.insert("instruction".into(), r);
            } else {
                options = json_vec(&["open", "close"]);
                if let Some(r) = find(vec![key.clone()], "Boolean", true)? {
                    roles.insert("instruction".into(), r);
                }
            }
            let mut feats = 0;
            for (act, f) in [("open", 1), ("close", 2), ("stop", 8)] {
                if options.contains(&json!(act)) {
                    feats |= f;
                }
            }
            if roles.contains_key("set_position") {
                feats |= 4
            }
            if roles.contains_key("tilt") {
                feats |= 128
            }
            id["supported_features"] = json!(feats);
            config = json!({"instructions":table,"instruction_options":options});
            all = true;
        }
        "fan" => {
            let table = &rules["roles"]["fan"];
            if !obj(table).values().flat_map(codes).any(|c| has(d, &c)) {
                return Ok(None);
            }
            for (k, cs) in obj(table) {
                let kind = if k == "speed" {
                    "Integer"
                } else if k == "switch" || k == "oscillate" {
                    "Boolean"
                } else {
                    "Enum"
                };
                add(&mut roles, d, &k, codes(&cs), kind, true)?;
                if k == "speed" && !roles.contains_key(&k) {
                    add(&mut roles, d, &k, codes(&cs), "Enum", true)?;
                }
            }
            let mut f = 0;
            if let Some(r) = roles.get("mode") {
                id["preset_modes"] = json!(r.range());
                f |= 8
            }
            if let Some(r) = roles.get("speed") {
                id["speed_count"] = json!(if r.kind == "Enum" {
                    r.range().len()
                } else {
                    100
                });
                f |= 1
            }
            for (k, bit) in [("oscillate", 2), ("direction", 4), ("switch", 48)] {
                if roles.contains_key(k) {
                    f |= bit
                }
            }
            id["supported_features"] = json!(f);
            all = true;
        }
        "humidifier" => {
            let cs = codes(if truth(&desc["dpcode"]) {
                &desc["dpcode"]
            } else {
                &desc["key"]
            });
            if !cs
                .iter()
                .chain(codes(&desc["current_humidity"]).iter())
                .chain(codes(&desc["humidity"]).iter())
                .any(|c| has(d, c))
            {
                return Ok(None);
            }
            add(&mut roles, d, "switch", cs, "Boolean", true)?;
            for (k, f) in [
                ("target_humidity", "humidity"),
                ("current_humidity", "current_humidity"),
            ] {
                if let Some(r) = opt(f, "Integer", k == "target_humidity")? {
                    roles.insert(k.into(), r);
                }
            }
            add(&mut roles, d, "mode", vec!["mode".into()], "Enum", true)?;
            let (lo, hi) = roles
                .get("target_humidity")
                .map(|r| (r.lo() / r.scale(), r.hi() / r.scale()))
                .unwrap_or((0.0, 100.0));
            id["min_humidity"] = number(lo.round_ties_even());
            id["max_humidity"] = number(hi.round_ties_even());
            id["supported_features"] = json!(if roles.contains_key("mode") { 1 } else { 0 });
            if let Some(r) = roles.get("mode") {
                id["available_modes"] = json!(r.range());
            }
            all = true;
        }
        "alarm_control_panel" => {
            let Some(r) = find(vec!["master_mode".into()], "Enum", true)? else {
                return Ok(None);
            };
            let range = r.range();
            roles.insert("master_mode".into(), r);
            add(
                &mut roles,
                d,
                "alarm_msg",
                vec!["alarm_msg".into()],
                "Raw",
                false,
            )?;
            id["supported_features"] = json!(
                [("home", 1), ("arm", 2), ("sos", 8)]
                    .iter()
                    .filter(|(v, _)| range.contains(&json!(v)))
                    .map(|(_, n)| n)
                    .sum::<i32>()
            );
            deps = vec![
                "master_mode".into(),
                "master_state".into(),
                "alarm_msg".into(),
            ];
            all = true;
        }
        "vacuum" => {
            for (k, cs) in obj(&rules["roles"]["vacuum"]) {
                add(
                    &mut roles,
                    d,
                    &k,
                    codes(&cs),
                    if ["mode", "status", "suction"].contains(&k.as_str()) {
                        "Enum"
                    } else {
                        "Boolean"
                    },
                    !["seek", "pause", "status"].contains(&k.as_str()),
                )?;
            }
            let mut f = n(&rules["features"]["vacuum"]["SEND_COMMAND"]) as i64;
            if roles.contains_key("switch_charge")
                || roles
                    .get("mode")
                    .is_some_and(|r| r.range().contains(&json!("chargego")))
            {
                f |= n(&rules["features"]["vacuum"]["RETURN_HOME"]) as i64;
            }
            for (k, feat) in [
                ("seek", "LOCATE"),
                ("pause", "PAUSE"),
                ("power_go", "START"),
                ("power_go", "STOP"),
                ("suction", "FAN_SPEED"),
                ("pause", "STATE"),
                ("status", "STATE"),
            ] {
                if roles.contains_key(k) {
                    f |= n(&rules["features"]["vacuum"][feat]) as i64;
                }
            }
            id["fan_speed_list"] = json!(roles.get("suction").map(Role::range).unwrap_or_default());
            id["supported_features"] = json!(f);
            all = true;
        }
        "climate" => {
            for (k, cs) in obj(&rules["roles"]["climate"]) {
                let kind = if ["unit_convert", "fan", "hvac_mode"].contains(&k.as_str()) {
                    "Enum"
                } else if k == "switch" || k.starts_with("swing_") {
                    "Boolean"
                } else {
                    "Integer"
                };
                let ff =
                    !["cur_temp_c", "cur_temp_f", "unit_convert", "cur_hum"].contains(&k.as_str());
                add(&mut roles, d, &k, codes(&cs), kind, ff)?;
            }
            let system = if env["temperature_unit"] == "F" {
                "°F"
            } else {
                "°C"
            };
            let unit_for = |r: &Role| {
                let mut u = r.spec["unit"].clone();
                if !truth(&u) && roles.contains_key("unit_convert") {
                    u = roles["unit_convert"].read(&d["status"]);
                }
                let text = s(&u).to_lowercase();
                if arr(&rules["constants"]["c_aliases"]).contains(&json!(text)) {
                    "°C"
                } else if arr(&rules["constants"]["f_aliases"]).contains(&json!(text)) {
                    "°F"
                } else if !truth(&u) {
                    if d["status"]["temp_unit_convert"] == "c" {
                        "°C"
                    } else if d["status"]["temp_unit_convert"] == "f" {
                        "°F"
                    } else {
                        ""
                    }
                } else {
                    ""
                }
            };
            let pick = |ks: &[&str], u: &str| {
                ks.iter()
                    .find(|k| roles.get(**k).is_some_and(|r| unit_for(r) == u))
                    .map(|k| (*k).to_string())
            };
            let cc = pick(&["cur_temp_c", "cur_temp_f"], "°C");
            let cf = pick(&["cur_temp_f", "cur_temp_c"], "°F");
            let sc = pick(&["set_temp_c", "set_temp_f"], "°C");
            let sf = pick(&["set_temp_f", "set_temp_c"], "°F");
            let pair = |a: &Option<String>,
                        b: &Option<String>,
                        othera: &Option<String>,
                        otherb: &Option<String>| {
                (a.is_some() && b.is_some())
                    || (a.is_some() && otherb.is_none())
                    || (b.is_some() && othera.is_none())
            };
            let (cur, set, tunit) = if system == "°F" && pair(&cf, &sf, &cc, &sc) {
                (cf, sf, "°F")
            } else if pair(&cc, &sc, &cf, &sf) {
                (cc, sc, "°C")
            } else {
                let (ck, sk) = if system == "°F" {
                    (["cur_temp_f", "cur_temp_c"], ["set_temp_f", "set_temp_c"])
                } else {
                    (["cur_temp_c", "cur_temp_f"], ["set_temp_c", "set_temp_f"])
                };
                (
                    ck.iter()
                        .find(|k| roles.contains_key(**k))
                        .map(|k| k.to_string()),
                    sk.iter()
                        .find(|k| roles.contains_key(**k))
                        .map(|k| k.to_string()),
                    system,
                )
            };
            config["cur_role"] = json!(cur);
            config["set_role"] = json!(set);
            config["cur_unit"] = json!(cur.as_ref().and_then(|k| roles.get(k)).map(unit_for));
            config["set_unit"] = json!(set.as_ref().and_then(|k| roles.get(k)).map(unit_for));
            config["switch_only"] = desc["switch_only_hvac_mode"].clone();
            id["temperature_unit"] = json!(tunit);
            id["target_temperature_step"] = json!(1.0);
            let mut f = 0;
            if let Some(r) = set.as_ref().and_then(|k| roles.get(k)) {
                id["min_temp"] = number(r.lo() / r.scale());
                id["max_temp"] = number(r.hi() / r.scale());
                id["target_temperature_step"] = number(n(&r.spec["step"]) / r.scale());
                f |= 1;
            }
            let mut filt = json!({});
            let mut presets = vec![];
            let mut hvac = vec![];
            if let Some(r) = roles.get("hvac_mode") {
                let rng = r.range();
                for t in &rng {
                    let m = rules["maps"]["_MODE_TO_HVAC"][s(t)].clone();
                    let count = rng
                        .iter()
                        .filter(|x| rules["maps"]["_MODE_TO_HVAC"][s(x)] == m)
                        .count();
                    filt[s(t)] = if !m.is_null() && count == 1 {
                        m
                    } else {
                        V::Null
                    };
                }
                hvac.push(json!("off"));
                for (t, v) in obj(&filt) {
                    if v.is_null() {
                        presets.push(json!(t))
                    } else if v != "off" {
                        hvac.push(v)
                    }
                }
            } else if roles.contains_key("switch") {
                hvac = vec![json!("off"), config["switch_only"].clone()]
            };
            if !presets.is_empty() {
                f |= 16;
                id["preset_modes"] = json!(presets);
                if !hvac.contains(&config["switch_only"]) {
                    hvac.push(config["switch_only"].clone());
                }
            }
            id["hvac_modes"] = json!(hvac);
            config["hvac_filtered"] = filt;
            if let Some(r) = roles.get("set_hum") {
                f |= 4;
                id["min_humidity"] = number((r.lo() / r.scale()).round_ties_even());
                id["max_humidity"] = number((r.hi() / r.scale()).round_ties_even());
            }
            if let Some(r) = roles.get("fan") {
                f |= 8;
                id["fan_modes"] = json!(r.range());
            }
            if ["swing_on_off", "swing_h", "swing_v"]
                .iter()
                .any(|k| roles.contains_key(*k))
            {
                f |= 32;
                let mut modes = vec![json!("off")];
                for (k, m) in [
                    ("swing_on_off", "on"),
                    ("swing_h", "horizontal"),
                    ("swing_v", "vertical"),
                ] {
                    if roles.contains_key(k) {
                        modes.push(json!(m));
                    }
                }
                id["swing_modes"] = json!(modes);
            }
            if roles.contains_key("switch") {
                f |= 384
            }
            id["supported_features"] = json!(f);
            all = true;
        }
        "light" => {
            let Some(r) = find(vec![key.clone()], "Boolean", true)? else {
                return Ok(None);
            };
            roles.insert("switch".into(), r);
            for (k, field, kind) in [
                ("brightness", "brightness", "Integer"),
                ("color_mode", "color_mode", "Enum"),
                ("color_temp", "color_temp", "Integer"),
            ] {
                if let Some(r) = opt(field, kind, true)? {
                    roles.insert(k.into(), r);
                }
            }
            if roles.contains_key("brightness") {
                for k in ["brightness_min", "brightness_max"] {
                    if let Some(r) = opt(k, "Integer", true)? {
                        roles.insert(k.into(), r);
                    }
                }
            }
            let cd = opt("color_data", "Json", true)?.or(opt("color_data", "String", true)?);
            let mut hsv = V::Null;
            if let Some(r) = cd {
                if r.kind == "Json" && truth(&r.spec) {
                    let mut ranges = vec![];
                    for (k, max, target) in [("h", 360, 360), ("s", 255, 100), ("v", 255, 255)] {
                        ranges.push(json!([
                            r.spec[k].get("min").cloned().unwrap_or(json!(0)),
                            r.spec[k].get("max").cloned().unwrap_or(json!(max)),
                            0,
                            target
                        ]));
                    }
                    hsv = json!(ranges)
                }
                if hsv.is_null() {
                    let big = roles
                        .get("brightness")
                        .is_some_and(|r| r.hi() / r.scale() > 255.0);
                    hsv =
                        rules["constants"][if s(&desc["fallback_color_data_mode"]).ends_with("V2")
                            || r.code == "colour_data_v2"
                            || big
                        {
                            "hsv_v2"
                        } else {
                            "hsv_v1"
                        }]
                        .clone();
                }
                roles.insert("color_data".into(), r);
            }
            let mut modes = vec![json!("onoff")];
            if roles.contains_key("brightness") {
                modes.push(json!("brightness"));
            }
            if roles.contains_key("color_data") {
                modes.push(json!("hs"));
            }
            let mut wm = "color_temp";
            if roles.contains_key("color_temp") {
                modes.push(json!("color_temp"));
            } else if roles.contains_key("color_data")
                && roles
                    .get("color_mode")
                    .is_some_and(|r| r.range().contains(&json!("white")))
            {
                modes.push(json!("white"));
                wm = "white"
            }
            if modes.len() > 1 {
                modes.retain(|x| *x != "onoff");
            }
            if modes.len() > 1 {
                modes.retain(|x| *x != "brightness");
            }
            modes.sort_by(|a, b| s(a).cmp(s(b)));
            id["supported_color_modes"] = json!(modes);
            id["min_color_temp_kelvin"] = rules["constants"]["min_kelvin"].clone();
            id["max_color_temp_kelvin"] = rules["constants"]["max_kelvin"].clone();
            config = json!({"hsv":hsv,"white_mode":wm,"min_kelvin":id["min_color_temp_kelvin"],"max_kelvin":id["max_color_temp_kelvin"]});
            all = true;
        }
        _ => return Ok(None),
    }
    if rules["role_order"].get(p).is_some() {
        let mut ordered = Roles::new();
        for k in codes(&rules["role_order"][p]) {
            if let Some(r) = roles.shift_remove(&k) {
                ordered.insert(k, r);
            }
        }
        ordered.extend(roles);
        roles = ordered;
    }
    if deps.is_empty() {
        for r in roles.values() {
            if !deps.contains(&r.code) {
                deps.push(r.code.clone())
            }
        }
    }
    Ok(Some(Plan {
        platform: p.into(),
        key,
        identity: id,
        roles,
        depends_on: deps,
        slot_kind: slot,
        update_all: all,
        config,
    }))
}
fn json_vec(xs: &[&str]) -> Vec<V> {
    xs.iter().map(|x| json!(x)).collect()
}
