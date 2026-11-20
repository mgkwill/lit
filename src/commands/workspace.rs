use super::worktree::WORKSPACE_BASE_REF;
use crate::core::{
    find_repo_root, read_head, read_ref, update_head, write_ref, Object, ObjectHash,
};
use crate::crypto::encryption::EncryptionConfig;
use crate::errors::LitError;
use crate::network::transport::walk_commit_graph;
use crate::response::CommandResponse;
use crate::storage::{Index, ObjectStore};
use serde::Serialize;
use std::collections::HashSet;
use std::fs::{self, File, Permissions};
use std::io::ErrorKind;
use std::os::unix::fs::PermissionsExt;
use std::path::{Component, Path};
use walkdir::WalkDir;

#[derive(Serialize)]
pub struct WorkspaceResponse {
    path: String,
    base: String,
    head: String,
    branch: Option<String>,
}

impl CommandResponse for WorkspaceResponse {
    fn command_name(&self) -> &'static str {
        "workspace"
    }

    fn human_readable(&self) -> String {
        format!(
            "Workspace {}\nBase {}\nHEAD {}",
            self.path, self.base, self.head
        )
    }
}

pub fn create(destination: &Path, base: &str, branch: &str) -> Result<WorkspaceResponse, LitError> {
    if !ObjectHash::is_full_hex(base) {
        return Err(LitError::repository(
            "Workspace base must be a full Lit commit ID",
        ));
    }
    super::remote::validate_ref(branch)?;
    let source = find_repo_root()?.canonicalize()?;
    let name = destination
        .file_name()
        .ok_or("Missing workspace directory name")?;
    let parent = destination
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."))
        .canonicalize()?;
    let target = parent.join(name);
    if parent.starts_with(&source) || parent.ancestors().any(crate::core::refs::is_lit_repo) {
        return Err(LitError::repository(
            "Workspace destination must be outside existing Lit repositories",
        ));
    }
    match target.symlink_metadata() {
        Ok(_) => return Err(LitError::repository("Workspace destination already exists")),
        Err(error) if error.kind() == ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }

    let staging = tempfile::Builder::new()
        .prefix(".lit-workspace-")
        .permissions(Permissions::from_mode(0o700))
        .tempdir_in(&parent)?;
    let checkout = staging.path();
    super::init::execute(
        false,
        Some(
            checkout
                .to_str()
                .ok_or("Workspace path is not UTF-8")?
                .to_owned(),
        ),
    )?;
    populate(&source, checkout, base)?;

    super::worktree::install(checkout, None, base, Some(branch))?;
    update_head(checkout, branch)?;
    write_ref(checkout, WORKSPACE_BASE_REF, base)?;
    verify_checkout(checkout)?;
    sync_checkout(checkout)?;

    super::worktree::publish_new_directory(checkout, &target)?;
    super::gc::sync_directory(&parent)?;
    Ok(WorkspaceResponse {
        path: target.display().to_string(),
        base: base.to_owned(),
        head: base.to_owned(),
        branch: Some(branch.to_owned()),
    })
}

fn populate(source: &Path, checkout: &Path, base: &str) -> Result<(), LitError> {
    let _guard = super::transaction::lock_writes(source)?;
    let source_store = ObjectStore::new(source);
    let base = ObjectHash::from_hex(base.to_owned());
    let commit = source_store
        .read_verified(&base)
        .map_err(|error| LitError::repository(format!("Cannot read workspace base: {error}")))?;
    if !matches!(commit, Object::Commit(_)) {
        return Err(LitError::repository(
            "Workspace base must identify a commit",
        ));
    }
    let objects = walk_commit_graph(&source_store, &base, &HashSet::new())?;
    configure_encryption(source, checkout)?;
    let destination_store = ObjectStore::new(checkout);
    for hash in objects {
        let object = source_store.read_verified(&hash)?;
        destination_store.write(&object)?;
    }
    Ok(())
}

fn configure_encryption(source: &Path, checkout: &Path) -> Result<(), LitError> {
    let mut config = EncryptionConfig::load(source).map_err(LitError::encryption)?;
    if config.enabled {
        // Initialize the new key while the repository still has an empty index.
        config.key_file = ".lit/keys/workspace.key".to_owned();
        config.save(checkout).map_err(LitError::encryption)?;
        Index::new().save(checkout).map_err(LitError::encryption)?;
    }
    Ok(())
}

fn verify_checkout(root: &Path) -> Result<(), LitError> {
    let result = super::verify::at(root, None);
    if let Some(failed) = result.checks.iter().find(|check| check.status != "ok") {
        let details = failed.details.as_deref().unwrap_or("Invalid repository");
        return Err(LitError::repository(format!(
            "Workspace verification failed: {details}"
        )));
    }
    Ok(())
}

fn sync_checkout(root: &Path) -> Result<(), LitError> {
    for entry in WalkDir::new(root).contents_first(true) {
        let entry = entry.map_err(|error| LitError::io(error.to_string()))?;
        if entry.file_type().is_symlink() {
            let link = fs::read_link(entry.path())?;
            if link.components().any(|component| match component {
                Component::Normal(name) => {
                    name.eq_ignore_ascii_case(".lit") || name.eq_ignore_ascii_case(".git")
                }
                Component::CurDir => false,
                _ => true,
            }) {
                return Err(LitError::repository(format!(
                    "Workspace symlink must use a relative path without parent or metadata traversal: {}",
                    entry.path().strip_prefix(root).unwrap_or(entry.path()).display(),
                )));
            }
        } else {
            File::open(entry.path())?.sync_all()?;
        }
    }
    Ok(())
}

pub fn info() -> Result<WorkspaceResponse, LitError> {
    let root = find_repo_root()?;
    let base = read_ref(&root, WORKSPACE_BASE_REF)
        .map_err(|_| LitError::repository("Workspace base ref not found"))?;
    if !ObjectHash::is_full_hex(&base) {
        return Err(LitError::repository("Invalid workspace base ref"));
    }
    Ok(WorkspaceResponse {
        path: root.display().to_string(),
        base,
        head: read_head(&root)?,
        branch: crate::core::get_current_branch(&root).ok(),
    })
}
