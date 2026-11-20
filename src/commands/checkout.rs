use crate::core::{find_repo_root, read_ref, update_head, write_ref, Object, ObjectHash};
use crate::response::CheckoutResponse;
use crate::storage::{Index, ObjectStore};
use std::fs;
use std::path::Path;

pub fn execute(
    target: String,
    create_new: bool,
) -> Result<CheckoutResponse, crate::errors::LitError> {
    let repo_root = find_repo_root()?;
    let _guard = super::transaction::lock_writes(&repo_root)?;
    super::merge::require_inactive(&repo_root)?;
    super::transaction::require_inactive(&repo_root, "checking out another revision")?;

    if create_new {
        use crate::core::read_head;
        super::remote::validate_ref(&target)?;
        let head_hash = read_head(&repo_root)?;
        checkout_commit(&repo_root, &ObjectHash::from_hex(head_hash.clone()))?;
        write_ref(&repo_root, &format!("heads/{}", target), &head_hash)?;
        update_head(&repo_root, &target)?;
        Ok(CheckoutResponse {
            target,
            is_new_branch: true,
            is_detached: false,
        })
    } else if let Ok(commit_hash) = read_ref(&repo_root, &format!("heads/{}", target)) {
        checkout_commit(&repo_root, &ObjectHash::from_hex(commit_hash))?;
        update_head(&repo_root, &target)?;
        Ok(CheckoutResponse {
            target,
            is_new_branch: false,
            is_detached: false,
        })
    } else {
        let hash = ObjectHash::from_hex(target.clone());
        checkout_commit(&repo_root, &hash)?;
        use crate::core::set_head_detached;
        set_head_detached(&repo_root, &target)?;
        Ok(CheckoutResponse {
            target,
            is_new_branch: false,
            is_detached: true,
        })
    }
}

fn checkout_commit(
    repo_root: &Path,
    commit_hash: &ObjectHash,
) -> Result<(), crate::errors::LitError> {
    let store = ObjectStore::new(repo_root);

    // Read commit
    let commit = match store.read(commit_hash)? {
        Object::Commit(c) => c,
        _ => return Err("Not a commit".into()),
    };

    // Read tree
    let tree = match store.read(&commit.tree)? {
        Object::Tree(t) => t,
        _ => return Err("Not a tree".into()),
    };

    let mut index = Index::new();
    let staging = tempfile::tempdir_in(repo_root.join(".lit")).map_err(|e| e.to_string())?;
    let entries = crate::core::diff::collect_tree_entries(&tree, &store, "")?;
    let mut files = Vec::with_capacity(entries.len());
    for (number, (path, entry)) in entries.into_iter().enumerate() {
        let target = super::worktree::safe_path(repo_root, &path)?;
        if target.symlink_metadata().is_ok_and(|meta| meta.is_dir()) {
            return Err(format!("Cannot replace checkout directory {path}").into());
        }
        let Object::Blob(blob) = store.read(&entry.hash)? else {
            return Err(format!("Expected a blob for {path}").into());
        };
        let prepared = staging.path().join(number.to_string());
        super::worktree::materialize(&prepared, &entry.mode, &blob.content)?;
        files.push((path.clone(), prepared));
        index.add(path, entry.hash.to_string(), entry.mode);
    }
    for (path, prepared) in files {
        let target = super::worktree::safe_path(repo_root, &path)?;
        fs::create_dir_all(target.parent().ok_or("Checkout path has no parent")?)
            .map_err(|e| e.to_string())?;
        // Renaming replaces the directory entry without following its old symlink.
        fs::rename(prepared, target).map_err(|e| e.to_string())?;
    }
    index.save(repo_root)?;
    Ok(())
}
