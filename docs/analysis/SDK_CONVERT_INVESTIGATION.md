# SDK value_convert investigation

SDK root: `/home/ubuntu/.cache/uv/archive-v0/EaUfhfmZ1Wvf3b_U/tuya_sharing/` (call it SDK/). All 24 strategies were run in a python harness (paho/requests/Crypto stubbed) to confirm outputs.

## 1. What the SDK does

### Where conversion is applied
- `device.py:153-190` `update_device_strategy_info` calls `GET /v1.0/m/life/devices/{id}/status`. It builds `local_strategy[dpId] = {value_convert, status_code, config_item{statusFormat, valueDesc, valueType, enumMappingMap, pid}}`.
  - If any dp has `supportLocal == false`, or the custom-type flag is on, then `support_local=False` and `local_strategy` stays empty.
- The ONLY consumer is `manager.py:_on_device_report` (manager.py:158-215), the MQTT (cloud push) handler. Two branches:
  - `device.support_local` true: MQ items look like `{'dpId': 1, 't': ..., 'value': 120}`. For each one it calls `strategy.convert(value_convert, (status_code, value), config_item)` (manager.py:190) and stores the result as `device.status[code] = value`.
  - `support_local` false: MQ items already carry `{code, value}`, and the value is stored as-is.
- So the cloud pushes RAW device dp values keyed by dpId (the same forms a LAN device speaks), and the SDK converts them to the "cloud code value" clients see. The initial `device.status` snapshot (device.py:121-129) comes from the cloud REST API and is already in cloud form, with no conversion.
- Details of `_on_device_report`:
  - The output `code` is NOT the `status_code` field. Every strategy returns `status_key` from `json.loads(config_item["statusFormat"]).popitem()`, the last key of that JSON dict.
  - `status_code` and the statusFormat key differ in real data: `humidity_value` vs `va_humidity` (wsdcg dp2, 2 in tuyadevices.json plus 2 core fixtures) and `clear_energy` vs `energy_reset` (dlq dp12).
  - Enum post-check (manager.py:194-203): if `status_range[code].type == "Enum"` and the converted value is not in `range`, the update is DROPPED.
  - Unknown dpId: skipped.
  - `t` timestamps are kept per code.
- Writes: `manager.send_commands` (device.py:~199) only POSTs cloud `{code, value}` commands to the cloud. The SDK has NO inverse conversion and no local write path. The write side is entirely ours to design.
- `custom_strategy.py` is dead code (commented out in `strategy.py:22-25`).

### The 24 strategies (`SDK/strategy_repo/*.py`)
All strategies share the signature `convert((dp_key, dp_value), config_item) -> (status_key, out)`. Every one except `hb_range_*`/`hb_jsq_lightv1`/`enum`/`default` returns `(key, None)` when `dp_value is None`. Base64 decode uses the standard alphabet.

