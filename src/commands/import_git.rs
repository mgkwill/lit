use super::git_interop::{checked_output, git, git_oid, text, translate, valid_oid, GitObject};
use crate::core::{find_repo_root, ObjectHash};
use crate::errors::LitError;
use crate::response::ImportGitResponse;
use crate::storage::ObjectStore;
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Stdio};

pub fn execute(source: String) -> Result<ImportGitResponse, LitError> {
    execute_at(source, None)
}

pub(super) fn execute_at(
    source: String,
    destination_root: Option<&Path>,
) -> Result<ImportGitResponse, LitError> {
    let source_path =
        fs::canonicalize(&source).map_err(|e| format!("Open Git source {source}: {e}"))?;
    if !source_path.join(".git").exists() && !source_path.join("HEAD").is_file() {
        return Err(format!("Not a Git repository: {source}").into());
    }
    let format = checked_output(git(&source_path).args(["rev-parse", "--show-object-format"]))?;
    if text(&format.stdout)?.trim() != "sha1" {
        return Err("Only SHA-1 Git repositories are supported".into());
    }

    let refs_output = checked_output(git(&source_path).args([
        "for-each-ref",
        "--format=%(objectname)%09%(refname)%09%(symref)",
        "refs/heads",
        "refs/tags",
    ]))?;
    if !refs_output.stderr.is_empty() {
        return Err(format!(
            "Git could not enumerate every branch and tag: {}",
            String::from_utf8_lossy(&refs_output.stderr).trim()
        )
        .into());
    }
    let mut refs = Vec::new();
    for line in text(&refs_output.stdout)?.lines() {
        let mut fields = line.split('\t');
        let oid = fields.next().ok_or("Missing Git ref object ID")?;
        let name = fields.next().ok_or("Missing Git ref name")?;
        let symref = fields.next().ok_or("Missing Git ref type")?;
        if !symref.is_empty() {
            return Err(format!("Symbolic Git ref {name} is unsupported outside HEAD").into());
        }
        if !valid_oid(oid) {
            return Err(format!("Invalid object ID for Git ref {name}").into());
        }
        refs.push((name, oid));
    }

    let head_path = checked_output(git(&source_path).args([
        "rev-parse",
        "--path-format=absolute",
        "--git-path",
        "HEAD",
    ]))?;
    let head_file = fs::read_to_string(text(&head_path.stdout)?.trim_end_matches(['\r', '\n']))
        .map_err(|e| format!("Read Git HEAD: {e}"))?;
    let head = head_file.trim_end_matches(['\r', '\n']);
    let symbolic_head = head.strip_prefix("ref: ");
    match symbolic_head {
        Some(reference) if reference.starts_with("refs/heads/") => {
            checked_output(git(&source_path).args(["check-ref-format", reference]))?;
        }
        None if valid_oid(head) => {}
        _ => return Err("Unsupported Git HEAD format".into()),
    }

    let mut roots: Vec<_> = refs.iter().map(|(_, oid)| (*oid).to_owned()).collect();
    if symbolic_head.is_none() {
        roots.push(head.into());
    }

    let repo_root = if let Some(root) = destination_root {
        root.to_owned()
    } else {
        match find_repo_root() {
            Ok(root) => root,
            Err(_) => {
                super::init::execute(false, None)?;
                find_repo_root()?
            }
        }
    };
    let _guard = super::transaction::lock_writes(&repo_root)?;
    super::merge::require_inactive(&repo_root)?;
    if repo_root.join(".lit/transaction.json").exists() {
        return Err("Finish the active Lit transaction before importing Git history".into());
    }
    let (objects, _) = read_objects(&source_path, &repo_root, roots, true)?;

    let destination = repo_root.join(".lit");
    // Prepare every update before publishing any ref. Each replacement is atomic
    // and references objects already in the store, so a failed publish can be retried.
    let staging = tempfile::tempdir_in(&destination)
        .map_err(|e| format!("Create Git import staging directory: {e}"))?;
    let mut updates = Vec::new();
    for (name, oid) in &refs {
        let target = objects.get(*oid).ok_or("Missing imported ref target")?;
        updates.push(stage(&destination, staging.path(), name, target.as_str())?);
    }
    let head = match symbolic_head {
        Some(_) => head.to_owned(),
        None => objects
            .get(head)
            .ok_or("Missing imported HEAD target")?
            .to_string(),
    };
    updates.push(stage(&destination, staging.path(), "HEAD", &head)?);
    for (temporary, path) in updates {
        fs::rename(temporary, &path).map_err(|e| format!("Update {}: {e}", path.display()))?;
    }

    let gitignore = source_path.join(".gitignore");
    let litignore = repo_root.join(".litignore");
    if gitignore.is_file() && !litignore.exists() {
        let _ = fs::copy(gitignore, litignore);
    }

    let objects_imported = objects.len() as u64;
    let refs_imported = refs.len() as u64;
    Ok(ImportGitResponse {
        source,
        objects_imported,
        refs_imported,
        hash_mapping_count: objects.len(),
        message: format!(
            "Imported {objects_imported} objects and {refs_imported} refs from Git repository"
        ),
    })
}

