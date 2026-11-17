use super::merge_worktree::{MergeWorktree, FILE};
use crate::core::merge::{FileMergeResult, FileMergeStatus, MergeStrategy};
use crate::core::{find_repo_root, read_head, Commit, Object, ObjectHash};
use crate::errors::LitError;
use crate::response::ResolveResponse;
use crate::storage::{Index, ObjectStore};
use std::fs;
use std::io::ErrorKind;
use std::path::Path;
use std::str::FromStr;

struct MergeState {
    head: ObjectHash,
    target: ObjectHash,
    conflicts: Vec<FileMergeResult>,
    // Written before publishing HEAD so continuation can be retried.
    result: Option<ObjectHash>,
    worktree: Option<MergeWorktree>,
}

impl MergeState {
    fn read(root: &Path, store: &ObjectStore) -> Result<Self, LitError> {
        let directory = root.join(".lit/merge");
        let target = read_marker(&directory, "MERGE_HEAD")?.ok_or_else(|| {
            LitError::repository("Merge state is missing MERGE_HEAD. Restore the merge metadata.")
        })?;
        let original = read_marker(&directory, "ORIG_HEAD")?;
        let result = read_marker(&directory, "RESULT")?;
        let current = ObjectHash::from_hex(read_head(root)?);
        let format = read_state_file(&directory.join("FORMAT"))?;
        let worktree = match read_state_file(&directory.join(FILE))? {
            Some(bytes) => {
                let worktree: MergeWorktree = serde_json::from_slice(&bytes).map_err(|e| {
                    LitError::repository(format!("Invalid merge worktree checkpoint: {e}"))
                })?;
                worktree.verify(root, store)?;
                Some(worktree)
            }
            None if format.is_none() => None,
            None => {
                return Err(LitError::repository(
                    "Merge worktree checkpoint is missing. Restore the merge metadata.",
                ))
            }
        };
        if let Some(format) = format {
            if format != b"2" {
                return Err(LitError::repository("Unsupported merge state format"));
            }
            if original.is_none() {
                return Err(LitError::repository("Merge state is missing ORIG_HEAD"));
            }
        }
        let conflicts = match read_state_file(&directory.join("conflicts.json"))? {
            Some(bytes) => serde_json::from_slice::<Vec<FileMergeResult>>(&bytes)
                .map_err(|e| LitError::repository(format!("Invalid merge conflicts.json: {e}")))?,
            // Older Lit removed conflicts.json when the last conflict was resolved.
            None if original.is_none() => Vec::new(),
            None => {
                return Err(LitError::repository(
                    "Merge state is missing conflicts.json. Restore the merge metadata.",
                ))
            }
        };
        let conflicts: Vec<_> = conflicts
            .into_iter()
            .filter(|entry| {
                entry.status == FileMergeStatus::Conflict
                    && worktree
                        .as_ref()
                        .is_none_or(|worktree| !worktree.resolved(&entry.path))
            })
            .collect();
        for entry in &conflicts {
            super::worktree::safe_path(root, &entry.path)?;
        }
        read_commit(store, &target)?;
        let head = if let Some(result) = &result {
            let commit = read_commit(store, result)?;
            match commit.parents.as_slice() {
                [parent, merged]
                    if merged == &target
                        && original.as_ref().is_none_or(|hash| hash == parent)
                        && (current == *result || current == *parent)
                        && conflicts.is_empty() =>
                {
                    let index = Index::load(root)?;
                    let entries = crate::core::diff::collect_tree_entries(
                        &read_tree(store, result)?,
                        store,
                        "",
                    )?;
                    if worktree.as_ref().is_none_or(|worktree| !worktree.aborting)
                        && (entries.len() != index.entries.len()
                            || entries.iter().any(|(path, entry)| {
                                index.entries.get(path).is_none_or(|saved| {
                                    saved.hash != entry.hash.as_str() || saved.mode != entry.mode
                                })
                            }))
                    {
                        return Err(LitError::repository(
                            "The index changed after preparing the merge commit. Restore the resolved index before continuing.",
                        ));
                    }
                    parent.clone()
                }
                _ => {
                    return Err(LitError::repository(
                        "Pending merge result disagrees with HEAD or merge metadata",
                    ))
                }
            }
        } else if original.as_ref().is_some_and(|hash| hash != &current) {
            return Err(LitError::repository(
                "HEAD changed during the merge. Restore the original checkout before continuing.",
            ));
        } else {
            current
        };
        read_commit(store, &head)?;
        Ok(Self {
            head,
            target,
            conflicts,
            result,
            worktree,
        })
    }
}

