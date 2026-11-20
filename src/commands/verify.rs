use crate::core::{find_repo_root, Object, ObjectHash, TreeEntry};
use crate::response::{VerifyResponse, VerifyResult};
use crate::storage::ObjectStore;

use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::Path;
use std::{fs, io};

pub fn execute() -> Result<VerifyResponse, crate::errors::LitError> {
    Ok(at(&find_repo_root()?, None))
}

pub(super) fn at(repo_root: &Path, legacy_refs: Option<&BTreeMap<String, String>>) -> VerifyResponse {
    let store = ObjectStore::new(repo_root);

    let mut checks = Vec::new();
    let (objects_checked, graph) = verify_objects(&store, legacy_refs.is_some(), &mut checks);
    let (refs_checked, roots) = verify_refs(repo_root, &graph, legacy_refs, &mut checks);
    verify_dag(&graph, &roots, &mut checks);

    let index =
        verify_index(repo_root, &graph).map_err(|error| error.internal_message().to_owned());
    checks.push(check("index", index));

    for (name, result) in [
        (
            "merge_state",
            super::resolve::verify_state(repo_root, &store),
        ),
        (
            "transaction_journal",
            super::transaction::verify_state(repo_root, &store),
        ),
    ] {
        checks.push(check(
            name,
            result
                .map(|details| details.unwrap_or_else(|| "No active operation".into()))
                .map_err(|error| error.internal_message().to_owned()),
        ));
    }

    let all_valid = checks.iter().all(|check| check.status == "ok");
    let message = if all_valid {
        "Repository is valid".to_string()
    } else {
        "Repository has errors".to_string()
    };

    VerifyResponse {
        valid: all_valid,
        checks,
        objects_checked,
        refs_checked,
        message,
    }
}

enum ObjectLinks {
    Blob,
    Tree(Vec<TreeEntry>),
    Commit {
        tree: ObjectHash,
        parents: Vec<ObjectHash>,
    },
    Tag {
        target: ObjectHash,
        target_type: String,
    },
}

impl ObjectLinks {
    fn kind(&self) -> &'static str {
        match self {
            Self::Blob => "blob",
            Self::Tree(_) => "tree",
            Self::Commit { .. } => "commit",
            Self::Tag { .. } => "tag",
        }
    }
}

fn verify_objects(
    store: &ObjectStore,
    migration: bool,
    checks: &mut Vec<VerifyResult>,
) -> (usize, HashMap<ObjectHash, ObjectLinks>) {
    let mut graph = HashMap::new();
    let mut seen = HashSet::new();
    let before = checks.len();
    let scan = store.visit_verified(migration, |hash, object| {
        seen.insert(hash.clone());
        let links = match object {
            Ok(Object::Blob(_)) => ObjectLinks::Blob,
            Ok(Object::Tree(tree)) => ObjectLinks::Tree(tree.entries),
            Ok(Object::Commit(commit)) => ObjectLinks::Commit {
                tree: commit.tree,
                parents: commit.parents,
            },
            Ok(Object::Tag(tag)) => ObjectLinks::Tag {
                target: tag.target,
                target_type: tag.target_type,
            },
            Err(error) => {
                checks.push(check(format!("object:{}", hash.short()), Err(error)));
                return;
            }
        };
        graph.insert(hash.clone(), links);
    });
    match scan {
        Ok(errors) => {
            for error in errors {
                checks.push(check("pack_index", Err(error)));
            }
        }
        Err(error) => checks.push(check("object_store", Err(error))),
    }
    let count = seen.len();
    if before == checks.len() {
        checks.push(check(
            "object_hashes",
            Ok(format!("All {count} objects verified")),
        ));
    }
    (count, graph)
}

