use crate::core::{Blob, Object};
use crate::errors::LitError;
use crate::storage::{Index, ObjectStore};
use std::collections::BTreeSet;
use std::fs;
use std::io::{ErrorKind, Write};
use std::path::{Component, Path, PathBuf};

pub(super) const WORKSPACE_BASE_REF: &str = "workspace/base";

/// Install a commit in a fresh clone or fast-forward a clean checkout.
/// Keep original files until the index and ref have been published.
pub(super) fn install(
    root: &Path,
    old: Option<&str>,
    new: &str,
    branch: Option<&str>,
) -> Result<(), LitError> {
    let reference = branch.map(|branch| root.join(".lit/refs/heads").join(branch));
    install_checkout(root, old, new, reference.as_deref(), false)
}

pub(super) fn install_merge(root: &Path, old: &str, new: &str) -> Result<(), LitError> {
    let reference = if let Ok(branch) = crate::core::get_current_branch(root) {
        super::remote::validate_ref(&branch)?;
        root.join(".lit/refs/heads").join(branch)
    } else {
        root.join(".lit/HEAD")
    };
    install_checkout(root, Some(old), new, Some(&reference), true)
}

fn install_checkout(
    root: &Path,
    old: Option<&str>,
    new: &str,
    reference: Option<&Path>,
    allow_matching_untracked: bool,
) -> Result<(), LitError> {
    let previous = super::status::head_entries(root, old)?;
    let next = super::status::head_entries(root, Some(new))?;
    let index = Index::load(root)?;
    let legacy_clone = index.entries.is_empty() && !previous.is_empty();
    if !legacy_clone
        && (index.entries.len() != previous.len()
            || index.entries.iter().any(|(path, entry)| {
                previous
                    .get(path)
                    .is_none_or(|old| old.mode != entry.mode || old.hash.as_str() != entry.hash)
            }))
    {
        let operation = if allow_matching_untracked {
            "merge"
        } else {
            "pull"
        };
        return Err(format!("Cannot {operation} with staged changes").into());
    }
    let metadata = root.join(".lit");
    let staging = tempfile::tempdir_in(&metadata).map_err(io_error)?;
    let store = ObjectStore::new(root);
    let mut new_index = Index::new();
    for (path, entry) in &next {
        new_index.add(path.clone(), entry.hash.to_string(), entry.mode.clone());
    }
    let paths: BTreeSet<_> = previous.keys().chain(next.keys()).collect();
    let mut changes = Vec::new();
    for (number, path) in paths.into_iter().enumerate() {
        let old = previous.get(path);
        let new = next.get(path);
        if old
            .zip(new)
            .is_some_and(|(a, b)| a.hash == b.hash && a.mode == b.mode)
        {
            continue;
        }
        let target = safe_path(root, path)?;
        if let Some(old) = old {
            let bytes = working_bytes(&target, &old.mode)
                .map_err(|_| format!("Cannot overwrite modified path {path}"))?;
            if Object::Blob(Blob::new(bytes)).hash() != old.hash {
                return Err(format!("Cannot overwrite modified path {path}").into());
            }
        } else if target.symlink_metadata().is_ok() {
            if allow_matching_untracked
                && new.is_some_and(|entry| {
                    working_bytes(&target, &entry.mode)
                        .is_ok_and(|bytes| Object::Blob(Blob::new(bytes)).hash() == entry.hash)
                })
            {
                // The merge can index an identical existing file without replacing it.
                continue;
            }
            return Err(format!("Cannot overwrite untracked path {path}").into());
        }
        let incoming = if let Some(new) = new {
            let Object::Blob(blob) = store.read(&new.hash)? else {
                return Err(format!("Expected a blob for {path}").into());
            };
            let prepared = staging.path().join(format!("new-{number}"));
            materialize(&prepared, &new.mode, &blob.content)?;
            Some(prepared)
        } else {
            None
        };
        let backup = old.map(|_| staging.path().join(format!("old-{number}")));
        changes.push((target, incoming, backup));
    }

    let index_path = metadata.join("index");
    let old_index = fs::read(&index_path).map_err(io_error)?;
    let mut applied = Vec::new();
    let mut created_dirs = Vec::new();
    let result = (|| {
        for (target, incoming, backup) in &changes {
            let parent = target.parent().ok_or("Worktree path has no parent")?;
            let missing: Vec<_> = parent.ancestors().take_while(|p| !p.exists()).collect();
            for dir in missing.into_iter().rev() {
                fs::create_dir(dir).map_err(io_error)?;
                created_dirs.push(dir.to_owned());
            }
            if let Some(backup) = backup {
                fs::rename(target, backup).map_err(io_error)?;
            }
            applied.push((target, backup, false));
            if let Some(incoming) = incoming {
                // Do not overwrite a path created since preflight.
                fs::hard_link(incoming, target).map_err(io_error)?;
                applied.last_mut().expect("recorded worktree update").2 = true;
            }
        }
        replace(&index_path, &new_index.encode(root, None)?)?;
        if let Some(path) = reference {
            fs::create_dir_all(path.parent().ok_or("Branch has no ref directory")?)
                .map_err(io_error)?;
            let bytes = crate::core::refs::encode_ref_file(root, path, &format!("{new}\n"))?;
            replace(path, &bytes)?;
        }
        Ok::<_, LitError>(())
    })();
    if let Err(error) = result {
        let mut restored = replace(&index_path, &old_index).is_ok();
        for (target, backup, installed) in applied.into_iter().rev() {
            if installed {
                restored &= fs::remove_file(target).is_ok();
            }
            if let Some(backup) = backup {
                restored &= fs::rename(backup, target).is_ok();
            }
        }
        for dir in created_dirs.into_iter().rev() {
            let _ = fs::remove_dir(dir);
        }
        if !restored {
            let recovery = staging.keep();
            return Err(format!(
                "{error}; worktree backups retained at {}",
                recovery.display()
            )
            .into());
        }
        return Err(error);
    }
    Ok(())
}

