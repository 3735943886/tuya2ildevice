use crate::adapter::*;
use crate::data::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value as V, json};
#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Plan {
    pub platform: String,
    pub key: String,
    pub identity: V,
    pub roles: Roles,
    pub depends_on: Vec<String>,
    pub slot_kind: Option<String>,
    pub update_all: bool,
    pub config: V,
}
impl Plan {
    pub fn role(&self, k: &str) -> Option<&Role> {
        self.roles.get(k)
    }
    pub fn get(&self, k: &str, st: &V) -> V {
        self.role(k).map(|r| r.read(st)).unwrap_or(V::Null)
    }
    pub fn round(&self, k: &str, st: &V) -> V {
        let v = self.get(k, st);
        if v.is_null() {
            v
        } else {
            number(n(&v).round_ties_even())
        }
    }
    pub fn cmd(&self, k: &str, v: &V) -> Result<V, String> {
        let r = self
            .role(k)
            .ok_or_else(|| format!("unsupported: {k} not available"))?;
        Ok(json!({"code":r.code,"value":r.write(v)?}))
    }
    pub fn read(&self, st: &V, total: f64, rules: &V) -> V {
        let main = self.get("main", st);
        match self.platform.as_str() {
            "switch" | "binary_sensor" | "siren" => {
                let v = match s(&self.config["read"]) {
                    "bitmap" => {
                        let raw = &st[s(&self.config["code"])];
                        if raw.is_null() {
                            V::Null
                        } else {
                            json!(n(raw) as i64 & (1 << n(&self.config["bit"]) as i64) != 0)
                        }
                    }
                    "set" => {
                        let raw = &st[s(&self.config["code"])];
                        if raw.is_null() {
                            V::Null
                        } else {
                            json!(items(&self.config["on"]).iter().any(|v| eq(v, raw)))
                        }
                    }
                    _ => main,
                };
                json!({"is_on":v})
            }
            "button" => json!({}),
            "select" => json!({"current_option":main,"options":self.role("main").unwrap().range()}),
            "number" => json!({"native_value":main}),
            "sensor" => {
                let v = match s(&self.config["read"]) {
                    "delta" => number(total / self.role("main").unwrap().scale()),
                    "wind" => {
                        rules["wind"][s(&st[self.role("main").unwrap().code.as_str()])].clone()
                    }
                    "electricity" => {
                        let raw = &st[self.role("main").unwrap().code.as_str()];
                        match s(&self.config["form"]) {
                            "Json" => parse(raw)[s(&self.config["attribute"])].clone(),
                            "Raw" => b64(raw)
                                .map(|b| electricity(&b)[s(&self.config["attribute"])].clone())
                                .unwrap_or(V::Null),
                            _ => hexbytes(s(raw))
                                .map(|b| electricity(&b)[s(&self.config["attribute"])].clone())
                                .unwrap_or(V::Null),
                        }
                    }
                    "text" => {
                        let raw = &st[self.role("main").unwrap().code.as_str()];
                        if raw.is_string() && !s(raw).is_empty() && s(raw).chars().count() <= 1024 {
                            raw.clone()
                        } else {
                            V::Null
                        }
                    }
                    "bitmap" => {
                        let raw = &st[self.role("main").unwrap().code.as_str()];
                        if raw.is_i64() || raw.is_boolean() {
                            raw.clone()
                        } else {
                            V::Null
                        }
                    }
                    _ => main,
                };
                json!({"native_value":v})
            }
            "valve" => json!({"is_closed":if main.is_null(){V::Null}else{json!(!truth(&main))}}),
            "event" => {
                let raw = &st[self.role("main").unwrap().code.as_str()];
                json!({"event":if raw.is_null(){V::Null}else if self.config["simple"]==true{json!([raw,null])}else{json!(["triggered",{"message":b64(raw).and_then(|b|String::from_utf8(b).ok())}])}})
            }
            "camera" => {
                json!({"is_recording":truth(&self.get("record_switch",st)),"motion_detection_enabled":truth(&self.get("motion_switch",st))})
            }
            "cover" => {
                let pos = self
                    .role("current_position")
                    .or(self.role("set_position"))
                    .map(|r| r.pct(st, 0.0, 100.0))
                    .unwrap_or(V::Null);
                let closed = if !pos.is_null() {
                    json!(n(&pos) == 0.0)
                } else if let Some(r) = self.role("current_state") {
                    let v = r.read(st);
                    if v.is_null() {
                        V::Null
                    } else if r.kind == "Boolean" {
                        json!(!truth(&v))
                    } else {
                        rules["maps"]["_CLOSED_ENUM"][s(&v)].clone()
                    }
                } else {
                    V::Null
                };
                json!({"current_position":pos,"current_tilt_position":self.role("tilt").map(|r|r.pct(st,0.0,100.0)).unwrap_or(V::Null),"is_closed":closed})
            }
            "fan" => {
                let speed = if let Some(r) = self.role("speed") {
                    if r.kind == "Integer" {
                        r.pct(st, 1.0, 100.0)
                    } else {
                        let v = r.read(st);
                        let opts = r.range();
                        opts.iter()
                            .position(|x| *x == v)
                            .map(|i| json!((i + 1) * 100 / opts.len()))
                            .unwrap_or(V::Null)
                    }
                } else {
                    V::Null
                };
                let dir = self.get("direction", st);
                json!({"is_on":self.get("switch",st),"percentage":speed,"direction":if dir=="forward"||dir=="reverse"{dir}else{V::Null},"oscillating":self.get("oscillate",st),"preset_mode":self.get("mode",st)})
            }
            "humidifier" => {
                json!({"is_on":self.get("switch",st),"mode":self.get("mode",st),"target_humidity":self.round("target_humidity",st),"current_humidity":self.round("current_humidity",st)})
            }
            "alarm_control_panel" => {
                let enc = &st["alarm_msg"];
                let decoded = b64(enc).and_then(|b| {
                    if b.len() % 2 != 0 {
                        return None;
                    }
                    String::from_utf16(
                        &b.as_chunks::<2>()
                            .0
                            .iter()
                            .map(|c| u16::from_be_bytes([c[0], c[1]]))
                            .collect::<Vec<_>>(),
                    )
                    .ok()
                });
                let v = self.get("master_mode", st);
                let triggered = st["master_state"] == "alarm"
                    && !(truth(enc)
                        && decoded.as_ref().is_some_and(|x| {
                            !x.is_empty()
                                && items(&rules["constants"]["alarm_non_triggering_messages"])
                                    .iter()
                                    .any(|message| {
                                        message.as_str().is_some_and(|message| x.contains(message))
                                    })
                        }));
                json!({"alarm_state":if triggered{json!("triggered")}else{rules["maps"]["_ALARM_STATE"][s(&v)].clone()},"changed_by":if self.role("alarm_msg").is_some()&&st["master_state"]=="alarm"{json!(decoded)}else{V::Null}})
            }
            "vacuum" => {
                let status = self.get("status", st);
                let activity = if !status.is_null() {
                    rules["maps"]["_VAC"][s(&status)].clone()
                } else if truth(&self.get("pause", st)) {
                    json!("paused")
                } else {
                    V::Null
                };
                json!({"activity":activity,"fan_speed":self.get("suction",st)})
            }
            "climate" => {
                let sw = self.get("switch", st);
                let raw = self.get("hvac_mode", st);
                let hvac = if sw == false {
                    json!("off")
                } else if self.role("hvac_mode").is_none() {
                    if sw == true {
                        self.config["switch_only"].clone()
                    } else {
                        V::Null
                    }
                } else {
                    rules["maps"]["_MODE_TO_HVAC"][s(&raw)].clone()
                };
                let pr = if items(&self.identity["preset_modes"]).contains(&raw) {
                    raw
                } else {
                    V::Null
                };
                let swing = if self.role("swing_on_off").is_some()
                    || self.role("swing_h").is_some()
                    || self.role("swing_v").is_some()
                {
                    if truth(&self.get("swing_on_off", st)) {
                        json!("on")
                    } else {
                        let h = truth(&self.get("swing_h", st));
                        let v = truth(&self.get("swing_v", st));
                        json!(if h && v {
                            "both"
                        } else if h {
                            "horizontal"
                        } else if v {
                            "vertical"
                        } else {
                            "off"
                        })
                    }
                } else {
                    V::Null
                };
                json!({"hvac_mode":hvac,"preset_mode":pr,"current_temperature":self.temp("cur",st),"temperature":self.temp("set",st),"current_humidity":self.round("cur_hum",st),"target_humidity":self.round("set_hum",st),"fan_mode":self.get("fan",st),"swing_mode":swing})
            }
            "light" => self.light_read(st),
            _ => json!({}),
        }
    }
    fn temp(&self, k: &str, st: &V) -> V {
        let role = s(&self.config[format!("{k}_role")]);
        let v = self.get(role, st);
        let unit = s(&self.config[format!("{k}_unit")]);
        if v.is_null() || unit.is_empty() || unit == s(&self.identity["temperature_unit"]) {
            v
        } else {
            number(temp_convert(
                n(&v),
                unit,
                s(&self.identity["temperature_unit"]),
            ))
        }
    }
    fn light_limits(&self, st: &V) -> Option<(f64, f64)> {
        let convert = |k| {
            let r = self.role(k)?;
            let v = r.read(st);
            if v.is_null() {
                None
            } else {
                Some(remap(
                    n(&v),
                    r.lo() / r.scale(),
                    r.hi() / r.scale(),
                    0.0,
                    255.0,
                ))
            }
        };
        Some((convert("brightness_min")?, convert("brightness_max")?))
    }
    fn light_mode(&self, st: &V) -> String {
        let opts = arr(&self.identity["supported_color_modes"]);
        if opts.len() == 1 {
            return s(&opts[0]).into();
        }
        if self.role("color_mode").is_some() && self.get("color_mode", st) != "white" {
            "hs".into()
        } else {
            s(&self.config["white_mode"]).into()
        }
    }
    fn light_hsv(&self, st: &V) -> Option<[f64; 3]> {
        let r = self.role("color_data")?;
        let raw = &st[&r.code];
        let trip = if r.kind == "Json" {
            let p = parse(raw);
            if ["h", "s", "v"].iter().any(|k| p[*k].is_null()) {
                return None;
            }
            vec![n(&p["h"]), n(&p["s"]), n(&p["v"])]
        } else {
            let t = raw.as_str()?;
            if t.len() != 12 || !t.is_ascii() {
                return None;
            }
            vec![
                (u64::from_str_radix(&t[..4], 16).ok()?) as f64,
                (u64::from_str_radix(&t[4..8], 16).ok()?) as f64,
                (u64::from_str_radix(&t[8..], 16).ok()?) as f64,
            ]
        };
        trip.iter()
            .zip(items(&self.config["hsv"]))
            .map(|(v, r)| remap(*v, n(&r[0]), n(&r[1]), n(&r[2]), n(&r[3])))
            .collect::<Vec<_>>()
            .try_into()
            .ok()
    }
    fn light_read(&self, st: &V) -> V {
        let mode = self.light_mode(st);
        let h = self.light_hsv(st);
        let b = if mode == "hs" && self.role("color_data").is_some() {
            h.as_ref()
                .map(|x| number(x[2].round_ties_even()))
                .unwrap_or(V::Null)
        } else if let Some(r) = self.role("brightness") {
            let v = r.read(st);
            if v.is_null() {
                V::Null
            } else {
                let mut b = remap(n(&v), r.lo() / r.scale(), r.hi() / r.scale(), 0.0, 255.0);
                if let Some((lo, hi)) = self.light_limits(st) {
                    b = remap(b, lo, hi, 0.0, 255.0);
                }
                number(b.round_ties_even())
            }
        } else {
            V::Null
        };
        let ct = if let Some(r) = self.role("color_temp") {
            let v = r.read(st);
            if v.is_null() {
                V::Null
            } else {
                let lo = n(&self.config["min_kelvin"]);
                let hi = n(&self.config["max_kelvin"]);
                let x = r.hi() / r.scale() - n(&v) + r.lo() / r.scale();
                number(
                    (1e6 / remap(
                        x,
                        r.lo() / r.scale(),
                        r.hi() / r.scale(),
                        1e6 / hi,
                        1e6 / lo,
                    ))
                    .round_ties_even(),
                )
            }
        } else {
            V::Null
        };
        json!({"is_on":self.get("switch",st),"brightness":b,"color_temp_kelvin":ct,"color_mode":mode,"hs_color":h.map(|x|vec![x[0],x[1]])})
    }
    pub fn write(&self, action: &str, args: &V, st: &V, rules: &V) -> Result<Vec<V>, String> {
        let one = |k: &str, v: &V| self.cmd(k, v).map(|x| vec![x]);
        match self.platform.as_str() {
            "switch" | "siren" => one("main", &json!(action == "turn_on")),
            "button" => one("main", &json!(true)),
            "select" => one("main", &args["option"]),
            "number" => one("main", &args["value"]),
            "valve" => one("main", &json!(action == "open")),
            "camera" => one("motion_switch", &json!(action == "enable_motion_detection")),
            "cover" => {
                let wp = |k: &str, v: &V| {
                    let r = self.role(k).ok_or("missing position")?;
                    Ok(vec![
                        json!({"code":r.code,"value":r.pct_write(v,0.0,100.0)?}),
                    ])
                };
                match action {
                    "set_cover_position" => wp("set_position", &args["position"]),
                    "set_cover_tilt_position" => wp("tilt", &args["tilt_position"]),
                    _ => {
                        let a = match action {
                            "open_cover" => "open",
                            "close_cover" => "close",
                            _ => "stop",
                        };
                        if a != "stop" && self.role("set_position").is_some() {
                            return wp("set_position", &json!(if a == "open" { 100 } else { 0 }));
                        }
                        if !items(&self.config["instruction_options"]).contains(&json!(a)) {
                            return Ok(vec![]);
                        }
                        if let Some(r) = self.role("instruction") {
                            let v = if r.kind == "Boolean" {
                                json!(a == "open")
                            } else {
                                self.config["instructions"][a].clone()
                            };
                            one("instruction", &v)
                        } else {
                            Ok(vec![])
                        }
                    }
                }
            }
            "fan" => {
                let speed = |v: &V| {
                    let r = self.role("speed").ok_or("missing speed")?;
                    let raw = if r.kind == "Integer" {
                        r.pct_write(v, 1.0, 100.0)?
                    } else {
                        let opts = r.range();
                        opts.iter()
                            .enumerate()
                            .find(|(i, _)| n(v) <= ((i + 1) * 100 / opts.len()) as f64)
                            .map(|(_, x)| x.clone())
                            .or_else(|| opts.last().cloned())
                            .ok_or("empty speed range")?
                    };
                    Ok::<V, String>(json!({"code":r.code,"value":raw}))
                };
                match action {
                    "turn_off" => one("switch", &json!(false)),
                    "turn_on" => {
                        if self.role("switch").is_none() {
                            return Ok(vec![]);
                        }
                        let mut c = one("switch", &json!(true))?;
                        if !args["percentage"].is_null() && self.role("speed").is_some() {
                            c.push(speed(&args["percentage"])?)
                        }
                        if !args["preset_mode"].is_null() && self.role("mode").is_some() {
                            c.push(self.cmd("mode", &args["preset_mode"])?)
                        }
                        Ok(c)
                    }
                    "set_percentage" => {
                        if args["percentage"] == 0 && self.role("switch").is_some() {
                            one("switch", &json!(false))
                        } else {
                            Ok(vec![speed(&args["percentage"])?])
                        }
                    }
                    "set_preset_mode" => one("mode", &args["preset_mode"]),
                    "oscillate" => one("oscillate", &args["oscillating"]),
                    _ => one("direction", &args["direction"]),
                }
            }
            "humidifier" => match action {
                "turn_on" | "turn_off" => one("switch", &json!(action == "turn_on")),
                "set_humidity" => one("target_humidity", &args["humidity"]),
                _ => one("mode", &args["mode"]),
            },
            "alarm_control_panel" => one("master_mode", &rules["maps"]["_ALARM_ACTION"][action]),
            "vacuum" => match action {
                "locate" => one("seek", &json!(true)),
                "pause" => one("pause", &json!(true)),
                "return_to_base" => {
                    if self.role("switch_charge").is_some() {
                        one("switch_charge", &json!(true))
                    } else {
                        one("mode", &rules["constants"]["vacuum_return_mode"])
                    }
                }
                "start" | "stop" => one("power_go", &json!(action == "start")),
                "set_fan_speed" => one("suction", &args["fan_speed"]),
                "send_command" => Ok(vec![
                    json!({"code":args["command"],"value":args["params"][0]}),
                ]),
                _ => Ok(vec![]),
            },
            "climate" => match action {
                "turn_on" | "turn_off" => one("switch", &json!(action == "turn_on")),
                "set_hvac_mode" => {
                    let hv = &args["hvac_mode"];
                    let mut out = vec![];
                    if self.role("switch").is_some() {
                        out.push(self.cmd("switch", &json!(*hv != "off"))?)
                    }
                    if let Some((raw, _)) =
                        fields(&self.config["hvac_filtered"]).find(|(_, v)| **v == *hv)
                    {
                        out.push(self.cmd("hvac_mode", &json!(raw))?)
                    }
                    Ok(out)
                }
                "set_preset_mode" => one("hvac_mode", &args["preset_mode"]),
                "set_fan_mode" => one("fan", &args["fan_mode"]),
                "set_humidity" => one("set_hum", &args["humidity"]),
                "set_temperature" => {
                    let mut v = args["temperature"].clone();
                    let unit = s(&self.config["set_unit"]);
                    if !unit.is_empty() && unit != s(&self.identity["temperature_unit"]) {
                        v = number(temp_convert(
                            n(&v),
                            s(&self.identity["temperature_unit"]),
                            unit,
                        ));
                    }
                    one(s(&self.config["set_role"]), &v)
                }
                "set_swing_mode" => {
                    let mode = s(&args["swing_mode"]);
                    let mut out = vec![];
                    for (k, on) in [
                        ("swing_on_off", mode == "on"),
                        ("swing_v", mode == "vertical" || mode == "both"),
                        ("swing_h", mode == "horizontal" || mode == "both"),
                    ] {
                        if self.role(k).is_some() {
                            out.push(self.cmd(k, &json!(on))?);
                        }
                    }
                    Ok(out)
                }
                _ => Err("unknown climate action".into()),
            },
            "light" => self.light_write(action, args, st),
            _ => Err("unsupported: no write path".into()),
        }
    }
    fn light_write(&self, action: &str, args: &V, st: &V) -> Result<Vec<V>, String> {
        if action == "turn_off" {
            return Ok(vec![self.cmd("switch", &json!(false))?]);
        }
        let mut cmds = vec![self.cmd("switch", &json!(true))?];
        let white = args.get("white").is_some() || args.get("color_temp_kelvin").is_some();
        if self.role("color_mode").is_some() && white {
            cmds.push(self.cmd("color_mode", &json!("white"))?)
        }
        if let Some(r) = self.role("color_temp")
            && args.get("color_temp_kelvin").is_some()
        {
            let lo = n(&self.config["min_kelvin"]);
            let hi = n(&self.config["max_kelvin"]);
            let x = 1e6 / lo - 1e6 / n(&args["color_temp_kelvin"]) + 1e6 / hi;
            let v = remap(
                x,
                1e6 / hi,
                1e6 / lo,
                r.lo() / r.scale(),
                r.hi() / r.scale(),
            );
            cmds.push(self.cmd("color_temp", &number(v))?);
        }
        let cur = self.light_read(st);
        if let Some(color_role) = self.role("color_data")
            && (args.get("hs_color").is_some()
                || (args.get("brightness").is_some() && cur["color_mode"] == "hs" && !white))
        {
            if self.role("color_mode").is_some() {
                cmds.push(self.cmd("color_mode", &json!("colour"))?)
            }
            let b = if truth(&args["brightness"]) {
                n(&args["brightness"])
            } else {
                n(&cur["brightness"])
            };
            let c = if truth(&args["hs_color"]) {
                &args["hs_color"]
            } else {
                &cur["hs_color"]
            };
            let raw: [i64; 3] = [n(&c[0]), n(&c[1]), b]
                .iter()
                .zip(items(&self.config["hsv"]))
                .map(|(x, r)| {
                    remap(*x, n(&r[2]), n(&r[3]), n(&r[0]), n(&r[1])).round_ties_even() as i64
                })
                .collect::<Vec<_>>()
                .try_into()
                .map_err(|_| "invalid HSV ranges")?;
            let v = if color_role.kind == "Json" {
                json!(format!(
                    "{{\"h\": {}, \"s\": {}, \"v\": {}}}",
                    raw[0], raw[1], raw[2]
                ))
            } else {
                json!(format!("{:04x}{:04x}{:04x}", raw[0], raw[1], raw[2]))
            };
            cmds.push(self.cmd("color_data", &v)?);
        } else if let Some(r) = self.role("brightness")
            && (args.get("brightness").is_some() || args.get("white").is_some())
        {
            let mut v = n(if args.get("brightness").is_some() {
                &args["brightness"]
            } else {
                &args["white"]
            });
            if let Some((lo, hi)) = self.light_limits(st) {
                v = remap(v, 0.0, 255.0, lo, hi)
            }
            cmds.push(self.cmd(
                "brightness",
                &number(remap(v, 0.0, 255.0, r.lo() / r.scale(), r.hi() / r.scale())),
            )?);
        }
        Ok(cmds)
    }
}
pub fn temp_convert(v: f64, from: &str, to: &str) -> f64 {
    if from == to {
        v
    } else if from == "°C" && to == "°F" {
        v * 1.8 + 32.0
    } else {
        (v - 32.0) / 1.8
    }
}