pub(super) fn verify_state(root: &Path, store: &ObjectStore) -> Result<Option<String>, LitError> {
    if !super::merge::in_progress(root)? {
        return Ok(None);
    }
    let state = MergeState::read(root, store)?;
    Ok(Some(format!(
        "Active merge with {} unresolved conflict(s)",
        state.conflicts.len()
    )))
}

/// Resolve conflicts with a side's version, then finalize with `lit resolve --continue`.
pub fn execute(
    file: Option<String>,
    strategy: Option<String>,
    all: bool,
    finish: bool,
) -> Result<ResolveResponse, LitError> {
    let root = find_repo_root()?;
    let _guard = super::transaction::lock_writes(&root)?;
    if !super::merge::in_progress(&root)? {
        return Err("No merge in progress. Nothing to resolve.".into());
    }
    let store = ObjectStore::new(&root);
    let mut state = MergeState::read(&root, &store)?;
    if finish {
        if state.conflicts.is_empty() && state.result.is_none() {
            prepare_worktree(&root, &store, &mut state)?;
        }
        if let Some(worktree) = &mut state.worktree {
            worktree.resume(&root, &store)?;
        }
        return finalize_merge(&root, &store, state);
    }

    let source = match strategy
        .as_deref()
        .map(MergeStrategy::from_str)
        .transpose()?
    {
        Some(MergeStrategy::Ours) => &state.head,
        Some(MergeStrategy::Theirs) => &state.target,
        Some(MergeStrategy::Recursive) => {
            return Err(
                "Cannot use 'recursive' strategy for resolve. Use 'ours' or 'theirs'.".into(),
            );
        }
        None => return Err("--strategy is required for resolve (ours or theirs)".into()),
    };
    let selected = super::merge::entries(&store, &read_tree(&store, source)?)?;
    if state.result.is_none() {
        prepare_worktree(&root, &store, &mut state)?;
    }
    if let Some(worktree) = &mut state.worktree {
        worktree.resume(&root, &store)?;
    }
    let original = super::merge::entries(&store, &read_tree(&store, &state.head)?)?;
    let (resolved, remaining): (Vec<_>, Vec<_>) = state
        .conflicts
        .into_iter()
        .partition(|conflict| all || file.as_deref() == Some(&conflict.path));
    if resolved.is_empty() {
        return Err(match file {
            Some(file) => format!("No conflict found for file '{file}'"),
            None => "No conflicts to resolve".to_owned(),
        }
        .into());
    }

    let worktree = state
        .worktree
        .as_mut()
        .ok_or("Missing merge worktree checkpoint")?;
    for conflict in &resolved {
        worktree.record_resolution(
            &conflict.path,
            original.get(&conflict.path),
            selected.get(&conflict.path),
        );
    }
    worktree.prepare(&root)?;
    worktree.verify(&root, &store)?;
    worktree.save(&root.join(".lit/merge"))?;
    worktree.resume(&root, &store)?;

    Ok(ResolveResponse {
        resolved_files: resolved.into_iter().map(|conflict| conflict.path).collect(),
        remaining_conflicts: remaining.len(),
        merge_complete: false,
        message: if remaining.is_empty() {
            "All conflicts resolved. Run 'lit resolve --continue' to finalize the merge.".into()
        } else {
            format!("{} conflict(s) remaining", remaining.len())
        },
    })
}

// Older in-flight merges kept clean paths at our version and lacked a
// reversible checkpoint. Apply the clean portion on their next resolve.
fn prepare_worktree(
    root: &Path,
    store: &ObjectStore,
    state: &mut MergeState,
) -> Result<(), LitError> {
    if state.worktree.is_some() {
        return Ok(());
    }
    let original = super::merge::entries(store, &read_tree(store, &state.head)?)?;
    let plan =
        super::merge::calculate(store, &state.head, &state.target, MergeStrategy::Recursive)?;
    let tree = super::merge::read_tree(store, &plan.tree.ok_or("Missing planned merge tree")?)?;
    let mut planned = super::merge::entries(store, &tree)?;
    for conflict in &state.conflicts {
        if let Some(entry) = original.get(&conflict.path) {
            planned.insert(conflict.path.clone(), entry.clone());
        } else {
            planned.remove(&conflict.path);
        }
    }
    let mut worktree = MergeWorktree::plan(&original, &planned, false);
    worktree.prepare(root)?;
    worktree.verify(root, store)?;
    worktree.save(&root.join(".lit/merge"))?;
    state.worktree = Some(worktree);
    Ok(())
}