fn verify_refs(
    repo_root: &Path,
    graph: &HashMap<ObjectHash, ObjectLinks>,
    legacy_refs: Option<&BTreeMap<String, String>>,
    checks: &mut Vec<VerifyResult>,
) -> (usize, Vec<ObjectHash>) {
    let before = checks.len();
    let metadata = repo_root.join(".lit");
    let refs_dir = metadata.join("refs");
    let mut roots = Vec::new();
    let mut count = 0;
    if let Some(refs) = legacy_refs {
        for (name, value) in refs {
            count += 1;
            match verify_ref_target(value, name, graph) {
                Ok(hash) => roots.push(hash),
                Err(error) => checks.push(check(format!("ref:{name}"), Err(error))),
            }
        }
    } else {
        let path = repo_root.join(crate::core::repository_format::LEGACY_REFS_FILE);
        match path.symlink_metadata() {
            Ok(_) => checks.push(check("refs", Err("Packed encrypted refs require 'lit migrate-repo'".into()))),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => checks.push(check("refs", Err(error.to_string()))),
        }
    }
    match refs_dir.symlink_metadata() {
        Ok(meta) if meta.is_dir() => {
            for entry in walkdir::WalkDir::new(&refs_dir)
                .min_depth(1)
                .sort_by_file_name()
            {
                let entry = match entry {
                    Ok(entry) if !entry.file_type().is_dir() => entry,
                    Ok(_) => continue,
                    Err(error) => {
                        checks.push(check(
                            "refs",
                            Err(format!("Cannot enumerate refs: {error}")),
                        ));
                        continue;
                    }
                };
                let name = entry
                    .path()
                    .strip_prefix(&refs_dir)
                    .expect("walked below refs directory")
                    .to_string_lossy();
                #[cfg(windows)]
                let name = name.replace('\\', "/");
                if let Some(packed) = legacy_refs.and_then(|refs| refs.get::<str>(&name)) {
                    match read_regular_ref(repo_root, entry.path()) {
                        Ok(loose) if loose.trim() == packed => {}
                        Ok(_) => checks.push(check(format!("ref:{name}"), Err("Conflicting loose and encrypted packed refs".into()))),
                        Err(error) => checks.push(check(format!("ref:{name}"), Err(error.to_string()))),
                    }
                    continue;
                }
                count += 1;
                let result = read_ref_root(repo_root, entry.path(), &name, graph);
                match result {
                    Ok(hash) => roots.push(hash),
                    Err(error) => checks.push(check(format!("ref:{name}"), Err(error))),
                }
            }
        }
        Ok(_) => checks.push(check(
            "refs",
            Err("Expected .lit/refs to be a directory".into()),
        )),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => checks.push(check("refs", Err(format!("Cannot inspect refs: {error}")))),
    }

    match read_regular_ref(repo_root, &metadata.join("HEAD")) {
        Ok(contents) => {
            let head = contents.trim();
            if let Some(branch) = head.strip_prefix("ref: refs/heads/") {
                // A symbolic HEAD can name an unborn branch, including in a repo with other refs.
                if let Err(error) = super::remote::validate_ref(branch) {
                    checks.push(check("ref:HEAD", Err(error.to_string())));
                }
            } else {
                count += 1;
                let hash = ObjectHash::from_hex(head.to_owned());
                match check_target(graph, &hash, Some("commit")) {
                    Ok(()) => roots.push(hash),
                    Err(error) => checks.push(check("ref:HEAD", Err(error))),
                }
            }
        }
        Err(error)
            if error.kind() == io::ErrorKind::NotFound
                && fs::read_to_string(metadata.join("config")).is_ok_and(|config| {
                    config.lines().any(|line| line.trim() == "bare = true")
                }) => {}
        Err(error) => checks.push(check("ref:HEAD", Err(format!("Cannot read HEAD: {error}")))),
    }
    if before == checks.len() {
        checks.push(check("refs", Ok(format!("All {count} refs valid"))));
    }
    (count, roots)
}

fn read_ref_root(
    repo: &Path,
    path: &Path,
    name: &str,
    graph: &HashMap<ObjectHash, ObjectLinks>,
) -> Result<ObjectHash, String> {
    let value = read_regular_ref(repo, path).map_err(|error| format!("Cannot read ref: {error}"))?;
    verify_ref_target(&value, name, graph)
}

fn verify_ref_target(
    value: &str,
    name: &str,
    graph: &HashMap<ObjectHash, ObjectLinks>,
) -> Result<ObjectHash, String> {
    let hash = ObjectHash::from_hex(value.trim().to_owned());
    let expected = (name.starts_with("heads/")
        || name.starts_with("remotes/")
        || name == super::worktree::WORKSPACE_BASE_REF)
        .then_some("commit");
    check_target(graph, &hash, expected)
        .map_err(|error| format!("Dangling or invalid reference: {error}"))?;
    Ok(hash)
}

fn read_regular_ref(repo: &Path, path: &Path) -> io::Result<String> {
    if !path.symlink_metadata()?.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "expected a regular file",
        ));
    }
    crate::core::refs::read_ref_file(repo, path)
}