| # | name | input raw (local) | output (cloud) | algorithm |
|---|---|---|---|---|
| 1 | default | any | same value | Passthrough. If value is `None` or `""`, it is replaced by a type default from `config_item.valueType`: Boolean False, Integer `valueDesc.min`, Enum `range[0]`, String/Raw/Bitmap `""`. (default.py) |
| 2 | enum | the enum token as sent by the device: str/int, e.g. "0","1","2","memory","off","on","single_click" | the cloud enum string | `k=str(v)`; look up `enumMappingMap[k]["value"]`, then `enumMappingMap[k.lower()]`. If there is no match, use `default.convert_value` (=`range[0]`). Mapping data is per-dp in `local_strategy`. Real maps: kg/cz `relay_status`: `{0:power_off,1:power_on,2:last,memory:last,off:power_off,on:power_on}`; wxkg `switch_mode*`: `{single_click:click,double_click:double_click,long_press:press}`; tdq `relay_status`: `{0:"0",1:"1",2:"2",memory:"2",off:...}`; dlq `clear_energy`: empty map, so anything becomes `range[0]`="empty". |
| 3 | cz_timer1_alg | base64 of N*6 bytes | JSON string list | Per 6-byte block: `timer_switch=b0>0`; `week_day`=list of set-bit indices of b1 (LSB first, i.e. bit0=Mon..); `start_time`=fmt(b2,b3); `end_time`=fmt(b4,b5). fmt(h,l): `n=h*256+l`, `"%02d:%02d"` of `n//60`, `n%60`. Output is `json.dumps(list)`. |
| 4 | cz_timer2_alg | same, base64 | same JSON string | Same algorithm but 10-byte blocks, adding `open_time`=fmt(b6,b7) and `close_time`=fmt(b8,b9). (Verified: `[{"timer_switch": true, "week_day": [1,3,5], "start_time": "01:00", "end_time": "04:16", "open_time": "00:00", "close_time": "00:00"}]`.) |
| 5 | dj_v1_hsv_alg | hex string `"RRGGBB" + "HHHH" "SS" "VV"` (14 chars), or `"RRGGBB0168ffff"` | JSON string `{"h","s","v"}` | If `s[6:]=="0168ffff"`: r,g,b=bytes; `colorsys.rgb_to_hsv`; `h=round(H*360,1)`, `s=round(S*255,1)`, `v=round(V*255,1)`. Otherwise `h=int(s[6:10],16)`, `s=round(round(int(s[10:12],16)/255,3)*255,1)`, and `v` likewise from `s[12:]`. |
| 6 | dj_v1_scene_alg | hex string: bright(2) temp(2) freq(2) hsvlen(2) then N x RRGGBB | JSON `{"frequency","bright","temperature","hsv":[{h,s,v}...]}` | Decode as described; each RGB -> HSV with h=round(H*360,1), s=round(S*255,1), v=round(V*255,1). Empty input returns `""`. |
| 7 | dj_v2_color_alg | 12-hex string "HHHHSSSSVVVV" | JSON string `{"h","s","v"}` (ints) | `int(x[0:4],16)`, `x[4:8]`, `x[8:12]`. Verified: `"00b403e803e8"` -> `{"h": 180, "s": 1000, "v": 1000}`. **Used by real dj devices, dp 24 `colour_data_v2`.** |
| 8 | dj_v2_contr_alg | hex string: flag(1 hex) h(4) s(4) v(4) bright(4) temp(rest) | JSON `{change_mode,h,s,v,bright,temperature}` | `flag 0->"direct"`, `1->"gradient"` (else `""`). Fields are hex parsed as listed; empty input returns `""`. dp 28 `control_data`. |
| 9 | dj_v2_music_alg | same layout as contr | same JSON | Identical parse. dp 27 `music_data`. |
| 10 | dj_v2_scene_alg | hex: scene_num(2 hex, output = 1+value) then 26-hex units | JSON `{scene_num, scene_units:[...]}` | Unit: switch_dur(2) grad_dur(2) mode(2: 0 static, 1 jump, 2 gradient) h(4) s(4) v(4) bright(4) temp(4). Output field names: `unit_switch_duration, unit_gradient_duration, unit_change_mode, h, s, v, bright, temperature`. dp 25 `scene_data_v2`. |
| 11 | hb_djv1_color | JSON string `{"h":0-360,"s":0-255,"v":0-255}` | `"R\|G\|B"` string | `colorsys.hsv_to_rgb(h/360,s/255,v/255)`; `int(x*255)` each; joined with `\|`. |
| 12 | hb_jsq_lightv1 | str | `value + "\|0\|0"` | Returns `""` if None. |
| 13 | hb_range_v1 | int | int 0..100 | Clamp raw to [25,255], then `floor((raw-25)*100/230)`. |
| 14 | hb_range_v2 | int | int 1000..12000 | `floor(1000 + raw*11000/255)`, raw clamped to [0,255]. |
| 15 | ms_dp_syn_alg | base64 of 2-byte pairs | python `set` of ints (lock unlock indices) | For each pair: `partition=b0&0x7f`, `bits=b1`; for each set bit `i`, add `(partition-1)*8+i`. Returns an empty set for empty input. |
| 16 | sd_clean_record | digit string | JSON `{record_time,clean_time,clean_area,map_id}` | By length: 6 = ttt+aaa; 11 = +map_id[6:11]; 18 = record_time[0:12]+ttt+aaa; else all fields (map_id [18:23]). |
| 17 | voice_atm_color | as dj_v1_hsv (hex 14 chars) | JSON `{h,s,v}` | Same as #5 but rounds s/v to 4 digits first. |
| 18 | db_v1_alarm | base64 -> hex, 8-hex-char (4-byte) records | JSON list `[{alarmCode,doAction,threshold?}]` | `id=byte0` (enum 1..13 -> overcurrent, three_phase_current_imbalance, ammeter_overvoltage, under_voltage, three_phase_current_loss, power_failure, magnetic, insufficient_balance, arrears, battery_overvoltage, cover_open, meter_cover_open, fault). `doAction=byte1==1`. `threshold=uint16(bytes2-3)/10^scale` (scale 0 for ids 1-4 and 8, scale 2 for id 10, none for the others), emitted as a str. Unknown ids are skipped. |
| 19 | db_v1_daily | base64 -> hex | JSON `{startMonth,startDay,endMonth,endDay,electricTotal}` | Bytes 0-3 are the date fields; `electricTotal=uint32(bytes4-7)/100.0`. |
| 20 | db_v1_data | base64 -> BYTES (bug: `hex2dec` applied to raw bytes chars) | JSON `{power,year,month,date,hour,minute,second}` | Written for hex; on raw bytes it is effectively broken. Do not port blindly (see unknowns). |
| 21 | db_v1_frozen | base64 -> hex | `{"day","hour"}` | byte0, byte1. |
| 22 | db_v1_month | base64 -> hex | `{startYear,startMonth,endYear,endMonth,electricTotal}` | Same layout as daily; `total/100`. |
| 23 | db_v1_params | base64 -> hex | JSON `{voltage,electricCurrent,power[,reactivePower,apparentPower,powerFactor]}` | Frame `010f`+17B, or `020f`+18B (last byte = sign bits: 1 current, 2 power, 4 reactive, 8 pf), or a legacy 8B frame with no header. V (2B)/10, I (3B)/1000, P (3B)/1000, Q (3B)/1000, S (3B)/1000, pf (1B)/100. (Verified on a synthetic frame.) |
| 24 | db_v1_tariff | base64 -> raw string (bug: `average_str` on bytes) | JSON per-weekday lists of `{start_time,end_time}` | 7 x 6-byte groups, each of 3x2-byte `[start_hour,end_hour]` slots; the SDK returns `""` if it does not get 7 groups. |

