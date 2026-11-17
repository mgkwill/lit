use crate::core::{Blob, Commit, Object, ObjectHash, Tag, Tree};
use crate::errors::LitError;
use sha1::{Digest, Sha1};
use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::process::{Command, Output, Stdio};

pub(super) fn git(directory: &Path) -> Command {
    let mut command = Command::new("git");
    command
        .current_dir(directory)
        .arg("--no-replace-objects")
        .env("GIT_NO_LAZY_FETCH", "1")
        .env("GIT_TERMINAL_PROMPT", "0");
    for variable in [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
    ] {
        command.env_remove(variable);
    }
    command
}

pub(super) fn checked_output(command: &mut Command) -> Result<Output, LitError> {
    let output = command.stdin(Stdio::null()).output().map_err(|error| {
        format!("Could not run Git. Install git and make it available on PATH: {error}")
    })?;
    if !output.status.success() {
        return Err(format!(
            "Git {} failed: {}",
            command
                .get_args()
                .skip(1)
                .map(|arg| arg.to_string_lossy())
                .collect::<Vec<_>>()
                .join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        )
        .into());
    }
    Ok(output)
}

pub(super) fn text(bytes: &[u8]) -> Result<&str, LitError> {
    std::str::from_utf8(bytes).map_err(|_| "Git ref or path is not valid UTF-8".into())
}

pub(super) fn valid_oid(oid: &str) -> bool {
    oid.len() == 40 && oid.bytes().all(|byte| byte.is_ascii_hexdigit())
}

pub(super) fn git_oid(kind: &str, body: &[u8]) -> String {
    let mut hash = Sha1::new();
    hash.update(format!("{kind} {}\0", body.len()));
    hash.update(body);
    hex::encode(hash.finalize())
}

/// Translate in dependency order without recursing through commit history.
pub(super) fn translate<T, U>(
    roots: Vec<String>,
    mut read: impl FnMut(&str) -> Result<(T, Vec<String>), LitError>,
    mut convert: impl FnMut(T, &HashMap<String, U>) -> Result<U, LitError>,
) -> Result<HashMap<String, U>, LitError> {
    let mut converted = HashMap::new();
    let mut visiting = HashSet::new();
    let mut stack: Vec<_> = roots.into_iter().map(|id| (id, None)).collect();
    while let Some((id, loaded)) = stack.pop() {
        if let Some(object) = loaded {
            let value = convert(object, &converted)?;
            visiting.remove(&id);
            converted.insert(id, value);
        } else if !converted.contains_key(&id) {
            if !visiting.insert(id.clone()) {
                return Err(format!("Cycle in object dependencies at {id}").into());
            }
            let (object, dependencies) = read(&id)?;
            stack.push((id, Some(object)));
            stack.extend(dependencies.into_iter().rev().map(|id| (id, None)));
        }
    }
    Ok(converted)
}

pub(super) struct GitObject {
    pub kind: String,
    pub body: Vec<u8>,
}

struct GitTreeEntry<'a> {
    mode: &'a str,
    name: &'a str,
    oid: String,
}

impl GitObject {
    pub fn dependencies(&self) -> Result<Vec<String>, LitError> {
        match self.kind.as_str() {
            "blob" => Ok(Vec::new()),
            "tree" => Ok(parse_tree(&self.body)?
                .into_iter()
                .filter(|entry| entry.mode != "160000")
                .map(|entry| entry.oid)
                .collect()),
            "commit" => {
                let (headers, _) = split_headers(&self.body)?;
                let tree = required_header(headers, b"tree ")?;
                std::iter::once(tree)
                    .chain(values(headers, b"parent "))
                    .map(parse_oid)
                    .collect()
            }
            "tag" => {
                let (headers, _) = split_headers(&self.body)?;
                Ok(vec![parse_oid(required_header(headers, b"object ")?)?])
            }
            kind => Err(format!("Unsupported Git object type: {kind}").into()),
        }
    }

