use crate::core::find_repo_root;
use crate::errors::LitError;
use crate::response::{BatchOperationResult, BatchResponse};
use serde::Deserialize;
use serde_json::{Map, Value};
use std::io::{self, BufRead};

#[derive(Deserialize)]
#[serde(tag = "command", content = "args", rename_all = "snake_case")]
enum Operation {
    Add {
        files: Vec<String>,
    },
    Commit {
        message: String,
        author: Option<String>,
    },
    Status {},
    Branch {
        name: Option<String>,
        #[serde(default)]
        delete: bool,
        #[serde(default)]
        all: bool,
    },
    Checkout {
        target: String,
        #[serde(default)]
        create: bool,
    },
    Log {
        count: Option<usize>,
        #[serde(default)]
        oneline: bool,
    },
    Snapshot {
        message: String,
        author: Option<String>,
        metadata: Option<Value>,
    },
}

pub fn execute(atomic: bool, dry_run: bool) -> Result<BatchResponse, LitError> {
    find_repo_root()?;
    execute_lines(io::stdin().lock(), atomic, dry_run)
}

fn execute_lines(
    input: impl BufRead,
    atomic: bool,
    dry_run: bool,
) -> Result<BatchResponse, LitError> {
    // Read the complete request before running its first operation.
    let lines = input
        .lines()
        .enumerate()
        .map(|(index, line)| line.map(|line| (index + 1, line)))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| LitError::io(format!("Read batch input: {e}")))?;
    let mut results = Vec::new();
    let mut succeeded = 0;
    let mut failed = 0;

    for (line, input) in lines {
        if input.trim().is_empty() {
            continue;
        }

        let mut result = BatchOperationResult {
            index: results.len(),
            line,
            command: String::new(),
            status: "ok".into(),
            result: None,
            error: None,
            error_code: None,
        };
        let parsed = parse_operation(&input, &mut result.command);
        if atomic && failed != 0 {
            result.status = "skipped".into();
            result.error = Some("Skipped after a prior operation failed".into());
        } else {
            let outcome = parsed
                .map_err(|message| ("INVALID_BATCH_OPERATION", message))
                .and_then(|operation| {
                    if dry_run {
                        Ok(serde_json::json!({"dry_run": true}))
                    } else {
                        apply(operation)
                            .map_err(|error| (error.error_code(), error.user_message().to_owned()))
                    }
                });
            match outcome {
                Ok(value) => {
                    succeeded += 1;
                    result.result = Some(value);
                }
                Err((code, message)) => {
                    failed += 1;
                    result.status = "error".into();
                    result.error = Some(message);
                    result.error_code = Some(code.into());
                }
            }
        }
        results.push(result);
    }

    if results.is_empty() {
        return Err("Provide at least one JSONL batch operation on stdin".into());
    }

    Ok(BatchResponse {
        total: results.len(),
        succeeded,
        failed,
        atomic,
        dry_run,
        results,
    })
}

fn parse_operation(input: &str, command: &mut String) -> Result<Operation, String> {
    let mut fields = serde_json::from_str::<Map<String, Value>>(input)
        .map_err(|_| "Expected a JSON object on this line".to_owned())?;
    let (name, args) = match (fields.remove("command"), fields.remove("op")) {
        (Some(name), None) => (
            name,
            fields
                .remove("args")
                .unwrap_or_else(|| Value::Object(Map::new())),
        ),
        (None, Some(name)) => (name, Value::Object(fields)),
        _ => return Err("Provide one command field, or use the flat op form".into()),
    };
    let name = name.as_str().ok_or("Command must be a string")?;
    name.clone_into(command);
    if !args.is_object() {
        return Err("The args field must be a JSON object".into());
    }
    let operation = serde_json::from_value::<Operation>(serde_json::json!({
        "command": name,
        "args": args,
    }))
    .map_err(|error| {
        // Deserialization may include the supplied value. Avoid echoing arguments.
        if error.to_string().starts_with("unknown variant") {
            "Unknown batch command".to_owned()
        } else {
            "Missing or invalid command arguments. See the command schema.".to_owned()
        }
    })?;
    if matches!(&operation, Operation::Add { files } if files.is_empty() || files.iter().any(|path| path.is_empty()))
    {
        return Err("Add requires a non-empty files array of non-empty paths".into());
    }
    Ok(operation)
}

fn apply(operation: Operation) -> Result<Value, LitError> {
    use crate::commands;
    let value = match operation {
        Operation::Add { files } => serde_json::to_value(commands::add::execute(files)?),
        Operation::Commit { message, author } => {
            serde_json::to_value(commands::commit::execute(message, author)?)
        }
        Operation::Status {} => serde_json::to_value(commands::status::execute()?),
        Operation::Branch { name, delete, all } => {
            serde_json::to_value(commands::branch::execute(name, delete, all)?)
        }
        Operation::Checkout { target, create } => {
            serde_json::to_value(commands::checkout::execute(target, create)?)
        }
        Operation::Log { count, oneline } => {
            serde_json::to_value(commands::log::execute(count.unwrap_or(10), oneline)?)
        }
        Operation::Snapshot {
            message,
            author,
            metadata,
        } => serde_json::to_value(commands::snapshot::execute(message, author, metadata)?),
    };
    value.map_err(|e| e.to_string().into())
}