pub(super) fn safe_path(root: &Path, path: &str) -> Result<PathBuf, LitError> {
    let mut target = root.to_owned();
    let mut parts = Path::new(path).components().peekable();
    if parts.peek().is_none()
        || path.contains('\\')
        || path
            .split('/')
            .any(|part| part.eq_ignore_ascii_case(".lit") || part.eq_ignore_ascii_case(".git"))
    {
        return Err(format!("Invalid worktree path {path}").into());
    }
    while let Some(part) = parts.next() {
        let Component::Normal(name) = part else {
            return Err(format!("Invalid worktree path {path}").into());
        };
        target.push(name);
        if parts.peek().is_some() {
            match target.symlink_metadata() {
                Ok(meta) if !meta.is_dir() => {
                    return Err(format!("Blocked parent for {path}").into())
                }
                Ok(_) => {}
                Err(e) if e.kind() == ErrorKind::NotFound => {}
                Err(e) => return Err(io_error(e)),
            }
        }
    }
    Ok(target)
}

pub(super) fn working_bytes(path: &Path, mode: &str) -> std::io::Result<Vec<u8>> {
    let metadata = path.symlink_metadata()?;
    #[cfg(unix)]
    if mode == "120000" && metadata.is_symlink() {
        use std::os::unix::ffi::OsStrExt;
        return Ok(fs::read_link(path)?.as_os_str().as_bytes().to_vec());
    }
    if !metadata.is_file()
        || mode == "120000"
        || (cfg!(unix) && super::status::regular_file_mode(&metadata) != mode)
    {
        return Err(std::io::Error::other("File type or mode differs"));
    }
    fs::read(path)
}

pub(super) fn materialize(path: &Path, mode: &str, bytes: &[u8]) -> Result<(), LitError> {
    #[cfg(unix)]
    if mode == "120000" {
        use std::os::unix::{ffi::OsStrExt, fs::symlink};
        symlink(std::ffi::OsStr::from_bytes(bytes), path).map_err(io_error)?;
        return Ok(());
    }
    if !matches!(mode, "100644" | "100755") {
        return Err(format!("Unsupported worktree file mode {mode}").into());
    }
    fs::write(path, bytes).map_err(io_error)?;
    #[cfg(unix)]
    if mode == "100755" {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(path).map_err(io_error)?.permissions();
        perms.set_mode(perms.mode() | 0o111);
        fs::set_permissions(path, perms).map_err(io_error)?;
    }
    Ok(())
}

pub(super) fn replace(path: &Path, bytes: &[u8]) -> Result<(), LitError> {
    let mut file =
        tempfile::NamedTempFile::new_in(path.parent().ok_or("Metadata path has no parent")?)
            .map_err(io_error)?;
    file.write_all(bytes).map_err(io_error)?;
    file.as_file().sync_all().map_err(io_error)?;
    file.persist(path).map_err(|e| io_error(e.error))?;
    Ok(())
}

fn io_error(error: std::io::Error) -> LitError {
    LitError::io(format!("Apply worktree update: {error}"))
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
pub(super) fn publish_new_directory(source: &Path, target: &Path) -> Result<(), LitError> {
    use std::ffi::CString;
    use std::io;
    use std::os::unix::ffi::OsStrExt;

    let path_arg = |path: &Path| {
        CString::new(path.as_os_str().as_bytes()).map_err(|error| LitError::io(error.to_string()))
    };
    let from = path_arg(source)?;
    let to = path_arg(target)?;
    // The kernel performs the no-replace check together with the rename.
    #[cfg(target_os = "linux")]
    let result = unsafe {
        libc::renameat2(
            libc::AT_FDCWD,
            from.as_ptr(),
            libc::AT_FDCWD,
            to.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    #[cfg(target_os = "macos")]
    let result = unsafe { libc::renamex_np(from.as_ptr(), to.as_ptr(), libc::RENAME_EXCL) };
    if result != 0 {
        return Err(LitError::io(format!(
            "Cannot publish {}: {}",
            target.display(),
            io::Error::last_os_error()
        )));
    }
    Ok(())
}
