use crate::core::find_repo_root;
use crate::errors::LitError;
use crate::network::transport::RemoteRepo;
use crate::network::AirgapValidator;
use crate::response::FetchResponse;
use crate::storage::ObjectStore;
use std::path::Path;

pub fn execute(
    remote: String,
    branch: Option<String>,
) -> Result<FetchResponse, crate::errors::LitError> {
    let repo_root = find_repo_root()?;
    let _lock = super::transaction::lock_writes(&repo_root)?;
    fetch_from(&repo_root, remote, branch).map(|(response, _)| response)
}

pub(super) fn fetch_from(
    repo_root: &Path,
    remote: String,
    branch: Option<String>,
) -> Result<(FetchResponse, bool), LitError> {
    let remote_url = super::remote::url(repo_root, &remote)?;
    if let Some(branch) = &branch {
        super::remote::validate_ref(branch)?;
    }
    AirgapValidator::new()?.validate_transport(&remote_url)?;
    if let Some(endpoint) = super::git_remote::discover(&remote_url)? {
        return super::git_remote::fetch(repo_root, remote, &endpoint, branch)
            .map(|response| (response, true));
    }

    let remote_repo = RemoteRepo::open(&remote_url)?;
    let local_store = ObjectStore::new(repo_root);

    // Determine which branches to fetch
    let branches_to_fetch: Vec<(String, String)> = if let Some(ref b) = branch {
        let hash = remote_repo.read_branch_ref(b)?;
        vec![(b.clone(), hash)]
    } else {
        remote_repo.list_branches()?
    };

    if branches_to_fetch.is_empty() {
        return Ok((
            FetchResponse {
                remote: remote.clone(),
                branches_updated: vec![],
                objects_transferred: 0,
                message: format!("No branches found on remote '{}'", remote),
            },
            false,
        ));
    }

    // Negotiate and download objects
    let wants: Vec<String> = branches_to_fetch.iter().map(|(_, h)| h.clone()).collect();
    let needed = remote_repo.negotiate_download(&local_store, &wants)?;
    let total_transferred = remote_repo.download_objects(&local_store, &needed)?;

    // Update remote-tracking refs
    let mut updated_branches = Vec::new();
    for (branch_name, hash) in &branches_to_fetch {
        super::remote::validate_ref(branch_name)?;
        crate::network::transport::update_remote_tracking_ref(
            repo_root,
            &remote,
            branch_name,
            hash,
        )?;
        updated_branches.push(format!(
            "{} -> {}/{}",
            &hash[..16.min(hash.len())],
            remote,
            branch_name
        ));
    }

    let message = if total_transferred > 0 {
        format!(
            "From {}\n  {} objects transferred, {} branches updated",
            remote_url,
            total_transferred,
            updated_branches.len()
        )
    } else {
        format!("From {}\n  Already up to date", remote_url)
    };

    Ok((
        FetchResponse {
            remote,
            branches_updated: updated_branches,
            objects_transferred: total_transferred,
            message,
        },
        false,
    ))
}