Note: `hb_*` and `voice_atm` are minor. The SDK list totals 24: `default, enum, cz_timer1/2_alg, dj_v1_hsv_alg, dj_v1_scene_alg, dj_v2_{color,contr,music,scene}_alg, hb_djv1_color, hb_jsq_lightv1, hb_range_v1/v2, ms_dp_syn_alg, sd_clean_record, voice_atm_color, db_v1_{alarm,daily,data,frozen,month,params,tariff}`.

## 2. What rustuya-bridge emits

- The device event handler is `/home/ubuntu/script/git/rustuya-bridge/src/bridge.rs:822-912` (`handle_device_event`). It parses the decrypted Tuya JSON, takes `data.dps` (active push) or root `dps` (passive: DP_QUERY reply or status report), and injects `cid` for sub-devices.
- `publish_device_event` (bridge.rs:1734+) publishes that `dps` object via the configured topic/payload templates (default `{"v":{value}}` and per-dp / whole-object forms). In cache mode it merges the dps into `DpsCache`, keeping values as-is.
- There is NO conversion anywhere (no base64 decode, no enum map, no scale). Grep for base64/convert in rustuya-core shows base64 only in the v3.1 transport wrapper (`rustuya-core/src/payload.rs`, `version.rs`), never in dp values.
- Inbound `set` (`payload.rs parse_mqtt_payload`) passes the JSON values through untouched into the device.
- So the bridge emits the RAW LAN dps as JSON, keyed by numeric dp id string: `{"1": true, "2": 200, "21": "white", "24": "00b403e803e8", "28": "..."}`.
  - bool -> JSON true/false.
  - int -> number (scale NOT applied; raw ints, e.g. temp*10).
  - enum -> JSON string (device dependent, e.g. "off"/"memory"/"single_click"/"0", NOT necessarily the cloud enum token).
  - Raw -> base64 string; this is also the cloud form, so it is the same as cloud.
  - hex-encoded / "Json"-typed dps -> plain hex strings (`dj_v2_*`), or JSON-encoded strings (`colour_data`).