/// Import immutable object data without publishing refs.
pub(super) fn read_objects(
    source: &Path,
    root: &Path,
    mut roots: Vec<String>,
    include_unreachable: bool,
) -> Result<(HashMap<String, ObjectHash>, usize), LitError> {
    if include_unreachable {
        let all = checked_output(git(source).args([
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname)",
        ]))?;
        roots.extend(text(&all.stdout)?.lines().map(str::to_owned));
    }
    let store = ObjectStore::new(root);
    let git_map = root.join(".lit/git-export-map");
    let mut reader = ObjectReader::new(source)?;
    let mut written = 0;
    let objects = translate(
        roots,
        |oid| {
            let object = reader.read(oid)?;
            let dependencies = object.dependencies()?;
            Ok(((oid.to_owned(), object), dependencies))
        },
        |(oid, object), objects| {
            match fs::read_to_string(git_map.join(&oid)) {
                Ok(hash) => {
                    let hash = hash.trim();
                    if hash.len() == 192 && hash.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                        let hash = ObjectHash::from_hex(hash.to_owned());
                        if store.exists(&hash) {
                            return Ok(hash);
                        }
                    }
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => return Err(format!("Read Git identity mapping: {error}").into()),
            }
            let lit = object.to_lit(objects)?;
            let hash = lit.hash();
            if !store.exists(&hash) {
                store.write(&lit)?;
                written += 1;
            }
            Ok(hash)
        },
    )?;
    Ok((objects, written))
}

fn stage(
    root: &Path,
    staging: &Path,
    name: &str,
    value: &str,
) -> Result<(PathBuf, PathBuf), LitError> {
    let mut path = root.to_path_buf();
    for part in Path::new(name).components() {
        let std::path::Component::Normal(part) = part else {
            return Err(format!("Invalid Lit ref path: {name}").into());
        };
        path.push(part);
        match fs::symlink_metadata(&path) {
            Ok(metadata) if metadata.is_symlink() => {
                return Err(format!("Ref path traverses a symlink: {name}").into());
            }
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(format!("Read ref path {name}: {e}").into()),
        }
    }
    if path.is_dir() {
        return Err(format!("Cannot replace existing ref directory: {name}").into());
    }
    let parent = path.parent().ok_or("Ref path has no parent")?;
    fs::create_dir_all(parent).map_err(|e| format!("Create ref directory for {name}: {e}"))?;
    // Mirror the ref names on the same filesystem to catch pathname aliases
    // (for example case-only differences) before updating the actual refs.
    let temporary = staging.join(name);
    let temporary_parent = temporary.parent().ok_or("Ref staging path has no parent")?;
    fs::create_dir_all(temporary_parent)
        .map_err(|e| format!("Stage ref directory for {name}: {e}"))?;
    let mut file =
        fs::File::create_new(&temporary).map_err(|e| format!("Stage ref {name}: {e}"))?;
    writeln!(file, "{value}").map_err(|e| format!("Stage ref {name}: {e}"))?;
    Ok((temporary, path))
}

struct ObjectReader {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}

impl ObjectReader {
    fn new(source: &Path) -> Result<Self, LitError> {
        let mut child = git(source)
            .args(["cat-file", "--batch"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("Start Git object reader: {e}"))?;
        let input = child.stdin.take().expect("Git has piped stdin");
        let output = BufReader::new(child.stdout.take().expect("Git has piped stdout"));
        Ok(Self {
            child,
            input,
            output,
        })
    }

    fn read(&mut self, oid: &str) -> Result<GitObject, LitError> {
        if !valid_oid(oid) {
            return Err("Invalid Git object ID".into());
        }
        writeln!(self.input, "{oid}").map_err(|e| format!("Request Git object {oid}: {e}"))?;
        let mut header = String::new();
        self.output
            .read_line(&mut header)
            .map_err(|e| format!("Read Git object header: {e}"))?;
        let fields: Vec<_> = header.split_ascii_whitespace().collect();
        let [reported_oid, kind, len] = fields.as_slice() else {
            return Err(format!("Git object {oid} is missing or unreadable").into());
        };
        if *reported_oid != oid {
            return Err(format!("Git returned an unexpected object for {oid}").into());
        }
        let length = len
            .parse::<usize>()
            .map_err(|_| "Invalid size in Git object header")?;
        let mut body = Vec::new();
        (&mut self.output)
            .take(length as u64)
            .read_to_end(&mut body)
            .map_err(|e| format!("Read Git object {oid}: {e}"))?;
        let mut newline = [0];
        if body.len() != length
            || self.output.read_exact(&mut newline).is_err()
            || newline[0] != b'\n'
        {
            return Err(format!("Truncated Git object {oid}").into());
        }
        if git_oid(kind, &body) != oid {
            return Err(format!("Git object {oid} failed its content hash check").into());
        }
        Ok(GitObject {
            kind: (*kind).to_owned(),
            body,
        })
    }
}

impl Drop for ObjectReader {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
