use serde_json::{Value, json};
use tuya_rule_engine::evaluate_json;

#[test]
fn python_numeric_vectors() {
    let cases: Vec<Value> = serde_json::from_str(include_str!("numeric_vectors.json")).unwrap();
    for case in cases {
        let actual: Value =
            serde_json::from_str(&evaluate_json(&case["request"].to_string())).unwrap();
        assert_eq!(
            actual["ok"], case["expected"]["ok"],
            "{}: {actual}",
            case["name"]
        );
        if actual["ok"] == true {
            let expected = &case["expected"]["value"];
            if expected.is_number() {
                assert_eq!(
                    actual["value"].as_f64(),
                    expected.as_f64(),
                    "{}",
                    case["name"]
                );
            } else if expected.is_boolean() {
                // Python integer validation preserves bool; later scale converts it to numeric.
                assert_eq!(
                    actual["value"].as_f64(),
                    Some(if expected == true { 1.0 } else { 0.0 })
                );
            } else {
                assert_eq!(&actual["value"], expected, "{}", case["name"]);
            }
        }
    }
}

#[test]
fn rejects_invalid_protocol() {
    for input in [
        "not json".to_string(),
        json!({"version": 99, "value": 1, "ops": []}).to_string(),
        json!({"version": 1, "value": 1, "ops": [{"op": "python_eval"}]}).to_string(),
        json!({"version": 1, "value": 1, "ops": [], "unexpected": true}).to_string(),
    ] {
        let response: Value = serde_json::from_str(&evaluate_json(&input)).unwrap();
        assert_eq!(response["ok"], false);
    }
}

fn canonical(v: &Value) -> Value {
    match v {
        Value::Array(xs) => Value::Array(xs.iter().map(canonical).collect()),
        Value::Object(o) => {
            let mut o = o.clone();
            if o.get("type").is_some_and(|v| v == "reject") {
                o.remove("reason");
            }
            Value::Object(o)
        }
        _ => v.clone(),
    }
}

#[test]
fn full_driver_reference_traces() {
    let rules: Value = serde_json::from_str(include_str!("../../../rules/default.json")).unwrap();
    let cases: Vec<Value> = serde_json::from_str(include_str!("driver_vectors.json")).unwrap();
    let install: Value = serde_json::from_str(&evaluate_json(
        &json!({"op":"install_rules","rules_id":"conformance","rules":rules}).to_string(),
    ))
    .unwrap();
    assert_eq!(install["ok"], true);
    for case in cases {
        let call = |request: Value| {
            let mut request = request;
            request["rules_id"] = json!("conformance");
            let response: Value =
                serde_json::from_str(&evaluate_json(&request.to_string())).unwrap();
            assert_eq!(response["ok"], true, "{}: {response}", case["name"]);
            response["value"].clone()
        };
        let mut driver = call(
            json!({"op":"create","device":case["device"],"options":case["options"]}),
        )["driver"]
            .clone();
        assert_eq!(
            driver["assembly"]["descriptor"], case["descriptor"],
            "{} descriptor",
            case["name"]
        );
        for step in case["steps"].as_array().unwrap() {
            let response = call(
                json!({"op":"handle","driver":driver,"input":step["input"],"now":step["now"]}),
            );
            assert_eq!(
                canonical(&response["outputs"]),
                canonical(&step["outputs"]),
                "{} step {}: {}",
                case["name"],
                step["now"],
                step["input"]
            );
            driver = response["driver"].clone();
        }
    }
}

#[test]
fn ffi_ownership_and_invalid_inputs() {
    use std::ffi::CStr;
    use tuya_rule_engine::{tuya_engine_eval, tuya_engine_free};
    for input in [std::ptr::null(), c"not json".as_ptr(), c"\xff".as_ptr()] {
        unsafe {
            let output = tuya_engine_eval(input);
            assert!(!output.is_null());
            let response: Value =
                serde_json::from_slice(CStr::from_ptr(output).to_bytes()).unwrap();
            assert_eq!(response["ok"], false);
            tuya_engine_free(output);
        }
    }
    unsafe {
        tuya_engine_free(std::ptr::null_mut());
    }
}