- The tricky bit is that the raw LAN value is exactly the input the SDK feeds its strategies (the cloud MQ pushes the same raw dp values), so the SDK strategy list is the definitive list of what differs.
- Our `bridge_device.py:171-192` (`_apply_dps`) stores raw bridge values directly as `device.status[code]` (via `local_strategy[dp].status_code`), with no conversion. Writes (`async_send_commands`, lines 141-169) send `cmd["value"]` straight to the bridge as the dp value, also with no inverse. It also uses `status_code`, not the statusFormat key (see item 3).

## 3. Where bridge-fed values differ from what core/handlers expect

Data: `/home/ubuntu/script/git/rustuya-homeassistant/tuyadevices.json` (60 devices; 55 have `local_strategy`; 5 have none; 341 dps total, 0 devices with `support_local` false). Core fixtures: 324, of which 16 have `local_strategy`, 4 of those with non-default strategies (149 default dps, 4 enum, 12 `dj_v2_*`).

| strategy | tuyadevices.json dps (devices) | categories | core fixtures dps |
|---|---|---|---|
| default | 304 (54) | kg 99, tdq 56, cz 55, mcs 26, cl 24, hps 14 ... | 149 |
| enum | 37 (27) | wxkg 17, kg 11, tdq 5, cz 4 | 4 (tdq x3, dlq x1) |
| dj_v2_color/scene/music/contr_alg | 0 | -- | 3 each = 12 (3 dj devices) |
| all other 19 (cz_timer, db_v1_*, dj_v1_*, hb_*, ms, sd, voice) | 0 | -- | 0 |

- 27/60 devices in tuyadevices.json (45%) carry at least one non-`default` strategy, and all of them are ONLY `enum` (none use the 19 "algorithmic" strategies). 304/341 = 89% of dps are `default`, i.e. passthrough.
- Of the `enum` dps, 0/37 have an identity mapping (every map changes at least the raw token spelling), so every enum dp needs the mapping. `enum` dps are:
  - `relay_status` (kg/cz/tdq): raw "0"/"1"/"2"/"on"/"off"/"memory" -> "power_off"/"power_on"/"last" for kg/cz and "0"/"1"/"2" for tdq.
  - `switch_mode1..4` (wxkg): raw "single_click"/"long_press" -> "click"/"press". Otherwise "double_click" is unchanged.
  - `clear_energy` (dlq): empty map, so always `range[0]` ("empty"); it is a write-only trigger anyway.
- Categories that must convert on the read side (from real fixtures and the SDK repo): dj (colour_data_v2 / scene_data_v2 / music_data / control_data: 4 dps per dj device with v2 firmware; core fixtures 3 of 3 dj devices with local_strategy). Core handlers `json.loads` these strings, and the raw hex will break them.
- `default` type quirks (still needed): the None/"" -> type-default substitution (e.g. `energy_reset: ""`).
- Status-code naming: the SDK writes to `statusFormat` key, not `status_code`. Mismatches: wsdcg dp2 (`humidity_value` -> `va_humidity`) and dlq dp12 (`clear_energy` -> `energy_reset`). Our bridge_device uses `status_code`. That is a BUG relative to core/handlers expectations. Check `device.status` keys: fixtures show `va_humidity` and `energy_reset`. (statusFormat is a dict in tuyadevices.json but a JSON STRING in core fixtures; the SDK `json.loads` it, so handle both.)
- Dps that need no conversion: bool/int/string/Bitmap/Raw base64 with `default`. Cloud statuses hold the same value (`phase_a: 'CN8AAP8AAAY='` in the dlq fixture is base64 in both worlds).
- Unknown: for cz_timer / db_v1_* / dj_v1 / hb / ms / sd, no corpus device in either data set uses them, but they exist in the SDK, so the cloud does hand them out for e.g. cz (countdown timers), some hb (Hubble) lights, some smart locks (ms) and db (breaker) devices.