pub fn abort() -> Result<ResolveResponse, LitError> {
    let root = find_repo_root()?;
    let _guard = super::transaction::lock_writes(&root)?;
    if !super::merge::in_progress(&root)? {
        return Err("No merge in progress. Nothing to abort.".into());
    }
    let store = ObjectStore::new(&root);
    let state = MergeState::read(&root, &store)?;
    if read_head(&root)? != state.head.as_str() {
        return Err("The merge commit was already published. Run 'lit resolve --continue' to finish cleanup.".into());
    }
    let mut worktree = state.worktree.filter(|worktree| worktree.abortable)
        .ok_or("This merge was started without an abort checkpoint. Save your changes and finish the merge with 'lit resolve --continue'.")?;
    worktree.abort(&root, &store)?;
    retire_state(&root, "lit merge --abort")?;
    Ok(ResolveResponse {
        resolved_files: vec![],
        remaining_conflicts: 0,
        merge_complete: false,
        message: "Merge aborted. Restored merge-owned paths and index entries.".into(),
    })
}

fn finalize_merge(
    root: &Path,
    store: &ObjectStore,
    state: MergeState,
) -> Result<ResolveResponse, LitError> {
    if !state.conflicts.is_empty() {
        return Err(format!(
            "{} unresolved conflict(s) remain. Resolve them first.",
            state.conflicts.len()
        )
        .into());
    }
    let merge_dir = root.join(".lit/merge");
    let result = match state.result {
        Some(hash) => hash,
        None => {
            let index = Index::load(root)?;
            let entries = index
                .entries
                .into_iter()
                .map(|(path, entry)| {
                    let tree_entry = crate::core::TreeEntry {
                        mode: entry.mode,
                        name: path.clone(),
                        hash: ObjectHash::from_hex(entry.hash),
                        object_type: "blob".into(),
                    };
                    (path, tree_entry)
                })
                .collect::<Vec<_>>();
            let tree = crate::core::merge::build_flat_tree(store, &entries)?;
            let author = std::env::var("USER")
                .or_else(|_| std::env::var("USERNAME"))
                .unwrap_or_else(|_| "Unknown".to_string());
            let commit = Commit::new(
                tree,
                vec![state.head, state.target],
                author,
                "Merge commit (conflicts resolved)".into(),
            );
            let result = store.write(&Object::Commit(commit))?;
            super::worktree::replace(&merge_dir.join("RESULT"), result.as_str().as_bytes())?;
            result
        }
    };
    if read_head(root)? != result.as_str() {
        super::merge::publish_head(root, &result)?;
    }
    retire_state(root, "lit resolve --continue")?;
    Ok(ResolveResponse {
        resolved_files: vec![],
        remaining_conflicts: 0,
        merge_complete: true,
        message: format!("Merge complete: {}", result.short()),
    })
}

fn retire_state(root: &Path, retry: &str) -> Result<(), LitError> {
    let cleanup = tempfile::tempdir_in(root.join(".lit")).map_err(|e| e.to_string())?;
    fs::rename(root.join(".lit/merge"), cleanup.path().join("merge"))
        .map_err(|e| format!("Could not retire merge state. Retry '{retry}': {e}").into())
}

fn read_state_file(path: &Path) -> Result<Option<Vec<u8>>, LitError> {
    match path.symlink_metadata() {
        Ok(meta) if meta.is_file() => fs::read(path)
            .map(Some)
            .map_err(|e| LitError::io(format!("Cannot read merge state {}: {e}", path.display()))),
        Ok(_) => Err(LitError::repository(format!(
            "Expected regular merge state file {}",
            path.display()
        ))),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
        Err(e) => Err(LitError::io(format!(
            "Cannot inspect merge state {}: {e}",
            path.display()
        ))),
    }
}

fn read_marker(directory: &Path, name: &str) -> Result<Option<ObjectHash>, LitError> {
    read_state_file(&directory.join(name))?
        .map(|bytes| {
            let hash = std::str::from_utf8(&bytes).unwrap_or_default().trim();
            if !ObjectHash::is_full_hex(hash) {
                return Err(LitError::repository(format!(
                    "Invalid object ID in merge {name}"
                )));
            }
            Ok(ObjectHash::from_hex(hash.to_owned()))
        })
        .transpose()
}

fn read_commit(store: &ObjectStore, hash: &ObjectHash) -> Result<Commit, LitError> {
    if let Ok(Object::Commit(commit)) = store.read(hash) {
        if Object::Commit(commit.clone()).hash() == *hash {
            return Ok(commit);
        }
    }
    Err(LitError::repository(format!(
        "Merge state references an invalid commit {hash}"
    )))
}

fn read_tree(store: &ObjectStore, commit: &ObjectHash) -> Result<crate::core::Tree, LitError> {
    match store.read(&read_commit(store, commit)?.tree)? {
        Object::Tree(tree) => Ok(tree),
        _ => Err("Expected a tree for the merge parent".into()),
    }
}
