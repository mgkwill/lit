use crate::core::{set_head_detached, update_head};
use crate::errors::LitError;
use crate::network::transport::{self, RemoteRepo};
use crate::network::AirgapValidator;
use crate::response::CloneResponse;
use crate::storage::ObjectStore;
use std::fs;
use std::path::Path;

pub fn execute(url: String, directory: Option<String>) -> Result<CloneResponse, LitError> {
    AirgapValidator::new()?.validate_transport(&url)?;
    let dir_name = directory.unwrap_or_else(|| {
        let name = url.trim_end_matches('/').rsplit('/').next().unwrap_or("");
        let name = name
            .strip_suffix(".git")
            .or_else(|| name.strip_suffix(".lit"))
            .unwrap_or(name);
        if name.is_empty() {
            "repo".into()
        } else {
            name.into()
        }
    });
    let target = std::env::current_dir()
        .map_err(|e| e.to_string())?
        .join(&dir_name);
    if target.symlink_metadata().is_ok() {
        return Err(format!("Directory '{dir_name}' already exists").into());
    }
    let parent = target.parent().ok_or("Clone target has no parent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let staging = tempfile::tempdir_in(parent).map_err(|e| e.to_string())?;
    let root = staging.path();
    super::init::execute(false, Some(root.to_string_lossy().into_owned()))?;
    super::remote::set_origin(root, &url)?;

    let (branches, transferred) = if let Some(endpoint) = super::git_remote::discover(&url)? {
        super::git_remote::clone_into(root, &endpoint)?
    } else {
        clone_native(root, &url)?
    };
    if target.symlink_metadata().is_ok() {
        return Err(format!("Directory '{dir_name}' already exists").into());
    }
    fs::rename(root, &target).map_err(|e| format!("Publish clone: {e}"))?;

    Ok(CloneResponse {
        url,
        directory: dir_name.clone(),
        branches_cloned: branches.clone(),
        objects_transferred: transferred,
        message: format!(
            "Cloned into '{dir_name}'\n  {transferred} objects, {} branches",
            branches.len()
        ),
    })
}

fn clone_native(root: &Path, url: &str) -> Result<(Vec<String>, usize), LitError> {
    let remote = RemoteRepo::open(url)?;
    let branches = remote.list_branches()?;
    let tags = remote.list_refs("tags")?;
    let head = remote.read_head()?;
    let detached = crate::commands::serve::is_valid_hex_hash(&head);
    let mut wants: Vec<_> = branches
        .iter()
        .map(|(_, hash)| hash.clone())
        .chain(tags.iter().map(|tag| tag.hash.clone()))
        .collect();
    if detached {
        wants.push(head.clone());
    }
    let store = ObjectStore::new(root);
    let needed = remote.negotiate_download(&store, &wants)?;
    let transferred = remote.download_objects(&store, &needed)?;

    for (branch, hash) in &branches {
        super::remote::validate_ref(branch)?;
        transport::update_remote_tracking_ref(root, "origin", branch, hash)?;
    }
    for tag in tags {
        super::remote::validate_ref(&tag.name)?;
        crate::core::write_ref(root, &format!("tags/{}", tag.name), &tag.hash)?;
    }
    if detached {
        super::worktree::install(root, None, &head, None)?;
        set_head_detached(root, &head)?;
    } else {
        let branch = head
            .strip_prefix("ref: refs/heads/")
            .ok_or("Invalid remote HEAD")?;
        super::remote::validate_ref(branch)?;
        if let Some((_, tip)) = branches.iter().find(|(name, _)| name == branch) {
            super::worktree::install(root, None, tip, Some(branch))?;
        }
        update_head(root, branch)?;
    }
    Ok((
        branches.into_iter().map(|(name, _)| name).collect(),
        transferred,
    ))
}