## 4. Recommendation

Read side (dp -> cloud code), implemented in the IL/renderer or in `BridgeDeviceManager._apply_dps`, mirroring `_on_device_report` exactly:
1. For each `dp_id`: `meta = local_strategy[dp_id]` (skip if missing).
2. `strategy = meta["value_convert"]`. Look up a port table; unknown strategy -> log once and fall back to `default`.
3. `code, value = convert(strategy, (status_code, raw), config_item)`. Use the **statusFormat key** as `code`, not `status_code`.
4. Enum guard: if `status_range[code].type == "Enum"` and `value not in range`, drop the update (like the SDK).
5. Store `device.status[code] = value`.
Priority to port, in order:
   a. `default` (None/"" substitution) and `enum` (the mapping table is fully in `local_strategy`), which cover 100% of the local corpus.
   b. `dj_v2_color/scene/music/contr_alg` (only 4 tiny hex parsers, and exercised by real dj fixtures).
   c. `cz_timer1/2_alg`, `dj_v1_hsv/scene`, `voice_atm_color`, `hb_djv1_color/range_v1/range_v2/hb_jsq_lightv1`, `ms_dp_syn_alg`, `sd_clean_record`, `db_v1_{params,daily,month,frozen,alarm}` (straightforward, algorithms above).
   d. Skip `db_v1_data`/`db_v1_tariff`, which look buggy in the SDK; decide from live data.
Because cloud/Tuya devices push through the same raw forms, the SDK strategy repo (SDK/strategy_repo/*.py, Apache-2.0) is the best port source. Vendoring it wholesale (it is 1,400 lines, pure python, only stdlib) is much cheaper than re-deriving; only `manager._on_device_report` (~25 lines) needs re-implementing.

Write side (cloud command `{code, value}` -> raw dp): no SDK precedent, so invert per strategy:
- `default`: passthrough.
- `enum`: invert `enumMappingMap` by choosing a raw key whose mapped `value` == the desired cloud value. Prefer the numeric-string key or the key that matches the last raw form seen from the device (relay_status has 0/1/2 AND on/off/memory; the wire type in the local protocol is a string, the device advertises which it accepts).
- `dj_v2_color/contr/music/scene_alg`: re-encode: `"%04x%04x%04x" % (h,s,v)`; contr/music: `flag(1) h s v bright temp`; scene: `"%02x" % (scene_num-1)` + units. Verify each with live devices.
- Timers, db_v1_*, hb_*: low priority; mark entity read-only until verified.
- `hb_range_v*` are lossy; the inverse is an approximation.

Unknowns needing live-device verification:
1. Actual wire types of enum dps over the LAN. E.g. does a kg `relay_status` arrive as "memory"/"off"/"on" or as "0"/"1"/"2"? Which do writes accept?
2. Whether `support_local == false` devices (custom-type) appear in practice. They would make `local_strategy` empty and the manager fall back to cloud-shaped statuses; rustuya-manager's cloud wizard output for such devices needs a design decision.
3. dj colour_data_v2 wire form (hex string, 12 chars) and whether the LAN reports firmware v1 (`dj_v1_hsv_alg`, 14 hex chars) or v2 for the same product, and the write encoding.
4. Bitmap/Raw/JSON dp values after the bridge's JSON parse: does the bridge ever hand a JSON dp as an object instead of a string? (The dp value is passed straight through `serde_json`, so a device that sends a JSON string stays a string.)
5. The `cid` key that the bridge injects into `dps` for sub-devices is not a dp id and must be ignored (bridge_device.py already ignores it because it is not in the map).
6. Sub-device / gateway `t` timestamps are not used by the bridge.
7. `db_v1_data` and `db_v1_tariff` in the SDK look broken on real payloads (hex slicing applied to raw bytes); confirm with a real device before porting.