    pub fn to_lit(&self, objects: &HashMap<String, ObjectHash>) -> Result<Object, LitError> {
        let resolve = |oid: &str| {
            objects
                .get(oid)
                .cloned()
                .ok_or_else(|| LitError::object(format!("Missing Git object {oid}")))
        };
        match self.kind.as_str() {
            "blob" => Ok(Object::Blob(Blob::new(self.body.clone()))),
            "tree" => {
                let mut tree = Tree::new();
                for entry in parse_tree(&self.body)? {
                    let kind = match entry.mode {
                        "40000" => "tree",
                        "160000" => "gitlink",
                        _ => "blob",
                    };
                    let hash = if kind == "gitlink" {
                        ObjectHash::from_hex(entry.oid)
                    } else {
                        resolve(&entry.oid)?
                    };
                    let mode = if kind == "tree" { "040000" } else { entry.mode };
                    tree.add_entry(mode.into(), entry.name.into(), hash, kind.into());
                }
                Ok(Object::Tree(tree))
            }
            "commit" => {
                let (headers, message) = split_headers(&self.body)?;
                let (author, timestamp) = identity(required_header(headers, b"author ")?);
                let (committer, _) = identity(required_header(headers, b"committer ")?);
                Ok(Object::Commit(Commit {
                    tree: resolve(text(required_header(headers, b"tree ")?)?)?,
                    parents: values(headers, b"parent ")
                        .map(|parent| resolve(text(parent)?))
                        .collect::<Result<_, _>>()?,
                    author,
                    committer,
                    timestamp,
                    message: String::from_utf8_lossy(message).into_owned(),
                    pq_signature: None,
                    metadata: None,
                    git_source: Some(self.body.clone()),
                }))
            }
            "tag" => {
                let (headers, message) = split_headers(&self.body)?;
                let (tagger, timestamp) = values(headers, b"tagger ")
                    .next()
                    .map(identity)
                    .unwrap_or_default();
                Ok(Object::Tag(Tag {
                    target: resolve(text(required_header(headers, b"object ")?)?)?,
                    target_type: text(required_header(headers, b"type ")?)?.into(),
                    tag_name: String::from_utf8_lossy(required_header(headers, b"tag ")?)
                        .into_owned(),
                    tagger,
                    timestamp,
                    message: String::from_utf8_lossy(message).into_owned(),
                    pq_signature: None,
                    metadata: None,
                    git_source: Some(self.body.clone()),
                }))
            }
            kind => Err(format!("Unsupported Git object type: {kind}").into()),
        }
    }
}

fn parse_tree(mut body: &[u8]) -> Result<Vec<GitTreeEntry<'_>>, LitError> {
    let mut entries = Vec::new();
    while !body.is_empty() {
        let nul = body
            .iter()
            .position(|byte| *byte == 0)
            .ok_or("Malformed Git tree entry")?;
        let oid = body
            .get(nul + 1..nul + 21)
            .ok_or("Truncated Git tree object ID")?;
        let (mode, name) = text(&body[..nul])?
            .split_once(' ')
            .ok_or("Malformed Git tree mode")?;
        if !matches!(mode, "40000" | "100644" | "100755" | "120000" | "160000") {
            return Err(format!("Unsupported Git tree mode: {mode}").into());
        }
        if name.is_empty()
            || name.contains(['/', '\\'])
            || matches!(name, "." | "..")
            || name.eq_ignore_ascii_case(".git")
            || name.eq_ignore_ascii_case(".lit")
        {
            return Err(format!("Unsupported Git tree path: {name}").into());
        }
        entries.push(GitTreeEntry {
            mode,
            name,
            oid: hex::encode(oid),
        });
        body = &body[nul + 21..];
    }
    Ok(entries)
}

fn split_headers(body: &[u8]) -> Result<(&[u8], &[u8]), LitError> {
    let separator = body
        .windows(2)
        .position(|window| window == b"\n\n")
        .ok_or("Missing header separator in Git object")?;
    Ok((&body[..separator], &body[separator + 2..]))
}

fn values<'a>(headers: &'a [u8], prefix: &'a [u8]) -> impl Iterator<Item = &'a [u8]> {
    headers
        .split(|byte| *byte == b'\n')
        .filter_map(move |line| line.strip_prefix(prefix))
}

fn required_header<'a>(headers: &'a [u8], prefix: &'a [u8]) -> Result<&'a [u8], LitError> {
    values(headers, prefix).next().ok_or_else(|| {
        format!(
            "Missing Git header {}",
            String::from_utf8_lossy(prefix).trim()
        )
        .into()
    })
}

fn parse_oid(value: &[u8]) -> Result<String, LitError> {
    let oid = text(value)?;
    if !valid_oid(oid) {
        return Err("Invalid SHA-1 Git object ID".into());
    }
    Ok(oid.to_owned())
}

fn identity(value: &[u8]) -> (String, i64) {
    let value = String::from_utf8_lossy(value);
    let end = value.rfind('>').map_or(value.len(), |index| index + 1);
    let (name, date) = value.split_at(end);
    (
        name.to_owned(),
        date.split_whitespace()
            .next()
            .and_then(|value| value.parse().ok())
            .unwrap_or(0),
    )
}
