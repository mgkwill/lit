use crate::core::{find_repo_root, get_current_branch, read_head, read_ref, ObjectHash};
use crate::errors::LitError;
use crate::response::PullResponse;
use crate::storage::ObjectStore;

pub fn execute(remote: String, branch: String) -> Result<PullResponse, LitError> {
    let root = find_repo_root()?;
    let _lock = super::transaction::lock_writes(&root)?;
    super::merge::require_inactive(&root)?;
    if root.join(".lit/transaction.json").try_exists().map_err(|e| LitError::io(e.to_string()))? {
        return Err("Finish the active transaction before pulling".into());
    }
    let current = get_current_branch(&root)?;
    super::remote::validate_ref(&current)?;
    let (fetch, git) = super::fetch::fetch_from(&root, remote.clone(), Some(branch.clone()))?;

    let local = read_head(&root).ok();
    let incoming = read_ref(&root, &format!("remotes/{remote}/{branch}"))?;
    let store = ObjectStore::new(&root);
    let new = ObjectHash::from_hex(incoming.clone());
    let (fast_forward, has_conflicts, merge_message) = if let Some(local) = &local {
        let old = ObjectHash::from_hex(local.clone());
        if old == new || crate::core::merge::is_ancestor(&store, &new, &old)? {
            (false, false, "Already up to date".into())
        } else if crate::core::merge::is_ancestor(&store, &old, &new)? {
            super::worktree::install(&root, Some(local), &incoming, Some(&current))?;
            (true, false, format!("Fast-forward to {}", new.short()))
        } else if git {
            return Err("Git pull requires a fast-forward. Fetch and integrate the tracking branch explicitly.".into());
        } else {
            let merge =
                super::merge::execute_target(&root, format!("{remote}/{branch}"), incoming, None)?;
            (merge.fast_forward, merge.has_conflicts, merge.message)
        }
    } else {
        super::worktree::install(&root, None, &incoming, Some(&current))?;
        (true, false, format!("Fast-forward to {}", new.short()))
    };
    Ok(PullResponse {
        message: format!(
            "From {remote}\n  {} objects fetched\n  {merge_message}",
            fetch.objects_transferred
        ),
        remote,
        branch,
        objects_fetched: fetch.objects_transferred,
        fast_forward,
        has_conflicts,
        merge_message,
    })
}
