use crate::core::find_repo_root;
use crate::core::ObjectHash;
use crate::network::transport::RemoteRepo;
use crate::network::AirgapValidator;
use crate::response::PushResponse;
use crate::storage::ObjectStore;
use std::collections::HashSet;

pub fn execute(
    remote: String,
    branch: String,
    force: bool,
) -> Result<PushResponse, crate::errors::LitError> {
    let repo_root = find_repo_root()?;
    let remote_url = super::remote::url(&repo_root, &remote)?;
    super::remote::validate_ref(&branch)?;

    let validator = AirgapValidator::new()?;
    validator.validate_transport(&remote_url)?;

    if super::git_remote::is_git(&remote_url) {
        return super::git_remote::push(&repo_root, remote, &remote_url, branch, force);
    }
    let remote_repo = RemoteRepo::open(&remote_url)?;

    // Get local branch tip
    let local_hash = crate::core::refs::read_ref(&repo_root, &format!("heads/{}", branch))
        .map_err(|_| format!("Branch '{}' not found locally", branch))?;

    let local_store = ObjectStore::new(&repo_root);

    // Check if branch exists on remote
    let remote_has_branch = remote_repo.read_branch_ref(&branch).ok();

    // Fast-forward check
    if let Some(ref remote_hash) = remote_has_branch {
        if remote_hash == &local_hash {
            return Ok(PushResponse {
                remote: remote.clone(),
                branch: branch.clone(),
                objects_transferred: 0,
                updated: false,
                message: "Everything up-to-date".to_string(),
            });
        }

        if !force {
            let remote_obj = ObjectHash::from_hex(remote_hash.clone());
            let local_obj = ObjectHash::from_hex(local_hash.clone());
            let is_ff = remote_repo.check_fast_forward(&local_store, &local_obj, &remote_obj)?;
            if !is_ff {
                return Err(
                    "Push rejected: non-fast-forward update. Use --force to override.".into(),
                );
            }
        }
    }

    // Walk local commit graph to find objects to transfer
    let remote_known: HashSet<String> = if let Some(ref rh) = remote_has_branch {
        let mut set = HashSet::new();
        set.insert(rh.clone());
        set
    } else {
        HashSet::new()
    };

    let needed = remote_repo.negotiate_upload(
        &local_store,
        std::slice::from_ref(&local_hash),
        &remote_known,
    )?;

    // Transfer objects from local to remote
    let transferred = remote_repo.upload_objects(&local_store, &needed)?;

    // Update remote's branch ref
    remote_repo.update_branch_ref(&branch, &local_hash, force)?;

    // Update local remote-tracking ref
    crate::network::transport::update_remote_tracking_ref(
        &repo_root,
        &remote,
        &branch,
        &local_hash,
    )?;

    let range = if let Some(old) = remote_has_branch {
        format!(
            "{}..{}",
            &old[..16.min(old.len())],
            &local_hash[..16.min(local_hash.len())]
        )
    } else {
        format!("[new branch] -> {}", branch)
    };

    Ok(PushResponse {
        remote: remote.clone(),
        branch: branch.clone(),
        objects_transferred: transferred,
        updated: true,
        message: format!(
            "To {}\n  {} {} objects transferred",
            remote_url, range, transferred
        ),
    })
}
