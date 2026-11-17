use super::merge_worktree::MergeWorktree;
use crate::core::merge::{find_merge_base, is_ancestor, merge_trees, MergeResult, MergeStrategy};
use crate::core::{
    find_repo_root, get_current_branch, read_head, read_ref, Commit, Object, ObjectHash,
};
use crate::errors::LitError;
use crate::response::MergeResponse;
use crate::storage::ObjectStore;
use std::fs;
use std::io::ErrorKind;
use std::path::Path;
use std::str::FromStr;

pub(super) fn in_progress(root: &Path) -> Result<bool, LitError> {
    match root.join(".lit/merge").symlink_metadata() {
        Ok(meta) if meta.is_dir() => Ok(true),
        Ok(_) => Err(LitError::repository(
            "Expected .lit/merge to be a directory",
        )),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(false),
        Err(e) => Err(LitError::io(format!("Cannot inspect .lit/merge: {e}"))),
    }
}

pub(super) fn require_inactive(root: &Path) -> Result<(), LitError> {
    if in_progress(root)? {
        return Err("A merge is in progress. Resolve the conflicts, then run 'lit resolve --continue', or use 'lit merge --abort'.".into());
    }
    Ok(())
}

pub(super) fn publish_head(root: &Path, hash: &ObjectHash) -> Result<(), LitError> {
    let metadata = root.join(".lit");
    let path = match get_current_branch(root) {
        Ok(branch) => {
            super::remote::validate_ref(&branch)?;
            metadata.join("refs/heads").join(branch)
        }
        Err(_) => metadata.join("HEAD"),
    };
    let bytes = crate::core::refs::encode_ref_file(root, &path, &format!("{hash}\n"))?;
    super::worktree::replace(&path, &bytes)
}

pub fn execute(
    branch: String,
    strategy: Option<String>,
) -> Result<MergeResponse, crate::errors::LitError> {
    let repo_root = find_repo_root()?;
    let _guard = super::transaction::lock_writes(&repo_root)?;
    require_inactive(&repo_root)?;
    let target_hash = read_ref(&repo_root, &format!("heads/{branch}"))
        .or_else(|_| read_ref(&repo_root, &format!("remotes/{branch}")))?;
    execute_target(&repo_root, branch, target_hash, strategy)
}

pub fn abort() -> Result<MergeResponse, LitError> {
    let response = super::resolve::abort()?;
    Ok(MergeResponse {
        merged: false,
        fast_forward: false,
        commit_hash: None,
        message: response.message,
        has_conflicts: false,
        file_results: vec![],
        strategy: "abort".into(),
    })
}

