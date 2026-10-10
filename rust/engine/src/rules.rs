//! Host-side rule loading; device evaluation remains sans-I/O.
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

fn patch(dst: &mut Value, src: &Value) {
    if let Some(fields) = src.as_object() {
        if !dst.is_object() {
            *dst = json!({});
        }
        for (key, value) in fields {
            if value == &json!({"$delete": true}) {
                dst.as_object_mut().unwrap().remove(key);
            } else {
                patch(&mut dst[key], value);
            }
        }
    } else {
        *dst = src.clone();
    }
}

// Interpret the bundled schema's vocabulary, so validation stays language-neutral.
fn check(value: &Value, schema: &Value, root: &Value, at: &str) -> Result<(), String> {
    if let Some(reference) = schema["$ref"].as_str() {
        return check(
            value,
            root.pointer(reference.trim_start_matches('#'))
                .ok_or("invalid schema reference")?,
            root,
            at,
        );
    }
    let valid = match schema["type"].as_str() {
        Some("object") => value.is_object(),
        Some("array") => value.is_array(),
        Some("string") => value.is_string(),
        Some("number") => value.is_number(),
        Some("boolean") => value.is_boolean(),
        None => true,
        _ => false,
    };
    if !valid {
        return Err(format!("rules {at}: expected {}", schema["type"]));
    }
    if let Some(expected) = schema.get("const")
        && value != expected
    {
        return Err(format!("rules {at}: expected {expected}"));
    }
    if let Some(choices) = schema["enum"].as_array()
        && !choices.contains(value)
    {
        return Err(format!("rules {at}: unsupported value {value}"));
    }
    if let Some(text) = value.as_str()
        && text.chars().count() < schema["minLength"].as_u64().unwrap_or(0) as usize
    {
        return Err(format!("rules {at}: string too short"));
    }
    if let Some(fields) = value.as_object() {
        if let Some(required) = schema["required"].as_array() {
            for key in required {
                if !fields.contains_key(key.as_str().unwrap()) {
                    return Err(format!("rules {at}: missing {key}"));
                }
            }
        }
        for (key, item) in fields {
            let child = schema["properties"]
                .get(key)
                .or_else(|| schema.get("additionalProperties").filter(|v| v.is_object()));
            if let Some(child) = child {
                check(item, child, root, &format!("{at}/{key}"))?;
            } else if schema["additionalProperties"] == false {
                return Err(format!("rules {at}: unknown field {key}"));
            }
        }
    }
    if let Some(items) = value.as_array() {
        for (i, item) in items.iter().enumerate() {
            if let Some(child) = schema.get("items") {
                check(item, child, root, &format!("{at}/{i}"))?;
            }
            if schema["uniqueItems"] == true && items[..i].contains(item) {
                return Err(format!("rules {at}: duplicate item"));
            }
        }
    }
    Ok(())
}

pub fn validate(value: &Value) -> Result<(), String> {
    let schema: Value = serde_json::from_str(include_str!("../../../rules/schema.json"))
        .map_err(|e| e.to_string())?;
    check(value, &schema, &schema, "")
}

pub fn compose(base: &Value, layers: &[Value]) -> Result<Value, String> {
    let mut result = base.clone();
    for layer in layers {
        if !layer.is_object() {
            return Err("rule layer must be a JSON object".into());
        }
        patch(&mut result, layer);
    }
    validate(&result)?;
    Ok(result)
}

fn files(path: &Path) -> Result<Vec<PathBuf>, String> {
    if path.is_file() {
        return Ok(vec![path.to_owned()]);
    }
    let mut paths = vec![];
    for entry in std::fs::read_dir(path).map_err(|e| format!("{}: {e}", path.display()))? {
        let entry = entry.map_err(|e| e.to_string())?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if entry.file_type().map_err(|e| e.to_string())?.is_file()
            && name.ends_with(".json")
            && !name.starts_with(['.', '_'])
            && name != "schema.json"
            && name != "manifest.json"
        {
            paths.push(entry.path());
        }
    }
    paths.sort_by(|a, b| a.file_name().cmp(&b.file_name()));
    // Host-saved settings always win, regardless of other filenames.
    if let Some(i) = paths
        .iter()
        .position(|p| p.file_name().is_some_and(|n| n == "zz_settings.json"))
    {
        let p = paths.remove(i);
        paths.push(p);
    }
    Ok(paths)
}

pub fn load(input: &Value) -> Result<Value, String> {
    let base = if input["base"].is_object() {
        input["base"].clone()
    } else if let Some(path) = input["default_path"].as_str() {
        read(Path::new(path))?
    } else {
        serde_json::from_str(include_str!("../../../rules/default.json"))
            .map_err(|e| e.to_string())?
    };
    validate(&base)?;
    let mut result = base.clone();
    let mut sources = vec![];
    let mut warnings = vec![];
    let mut paths = vec![];
    for path in input["paths"].as_array().ok_or("paths must be an array")? {
        paths.extend(files(Path::new(
            path.as_str().ok_or("path must be a string")?,
        ))?);
    }
    paths.sort_by_key(|p| p.file_name().is_some_and(|n| n == "zz_settings.json"));
    for file in paths {
        let loaded = (|| {
            let mut value = read(&file)?;
            if !value.is_object() {
                return Err("expected a JSON object".to_string());
            }
            let settings = file.file_name().is_some_and(|n| n == "zz_settings.json");
            let legacy = input["legacy_overrides"] == true
                && value
                    .as_object()
                    .unwrap()
                    .keys()
                    .all(|k| base.get(k).is_none());
            if (settings && value.get("overrides").is_none()) || legacy {
                value = json!({"overrides":value});
            }
            compose(&result, &[value])
        })();
        match loaded {
            Ok(next) => {
                result = next;
                sources.push(file.to_string_lossy().to_string());
            }
            Err(error) if input["tolerant"] == true => warnings.push(format!(
                "{}: {error}",
                file.file_name().unwrap().to_string_lossy()
            )),
            Err(error) => return Err(format!("{}: {error}", file.display())),
        }
    }
    Ok(json!({"rules":result,"sources":sources,"warnings":warnings}))
}

fn read(path: &Path) -> Result<Value, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))
}
