//! Small, language-neutral expression and state-transition interpreter.
//! Programs are data: no eval, filesystem, networking, imports or clock reads.
use crate::data::*;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value as V, json};
#[derive(Clone, Serialize, Deserialize)]
pub struct Machine {
    pub config: V,
    pub state: V,
}
fn path<'a>(root: &'a V, parts: &[String]) -> Option<&'a V> {
    let mut v = root;
    for part in parts {
        v = if let Some(o) = v.as_object() {
            o.get(part)?
        } else {
            let a = v.as_array()?;
            a.get(part.parse::<usize>().ok()?)?
        };
    }
    Some(v)
}
fn set(root: &mut V, parts: &[String], value: V) -> Result<(), String> {
    let Some((key, parents)) = parts.split_last() else {
        return Err("empty assignment path".into());
    };
    let mut v = root;
    for part in parents {
        if !v.is_object() {
            *v = json!({});
        }
        if v.get(part).is_none() {
            v[part] = json!({});
        }
        v = &mut v[part];
    }
    if !v.is_object() {
        *v = json!({});
    }
    v[key] = value;
    Ok(())
}
pub fn expr(e: &V, ctx: &V) -> Result<V, String> {
    let Some(op) = e.get("op").and_then(V::as_str) else {
        return Ok(e.clone());
    };
    let args = arr(&e["args"]);
    let values = args
        .iter()
        .map(|x| expr(x, ctx))
        .collect::<Result<Vec<_>, _>>()?;
    let a = values.first().cloned().unwrap_or(V::Null);
    let b = values.get(1).cloned().unwrap_or(V::Null);
    Ok(match op {
        "literal" => e["value"].clone(),
        "get" => path(ctx, &codes(&e["path"]))
            .cloned()
            .unwrap_or_else(|| e["default"].clone()),
        "if" => {
            if truth(&expr(&e["condition"], ctx)?) {
                expr(&e["then"], ctx)?
            } else {
                expr(&e["else"], ctx)?
            }
        }
        "coalesce" => values.into_iter().find(|v| !v.is_null()).unwrap_or(V::Null),
        "eq" => json!(eq(&a, &b)),
        "ne" => json!(!eq(&a, &b)),
        "not" => json!(!truth(&a)),
        "and" => json!(values.iter().all(truth)),
        "or" => json!(values.iter().any(truth)),
        "contains" => json!(if a.is_array() {
            items(&a).iter().any(|v| eq(v, &b))
        } else if a.is_object() {
            a.get(s(&b)).is_some()
        } else if a.is_string() && b.is_string() {
            s(&a).contains(s(&b))
        } else {
            false
        }),
        "is_number" => json!(a.is_number()),
        "is_null" => json!(a.is_null()),
        "lt" | "le" | "gt" | "ge" => json!(
            a.is_number()
                && b.is_number()
                && match op {
                    "lt" => n(&a) < n(&b),
                    "le" => n(&a) <= n(&b),
                    "gt" => n(&a) > n(&b),
                    _ => n(&a) >= n(&b),
                }
        ),
        "add" | "sub" | "mul" | "div" | "min" | "max" => {
            if !a.is_number() || !b.is_number() {
                V::Null
            } else {
                number(match op {
                    "add" => n(&a) + n(&b),
                    "sub" => n(&a) - n(&b),
                    "mul" => n(&a) * n(&b),
                    "div" => n(&a) / n(&b),
                    "min" => n(&a).min(n(&b)),
                    _ => n(&a).max(n(&b)),
                })
            }
        }
        "round_half_even" => {
            if a.is_number() {
                number(n(&a).round_ties_even())
            } else {
                V::Null
            }
        }
        "lookup" => a
            .get(s(&b))
            .cloned()
            .unwrap_or_else(|| e["default"].clone()),
        _ => return Err(format!("unknown expression op {op}")),
    })
}
const INSTRUCTION_LIMIT: usize = 10_000;
#[derive(Default, Serialize)]
struct Output {
    values: Map<String, V>,
    timers: Map<String, V>,
    commands: Vec<V>,
}
fn run(program: &V, ctx: &mut V, result: &mut Output, budget: &mut usize) -> Result<(), String> {
    if !program.is_null() && !program.is_array() {
        return Err("rule program must be an array".into());
    }
    for stmt in items(program) {
        if *budget == 0 {
            return Err("rule instruction limit exceeded".into());
        }
        *budget -= 1;
        if stmt.get("if").is_some() {
            let yes = truth(&expr(&stmt["if"], ctx)?);
            run(
                if yes { &stmt["then"] } else { &stmt["else"] },
                ctx,
                result,
                budget,
            )?;
        } else if stmt.get("set").is_some() {
            let parts = codes(&stmt["set"]);
            if parts.first().is_none_or(|p| p != "state") {
                return Err("rules can assign only state slots".into());
            }
            let value = expr(&stmt["value"], ctx)?;
            set(ctx, &parts, value)?;
        } else if stmt.get("emit").is_some() {
            result
                .values
                .insert(s(&stmt["emit"]).into(), expr(&stmt["value"], ctx)?);
        } else if stmt.get("timer").is_some() {
            result
                .timers
                .insert(s(&stmt["timer"]).into(), expr(&stmt["after"], ctx)?);
        } else if stmt.get("send").is_some() {
            let send = &stmt["send"];
            let command =
                json!({"code":expr(&send["code"],ctx)?,"value":expr(&send["value"],ctx)?});
            result.commands.push(command);
        } else {
            return Err("unknown rule statement".into());
        }
    }
    Ok(())
}
impl Machine {
    pub fn invoke(&mut self, method: &str, input: &V) -> Result<V, String> {
        let mut ctx = input.clone();
        if !ctx.is_object() {
            ctx = json!({});
        }
        ctx["state"] = self.state.clone();
        ctx["config"] = self.config.clone();
        let program = if method == "write" {
            &self.config["write"][s(&input["prop"])]
        } else {
            &self.config[method]
        };
        let mut result = Output::default();
        let mut budget = INSTRUCTION_LIMIT;
        run(program, &mut ctx, &mut result, &mut budget)?;
        let result = serde_json::to_value(result).map_err(|e| e.to_string())?;
        self.state = ctx["state"].clone();
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn failed_program_does_not_commit_state() {
        let mut machine = Machine {
            config: json!({"update":[
                {"set":["state","count"],"value":2},
                {"emit":"count","value":{"op":"unknown"}}
            ]}),
            state: json!({"count":1}),
        };
        assert!(machine.invoke("update", &json!({})).is_err());
        assert_eq!(machine.state, json!({"count":1}));
    }

    #[test]
    fn nested_programs_share_instruction_budget() {
        let statement = json!({"set":["state","count"],"value":2});
        let mut machine = Machine {
            config: json!({"update":[{"if":true,"then":vec![statement; INSTRUCTION_LIMIT]}]}),
            state: json!({"count":1}),
        };
        assert_eq!(
            machine.invoke("update", &json!({})).unwrap_err(),
            "rule instruction limit exceeded"
        );
        assert_eq!(machine.state, json!({"count":1}));
    }
}