fn verify_dag(
    graph: &HashMap<ObjectHash, ObjectLinks>,
    roots: &[ObjectHash],
    checks: &mut Vec<VerifyResult>,
) {
    let mut queue: Vec<_> = roots.iter().collect();
    let mut seen = HashSet::new();
    let before = checks.len();
    while let Some(hash) = queue.pop() {
        if !seen.insert(hash) {
            continue;
        }
        let mut follow = |target, expected: Result<&str, &str>, relation: String| {
            let result = expected
                .map_err(str::to_owned)
                .and_then(|kind| check_target(graph, target, Some(kind)));
            match result {
                Ok(()) => queue.push(target),
                Err(error) => checks.push(check(
                    format!("link:{}", hash.short()),
                    Err(format!("{relation}: {error}")),
                )),
            }
        };

        match &graph[hash] {
            ObjectLinks::Blob => {}
            ObjectLinks::Commit { tree, parents } => {
                follow(tree, Ok("tree"), "Commit tree".into());
                for parent in parents {
                    follow(parent, Ok("commit"), "Commit parent".into());
                }
            }
            ObjectLinks::Tree(entries) => {
                for entry in entries {
                    let expected = match (entry.mode.as_str(), entry.object_type.as_str()) {
                        ("040000", "tree") => Ok("tree"),
                        ("100644" | "100755" | "120000", "blob") => Ok("blob"),
                        // Git links name objects in a different repository.
                        ("160000", "gitlink")
                            if entry.hash.len() == 40
                                && entry
                                    .hash
                                    .as_str()
                                    .bytes()
                                    .all(|byte| byte.is_ascii_hexdigit()) =>
                        {
                            continue
                        }
                        _ => Err("Invalid mode, kind or Git link ID"),
                    };
                    follow(
                        &entry.hash,
                        expected,
                        format!("Tree entry {:?}", entry.name),
                    );
                }
            }
            ObjectLinks::Tag {
                target,
                target_type,
            } => {
                follow(target, Ok(target_type), "Annotated tag target".into());
            }
        }
    }

    let errors = checks.len() - before;
    let details = if errors == 0 {
        Ok(format!(
            "All reachable links verified ({} objects)",
            seen.len()
        ))
    } else {
        Err(format!("{errors} broken reachable link(s)"))
    };
    checks.push(check("dag_connectivity", details));
}

fn check_target(
    graph: &HashMap<ObjectHash, ObjectLinks>,
    hash: &ObjectHash,
    expected: Option<&str>,
) -> Result<(), String> {
    if !ObjectHash::is_full_hex(hash.as_str()) {
        return Err(format!("Invalid object ID {:?}", hash.short()));
    }
    let kind = graph
        .get(hash)
        .ok_or_else(|| format!("Object {hash} is missing or corrupt"))?
        .kind();
    if let Some(expected) = expected.filter(|expected| *expected != kind) {
        return Err(format!("Object {hash} is a {kind}, expected {expected}"));
    }
    Ok(())
}

fn check(name: impl Into<String>, result: Result<String, String>) -> VerifyResult {
    let (status, details) = match result {
        Ok(details) => ("ok", details),
        Err(details) => ("error", details),
    };
    VerifyResult {
        check: name.into(),
        status: status.into(),
        details: Some(details),
    }
}

fn verify_index(
    repo_root: &Path,
    graph: &HashMap<ObjectHash, ObjectLinks>,
) -> Result<String, crate::errors::LitError> {
    let index = crate::storage::Index::load(repo_root)?;
    check_index_entries(&index, |hash| {
        matches!(graph.get(hash), Some(ObjectLinks::Blob))
    })?;
    Ok(format!(
        "All {} index entries reference valid blobs",
        index.entries.len()
    ))
}

pub(super) fn validate_index_entries(
    index: &crate::storage::Index,
    store: &ObjectStore,
) -> Result<(), String> {
    check_index_entries(index, |hash| {
        matches!(store.read_verified(hash), Ok(Object::Blob(_)))
    })
}

fn check_index_entries(
    index: &crate::storage::Index,
    valid_blob: impl Fn(&ObjectHash) -> bool,
) -> Result<(), String> {
    for (path, entry) in &index.entries {
        if path != &entry.path || !ObjectHash::is_full_hex(&entry.hash) {
            return Err(format!("Malformed index entry for {}", entry.path));
        }
        let hash = ObjectHash::from_hex(entry.hash.clone());
        if !valid_blob(&hash) {
            return Err(format!(
                "Index entry for {} does not reference a valid blob {hash}",
                entry.path
            ));
        }
    }
    Ok(())
}