pub(super) fn execute_target(
    repo_root: &std::path::Path,
    branch: String,
    target_hash: String,
    strategy: Option<String>,
) -> Result<MergeResponse, crate::errors::LitError> {
    if repo_root
        .join(".lit/transaction.json")
        .try_exists()
        .map_err(|e| LitError::io(e.to_string()))?
    {
        return Err("Finish the active transaction before merging".into());
    }
    let store = ObjectStore::new(repo_root);
    let strategy = match strategy {
        Some(strategy) => MergeStrategy::from_str(&strategy)?,
        None => MergeStrategy::Recursive,
    };
    let head_hash = ObjectHash::from_hex(read_head(repo_root)?);
    let target_hash = ObjectHash::from_hex(target_hash);

    // Check if already up to date
    if head_hash.to_string() == target_hash.to_string() {
        return Ok(MergeResponse {
            merged: true,
            fast_forward: false,
            commit_hash: None,
            message: "Already up to date.".to_string(),
            has_conflicts: false,
            file_results: vec![],
            strategy: format!("{:?}", strategy).to_lowercase(),
        });
    }

    // Check for fast-forward: if HEAD is an ancestor of target
    if is_ancestor(&store, &head_hash, &target_hash)? {
        super::worktree::install_merge(repo_root, head_hash.as_str(), target_hash.as_str())?;

        return Ok(MergeResponse {
            merged: true,
            fast_forward: true,
            commit_hash: Some(target_hash.to_string()),
            message: format!("Fast-forward merge to {}", target_hash.short()),
            has_conflicts: false,
            file_results: vec![],
            strategy: format!("{:?}", strategy).to_lowercase(),
        });
    }

    // Check if target is ancestor of HEAD (already merged)
    if is_ancestor(&store, &target_hash, &head_hash)? {
        return Ok(MergeResponse {
            merged: true,
            fast_forward: false,
            commit_hash: None,
            message: "Already up to date.".to_string(),
            has_conflicts: false,
            file_results: vec![],
            strategy: format!("{:?}", strategy).to_lowercase(),
        });
    }

    let merge_result = calculate(&store, &head_hash, &target_hash, strategy)?;
    let tree_hash = merge_result.tree.ok_or("Merge failed to produce a tree")?;

    let file_results: Vec<crate::response::FileMergeInfo> = merge_result
        .file_results
        .iter()
        .map(|r| crate::response::FileMergeInfo {
            path: r.path.clone(),
            status: format!("{:?}", r.status).to_lowercase(),
            conflict_count: r.conflicts.len(),
        })
        .collect();

    if merge_result.has_conflicts && strategy == MergeStrategy::Recursive {
        // Publish the complete state with a single directory rename.
        let original = entries(&store, &get_commit_tree(&store, &head_hash)?)?;
        let planned = entries(&store, &read_tree(&store, &tree_hash)?)?;
        let mut worktree = MergeWorktree::plan(&original, &planned, true);
        worktree.prepare(repo_root)?;
        worktree.verify(repo_root, &store)?;

        let metadata = repo_root.join(".lit");
        let staging = tempfile::tempdir_in(&metadata).map_err(|e| e.to_string())?;
        let state = staging.path().join("merge");
        fs::create_dir(&state).map_err(|e| e.to_string())?;
        let conflicts = serde_json::to_vec_pretty(&merge_result.file_results)
            .map_err(|e| format!("Failed to serialize conflicts: {e}"))?;
        super::worktree::replace(&state.join("conflicts.json"), &conflicts)?;
        super::worktree::replace(&state.join("MERGE_HEAD"), target_hash.as_str().as_bytes())?;
        super::worktree::replace(&state.join("ORIG_HEAD"), head_hash.as_str().as_bytes())?;
        worktree.save(&state)?;
        super::worktree::replace(&state.join("FORMAT"), b"2")?;
        fs::rename(&state, metadata.join("merge"))
            .map_err(|e| format!("Failed to publish merge state: {e}"))?;
        worktree.resume(repo_root, &store)?;

        let conflict_count: usize = merge_result
            .file_results
            .iter()
            .filter(|r| r.status == crate::core::merge::FileMergeStatus::Conflict)
            .count();

        return Ok(MergeResponse {
            merged: false,
            fast_forward: false,
            commit_hash: None,
            message: format!(
                "CONFLICT: Merge of '{}' has {} conflicting file(s). Use 'lit resolve' to resolve.",
                branch, conflict_count
            ),
            has_conflicts: true,
            file_results,
            strategy: format!("{:?}", strategy).to_lowercase(),
        });
    }

    // Create merge commit.
    let author = std::env::var("USER")
        .or_else(|_| std::env::var("USERNAME"))
        .unwrap_or_else(|_| "Unknown".to_string());

    let commit = Commit::new(
        tree_hash,
        vec![head_hash.clone(), target_hash],
        author,
        format!("Merge branch '{}'", branch),
    );

    let commit_obj = Object::Commit(commit);
    let commit_hash = store.write(&commit_obj)?;

    super::worktree::install_merge(repo_root, head_hash.as_str(), commit_hash.as_str())?;

    Ok(MergeResponse {
        merged: true,
        fast_forward: false,
        commit_hash: Some(commit_hash.to_string()),
        message: format!("Merge branch '{}' ({})", branch, commit_hash.short()),
        has_conflicts: false,
        file_results,
        strategy: format!("{:?}", strategy).to_lowercase(),
    })
}

pub(super) fn calculate(
    store: &ObjectStore,
    head: &ObjectHash,
    target: &ObjectHash,
    strategy: MergeStrategy,
) -> Result<MergeResult, LitError> {
    let base = find_merge_base(store, head, target)?
        .map(|hash| get_commit_tree(store, &hash))
        .transpose()?;
    Ok(merge_trees(
        store,
        base.as_ref(),
        &get_commit_tree(store, head)?,
        &get_commit_tree(store, target)?,
        strategy,
    )?)
}

pub(super) fn entries(
    store: &ObjectStore,
    tree: &crate::core::Tree,
) -> Result<std::collections::HashMap<String, crate::core::TreeEntry>, LitError> {
    Ok(crate::core::diff::collect_tree_entries(tree, store, "")?
        .into_iter()
        .collect())
}

pub(super) fn read_tree(
    store: &ObjectStore,
    hash: &ObjectHash,
) -> Result<crate::core::Tree, LitError> {
    match store.read(hash)? {
        Object::Tree(tree) => Ok(tree),
        _ => Err(format!("Expected tree object for {hash}").into()),
    }
}

pub(super) fn get_commit_tree(
    store: &ObjectStore,
    commit_hash: &ObjectHash,
) -> Result<crate::core::Tree, LitError> {
    let commit = match store.read(commit_hash)? {
        Object::Commit(c) => c,
        _ => return Err(format!("Expected commit object for {}", commit_hash).into()),
    };
    read_tree(store, &commit.tree)
}
