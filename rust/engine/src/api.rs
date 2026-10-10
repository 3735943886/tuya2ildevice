use crate::{adapter::*, classify, data::*, driver::Driver, driver::check, plan::Plan};
use serde_json::{Value as V, json};
use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};
static PACKS: OnceLock<Mutex<HashMap<String, Arc<V>>>> = OnceLock::new();
pub fn request(input: &V) -> Result<V, String> {
    if input["op"] == "load_rules" {
        return crate::rules::load(input);
    }
    if input["op"] == "merge_rules" {
        return crate::rules::compose(&input["base"], &arr(&input["layers"]));
    }
    let registry = PACKS.get_or_init(|| Mutex::new(HashMap::new()));
    if input["op"] == "install_rules" {
        let rules = input["rules"].clone();
        if rules["version"] != 1 {
            return Err("unsupported rule version".into());
        }
        let id = s(&input["rules_id"]).to_string();
        let mut packs = registry.lock().map_err(|_| "rule registry poisoned")?;
        if let Some(old) = packs.get(&id) {
            if **old != rules {
                return Err("rule id already registered with different content".into());
            }
            return Ok(json!(true));
        }
        crate::rules::validate(&rules)?;
        packs.insert(id, Arc::new(rules));
        return Ok(json!(true));
    }
    let cached = if input["rules"].is_null() {
        registry
            .lock()
            .map_err(|_| "rule registry poisoned")?
            .get(s(&input["rules_id"]))
            .cloned()
    } else {
        None
    };
    let rules = cached.as_deref().unwrap_or(&input["rules"]);
    match s(&input["op"]) {
        "create" => {
            let d = Driver::new(&input["device"], &input["options"], rules)?;
            let mut warnings = vec![];
            for (code, r) in obj(&d.block["remap"]) {
                if truth(&r["invert"])
                    && d.covers.iter().any(|c| {
                        [&c.cur, &c.setp, &c.ins]
                            .iter()
                            .any(|r| r.as_ref().is_some_and(|r| r.code == code))
                    })
                {
                    warnings.push(format!("remap.{code}.invert on a cover is deprecated: move it to the cover settings"));
                }
            }
            if d.block["converters"].get("cover_motion").is_some() {
                warnings.push(
                    "converters.cover_motion is deprecated: move it to the cover settings".into(),
                );
            }
            Ok(json!({"driver":d,"warnings":warnings}))
        }
        "handle" => {
            let mut d: Driver =
                serde_json::from_value(input["driver"].clone()).map_err(|e| e.to_string())?;
            let result = d.handle(n(&input["now"]), &input["input"], rules)?;
            Ok(json!({"driver":d,"outputs":result["outputs"],"callbacks":result["callbacks"]}))
        }
        "converted" => {
            let mut d: Driver =
                serde_json::from_value(input["driver"].clone()).map_err(|e| e.to_string())?;
            let mut out = vec![];
            d.converted(n(&input["index"]) as usize, &input["result"], &mut out);
            Ok(json!({"driver":d,"outputs":out}))
        }
        "write_callback" => {
            let d: Driver =
                serde_json::from_value(input["driver"].clone()).map_err(|e| e.to_string())?;
            let commands = if input["commands"].is_object() {
                obj(&input["commands"])
                    .iter()
                    .map(|(code, v)| json!({"code":code,"value":v}))
                    .collect()
            } else {
                arr(&input["commands"])
            };
            let output = match d.adapter.write(&commands) {
                Ok(dps) => json!({"type":"send_message","channel":"set","json":{"dps":dps}}),
                Err(e) => {
                    json!({"type":"reject","prop":input["prop"],"code":if e.starts_with("unsupported:"){"unsupported"}else{"invalid_value"},"reason":e})
                }
            };
            Ok(json!({"outputs":[output]}))
        }
        "carry" => {
            let mut d: Driver =
                serde_json::from_value(input["driver"].clone()).map_err(|e| e.to_string())?;
            let old: Driver =
                serde_json::from_value(input["old"].clone()).map_err(|e| e.to_string())?;
            d.seq = d.seq.max(old.seq);
            for (name, b) in d.assembly.bindings.iter_mut() {
                if let Some(ob) = old.assembly.bindings.get(name) {
                    let a = d.plans.get(b.plan);
                    let o = old.plans.get(ob.plan);
                    if a.is_some_and(|p| p.slot_kind.as_deref() == Some("delta"))
                        && o.is_some_and(|p| p.slot_kind.as_deref() == Some("delta"))
                        && a.unwrap().depends_on == o.unwrap().depends_on
                    {
                        b.total = ob.total;
                        b.last_ts = ob.last_ts.clone();
                    }
                }
            }
            Ok(json!({"driver":d}))
        }
        "settings" => {
            let d: Driver =
                serde_json::from_value(input["driver"].clone()).map_err(|e| e.to_string())?;
            Ok(d.settings_block())
        }
        "on_update" => {
            let p: Plan =
                serde_json::from_value(input["plan"].clone()).map_err(|e| e.to_string())?;
            let mut slot = input["slot"].clone();
            let changed = &input["changed"];
            let mut write = false;
            let mut fire = V::Null;
            if changed.is_null() {
                write = true
            } else if p.slot_kind.as_deref() == Some("event") {
                if p.depends_on
                    .first()
                    .is_some_and(|c| codes(changed).contains(c))
                {
                    fire = p.read(&input["status"], 0.0, rules)["event"].clone();
                    write = truth(&fire);
                }
            } else if p.slot_kind.as_deref() == Some("delta") {
                let code = &p.depends_on[0];
                let ts = &input["timestamps"][code];
                let raw = &input["status"][code];
                if codes(changed).contains(code)
                    && !ts.is_null()
                    && *ts != slot["last_ts"]
                    && !raw.is_null()
                {
                    slot["total"] = json!(n(&slot["total"]) + n(raw));
                    slot["last_ts"] = ts.clone();
                    write = true;
                }
            } else {
                write = p.update_all || p.depends_on.iter().any(|c| codes(changed).contains(c));
            }
            Ok(json!({"slot":slot,"write_state":write,"fire":fire}))
        }
        "resolve" => Ok(json!(resolve(
            &input["device"],
            &codes(&input["reference"]["candidates"]),
            s(&input["kind"]),
            input["reference"]["source"] == "function_first"
        )?)),
        "merge" => {
            let mut value = json!({});
            for mapping in arr(&input["mappings"]) {
                merge(&mut value, &mapping);
            }
            Ok(value)
        }
        "override_validate" => {
            crate::driver::validate(&input["block"], rules)?;
            let known = codes(&input["converter_types"]);
            for name in obj(&input["block"]["converters"]).keys() {
                if !["cover_motion", "declarative"].contains(&name.as_str())
                    && !known.contains(name)
                {
                    return Err(format!("override: unknown converter {name}"));
                }
            }
            Ok(input["block"].clone())
        }
        "unused" => {
            let a = Adapter {
                entries: serde_json::from_value(input["entries"].clone())
                    .map_err(|e| e.to_string())?,
                ..Default::default()
            };
            let mut plans = vec![];
            let consumed = codes(&input["consumed"]);
            let a = Adapter {
                entries: a
                    .entries
                    .into_iter()
                    .filter(|(_, e)| !consumed.contains(&e.0))
                    .collect(),
                ..a
            };
            crate::driver::unused(&input["device"], &a, &mut plans)?;
            Ok(json!(plans))
        }
        "assemble" => {
            let mut plans: Vec<Plan> =
                serde_json::from_value(input["plans"].clone()).map_err(|e| e.to_string())?;
            Ok(json!(crate::assembly::assemble(
                &input["device"],
                &mut plans,
                &input["info"],
                truth(&input["allow_hazardous"]),
                rules
            )))
        }
        "binding_read" => {
            let binding: crate::assembly::Binding =
                serde_json::from_value(input["binding"].clone()).map_err(|e| e.to_string())?;
            let plan: Plan =
                serde_json::from_value(input["plan"].clone()).map_err(|e| e.to_string())?;
            Ok(binding.read(&plan, &input["status"], rules, &input["definition"]))
        }
        "binding_write" => {
            let binding: crate::assembly::Binding =
                serde_json::from_value(input["binding"].clone()).map_err(|e| e.to_string())?;
            let plan: Plan =
                serde_json::from_value(input["plan"].clone()).map_err(|e| e.to_string())?;
            Ok(json!(binding.write(
                &plan,
                &input["value"],
                &input["status"],
                rules
            )?))
        }
        "motion_create" => {
            let mut cfg = rules["motion_defaults"].clone();
            for k in obj(&input["config"]).keys() {
                if cfg.get(k).is_none() {
                    return Err(format!("unknown cover_motion config {k}"));
                }
            }
            merge(&mut cfg, &input["config"]);
            let prop = input.get("prop").cloned().unwrap_or(json!("cover_state"));
            let m = crate::driver::Motion {
                prop: s(&prop).into(),
                group: input["group"].as_str().map(String::from),
                command: cfg["command"].as_str().map(String::from),
                target_code: cfg["set_position"].as_str().map(String::from),
                position: cfg["position"].as_str().map(String::from),
                words: cfg["words"].clone(),
                invert: truth(&cfg["invert"]),
                settle: n(&cfg["settle"]),
                mode: input.get("mode").map(s).unwrap_or("inferred").into(),
                invert_report: truth(&input["invert_report"]),
                state: None,
                target: None,
                by_word: false,
                last: None,
                external: false,
            };
            let mut def = rules["motion_property"].clone();
            if input["group"].is_string() {
                def["group"] = input["group"].clone();
            }
            Ok(json!({"motion":m,"props":{s(&prop):def}}))
        }
        "motion_update" => {
            let mut m: crate::driver::Motion =
                serde_json::from_value(input["motion"].clone()).map_err(|e| e.to_string())?;
            let (values, timer) = m.update(
                &input["codes"],
                &codes(&input["changed"]),
                truth(&input["active"]),
                truth(&input["timer"]),
            );
            Ok(
                json!({"motion":m,"result":{"values":values,"timers":if let Some(t)=timer{json!({"settle":t})}else{json!({})}}}),
            )
        }
        "motion_reset" => {
            let mut m: crate::driver::Motion =
                serde_json::from_value(input["motion"].clone()).map_err(|e| e.to_string())?;
            m.reset();
            Ok(json!(m))
        }
        "quirk_schema" => {
            let mut d = input["device"].clone();
            for t in ["function", "status_range", "status"] {
                if !d[t].is_object() {
                    d[t] = json!({});
                }
            }
            let mut dpmap = d.get("dpmap").cloned().unwrap_or(json!({}));
            let quirk = input
                .get("quirk")
                .filter(|v| !v.is_null())
                .cloned()
                .unwrap_or_else(|| rules["quirks"][s(&d["product_id"])].clone());
            for op in arr(&quirk["ops"]) {
                if op.get("when").is_some() && !truth(&cond(&op["when"], &d["status"])) {
                    continue;
                }
                patch_op(&mut d, &mut dpmap, &op);
            }
            d["dpmap"] = dpmap;
            Ok(d)
        }
        "status_quirk" => {
            let mut st = input["status"].clone();
            for op in arr(&input["quirk"]["ops"]) {
                if op["op"] == "MapInitialStatus" {
                    let code = s(&op["code"]);
                    for pair in arr(&op["mapping"]) {
                        if eq(&st[code], &pair[0]) {
                            st[code] = pair[1].clone();
                            break;
                        }
                    }
                }
            }
            Ok(st)
        }
        "device_info" => {
            let quirk = input
                .get("quirk")
                .filter(|v| !v.is_null())
                .cloned()
                .unwrap_or_else(|| rules["quirks"][s(&input["device"]["product_id"])].clone());
            let meta = &quirk["meta"];
            Ok(if truth(&meta["manufacturer"]) {
                json!({"manufacturer":meta["manufacturer"],"model":meta["model"],"model_id":meta["model_id"]})
            } else {
                json!({"manufacturer":"Tuya","model":input["device"]["product_name"],"model_id":input["device"]["product_id"]})
            })
        }
        "unit_policy" => {
            let mut id = json!({});
            let desc = json!({"native_unit_of_measurement":input["fallback_unit"],"suggested_unit_of_measurement":input["suggested_unit"]});
            classify::unit(
                s(&input["platform"]),
                &json!({"status":{"temp_unit_convert":input["temp_unit_convert"]}}),
                &json!({"allowed_units":input["allowed_units"]}),
                &desc,
                &mut id,
                input["device_class"].clone(),
                input["dp_unit"].clone(),
                input["suggested_unit"].clone(),
                rules,
            );
            Ok(id)
        }
        "codec" => {
            let v = &input["value"];
            match s(&input["name"]) {
                "json_loads" => Ok(if v.is_string() { parse(v) } else { V::Null }),
                "b64_decode" => Ok(json!(b64(v))),
                "electricity_bytes" => Ok(electricity(
                    &arr(v).iter().map(|b| n(b) as u8).collect::<Vec<_>>(),
                )),
                "electricity_hex" => Ok(hexbytes(s(v)).map(|b| electricity(&b)).unwrap_or(V::Null)),
                "hsv_hex_decode" => {
                    let text = s(v);
                    Ok(if text.len() == 12 {
                        hexbytes(text)
                            .map(|b| json!([uint(&b[..2]), uint(&b[2..4]), uint(&b[4..])]))
                            .unwrap_or(V::Null)
                    } else {
                        V::Null
                    })
                }
                _ => Err("unknown codec".into()),
            }
        }
        "value_op" => {
            let name = s(&input["name"]);
            let spec = input["spec"].clone();
            let value = &input["value"];
            let r = Role {
                code: "value".into(),
                kind: if name.contains("bool") {
                    "Boolean"
                } else if name.contains("enum") {
                    "Enum"
                } else {
                    "Integer"
                }
                .into(),
                spec,
                report_type: V::Null,
            };
            let st = json!({"value":value});
            match name {
                "validate_bool_read" | "validate_enum_read" | "validate_int_read" => {
                    Ok(r.read(&st))
                }
                "validate_bool_write" | "validate_enum_write" | "validate_int_write" => {
                    r.write(value)
                }
                "scale_value" => Ok(json!(n(value) / r.scale())),
                "scaled_range" => Ok(json!([r.lo() / r.scale(), r.hi() / r.scale()])),
                "remap" => {
                    let mut v = n(value);
                    let range = &input["range"];
                    if input["reverse"] == true {
                        v = n(&range[1]) - v + n(&range[0]);
                    }
                    Ok(json!(remap(
                        v,
                        n(&range[0]),
                        n(&range[1]),
                        n(&range[2]),
                        n(&range[3])
                    )))
                }
                "remap_read" => {
                    let v = r.read(&st);
                    if v.is_null() {
                        return Ok(V::Null);
                    }
                    let mut v = n(&v);
                    if input["reverse"] == true {
                        v = r.hi() / r.scale() - v + r.lo() / r.scale();
                    }
                    Ok(number(
                        remap(
                            v,
                            r.lo() / r.scale(),
                            r.hi() / r.scale(),
                            n(&input["target"][0]),
                            n(&input["target"][1]),
                        )
                        .round_ties_even(),
                    ))
                }
                "remap_write" => {
                    let lo = n(&input["target"][0]);
                    let hi = n(&input["target"][1]);
                    let mut v = n(value);
                    if input["reverse"] == true {
                        v = hi - v + lo;
                    }
                    r.write(&number(remap(
                        v,
                        lo,
                        hi,
                        r.lo() / r.scale(),
                        r.hi() / r.scale(),
                    )))
                }
                _ => Err("unknown value op".into()),
            }
        }
        "build" => Ok(json!(classify::build(
            s(&input["platform"]),
            &input["device"],
            &input["env"],
            &input["description"],
            rules
        )?)),
        "classify" => {
            let plans =
                classify::classify(&input["device"], &input["env"], rules, &input["platforms"])?;
            Ok(json!(plans))
        }
        "read" => {
            let p: Plan =
                serde_json::from_value(input["plan"].clone()).map_err(|e| e.to_string())?;
            Ok(p.read(&input["status"], n(&input["total"]), rules))
        }
        "write" => {
            let p: Plan =
                serde_json::from_value(input["plan"].clone()).map_err(|e| e.to_string())?;
            Ok(json!(p.write(
                s(&input["action"]),
                &input["args"],
                &input["status"],
                rules
            )?))
        }
        "check" => match check(
            &input["descriptor"],
            &input["state"],
            s(&input["prop"]),
            &input["value"],
        ) {
            Ok(v) => Ok(json!({"accept":v})),
            Err((code, reason)) => Ok(json!({"reject":code,"reason":reason})),
        },
        "adapter_create" => Ok(json!(Adapter::new(
            &input["device"],
            &input["dpmap"],
            rules
        ))),
        "adapter_read" => {
            let a: Adapter =
                serde_json::from_value(input["adapter"].clone()).map_err(|e| e.to_string())?;
            Ok(a.read(&input["dps"], rules))
        }
        "adapter_write" => {
            let a: Adapter =
                serde_json::from_value(input["adapter"].clone()).map_err(|e| e.to_string())?;
            let mut present = vec![];
            let mut missing = vec![];
            for c in arr(&input["commands"]) {
                if a.entries.values().any(|e| e.0 == s(&c["code"])) {
                    present.push(c)
                } else {
                    missing.push(c["code"].clone());
                }
            }
            let dps = if present.is_empty() {
                json!({})
            } else {
                a.write(&present)?
            };
            Ok(json!([dps, missing]))
        }
        "strategy_code" => Ok(json!(strategy_code(&input["meta"]))),
        "remap" => {
            let code = "value";
            let a = Adapter {
                remaps: json!({code:input["remap"]}),
                ..Default::default()
            };
            let mut v = input["value"].clone();
            let alias = &input["remap"]["alias"];
            if input["direction"] == "read" {
                if v.is_string() {
                    v = alias.get(s(&v)).cloned().unwrap_or(v);
                }
                v = a.flip(code, &v);
            } else {
                v = a.flip(code, &v);
                if let Some((dev, _)) = obj(alias).iter().find(|(_, std)| **std == v) {
                    v = json!(dev);
                }
            }
            Ok(v)
        }
        "strategy_read" => {
            strategy_read(s(&input["name"]), &input["value"], &input["config"], rules)
        }
        "strategy_write" => strategy_write(s(&input["name"]), &input["value"], &input["config"]),
        _ => Err("unknown engine operation".into()),
    }
}
