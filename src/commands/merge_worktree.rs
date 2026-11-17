use crate::core::{Blob, Object, ObjectHash, TreeEntry};
use crate::errors::LitError;
use crate::storage::index::IndexEntry;
use crate::storage::{Index, ObjectStore};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};

pub(super) const FILE: &str = "worktree.json";

/// Each path is changed at most once. Record both versions before touching disk
/// so a retry can recognize a path already written or restored.
#[derive(Serialize, Deserialize)]
pub(super) struct MergeWorktree {
    pub(super) abortable: bool,
    pub(super) aborting: bool,
    changes: BTreeMap<String, FileChange>,
    created_dirs: BTreeSet<String>,
    original_index: Option<Vec<u8>>,
}

#[derive(Serialize, Deserialize)]
struct FileChange {
    before: Option<IndexEntry>,
    after: Option<IndexEntry>,
    pending: bool,
    #[cfg(unix)]
    permissions: Option<u32>,
}

impl MergeWorktree {
    pub(super) fn plan(
        original: &HashMap<String, TreeEntry>,
        merged: &HashMap<String, TreeEntry>,
        abortable: bool,
    ) -> Self {
        let paths: BTreeSet<_> = original.keys().chain(merged.keys()).collect();
        let changes = paths
            .into_iter()
            .filter_map(|path| {
                let before = index_entry(path, original.get(path));
                let after = index_entry(path, merged.get(path));
                (before != after).then(|| {
                    (
                        path.clone(),
                        FileChange {
                            before,
                            after,
                            pending: true,
                            #[cfg(unix)]
                            permissions: None,
                        },
                    )
                })
            })
            .collect();
        Self {
            abortable,
            aborting: false,
            changes,
            created_dirs: BTreeSet::new(),
            original_index: None,
        }
    }

    pub(super) fn resolved(&self, path: &str) -> bool {
        self.changes.contains_key(path)
    }

    pub(super) fn record_resolution(
        &mut self,
        path: &str,
        original: Option<&TreeEntry>,
        selected: Option<&TreeEntry>,
    ) {
        self.changes.insert(
            path.to_owned(),
            FileChange {
                before: index_entry(path, original),
                after: index_entry(path, selected),
                pending: true,
                #[cfg(unix)]
                permissions: None,
            },
        );
    }

    pub(super) fn verify(&self, root: &Path, store: &ObjectStore) -> Result<(), LitError> {
        if let Some(original) = &self.original_index {
            Index::decode(root, original, None)
                .map_err(|e| format!("Invalid merge checkpoint index: {e}"))?;
        }
        for (path, change) in &self.changes {
            checked_path(root, path)?;
            #[cfg(unix)]
            if change.permissions.is_some_and(|mode| mode & !0o777 != 0) {
                return Err(format!("Invalid merge checkpoint permissions for {path}").into());
            }
            for entry in [&change.before, &change.after].into_iter().flatten() {
                if entry.path != *path
                    || !ObjectHash::is_full_hex(&entry.hash)
                    || !matches!(entry.mode.as_str(), "100644" | "100755" | "120000")
                {
                    return Err(format!("Invalid merge checkpoint entry for {path}").into());
                }
                let hash = ObjectHash::from_hex(entry.hash.clone());
                if !store
                    .read(&hash)
                    .is_ok_and(|object| matches!(object, Object::Blob(_)) && object.hash() == hash)
                {
                    return Err(format!("Invalid merge checkpoint blob for {path}").into());
                }
            }
        }
        for directory in &self.created_dirs {
            checked_path(root, directory)?;
            if !self
                .changes
                .keys()
                .any(|path| Path::new(path).starts_with(directory) && path != directory)
            {
                return Err(format!("Invalid merge checkpoint directory {directory}").into());
            }
        }
        Ok(())
    }

    /// Validate every pending write before publishing its journal.
    pub(super) fn prepare(&mut self, root: &Path) -> Result<(), LitError> {
        let index = Index::load(root)?;
        if self.original_index.is_none() {
            self.original_index =
                Some(fs::read(root.join(".lit/index")).map_err(|e| e.to_string())?);
        }
        for (path, change) in self.changes.iter_mut().filter(|(_, change)| change.pending) {
            let target = checked_path(root, path)?;
            if index.entries.get(path) != change.before.as_ref()
                || !matches_file(&target, change.before.as_ref())?
            {
                return Err(format!(
                    "Cannot merge with staged, modified, or untracked work at {path}"
                )
                .into());
            }
            #[cfg(unix)]
            if change
                .before
                .as_ref()
                .is_some_and(|entry| entry.mode != "120000")
            {
                use std::os::unix::fs::PermissionsExt;
                change.permissions = Some(
                    fs::metadata(&target)
                        .map_err(|e| e.to_string())?
                        .permissions()
                        .mode()
                        & 0o777,
                );
            }
            if change.after.is_some() {
                for directory in target
                    .parent()
                    .into_iter()
                    .flat_map(Path::ancestors)
                    .take_while(|directory| !directory.exists())
                {
                    let relative = directory.strip_prefix(root).map_err(|e| e.to_string())?;
                    self.created_dirs.insert(
                        relative
                            .to_str()
                            .ok_or("Non-UTF-8 merge path")?
                            .replace(std::path::MAIN_SEPARATOR, "/"),
                    );
                }
            }
        }
        Ok(())
    }

    pub(super) fn save(&self, directory: &Path) -> Result<(), LitError> {
        let bytes = serde_json::to_vec(self).map_err(|e| e.to_string())?;
        super::worktree::replace(&directory.join(FILE), &bytes)
    }

    pub(super) fn resume(&mut self, root: &Path, store: &ObjectStore) -> Result<(), LitError> {
        if self.aborting {
            return Err("Merge abort was interrupted. Retry 'lit merge --abort'.".into());
        }
        if self.changes.values().any(|change| change.pending) {
            self.apply(root, store, false)?;
            for change in self.changes.values_mut() {
                change.pending = false;
            }
            self.save(&root.join(".lit/merge"))?;
        }
        Ok(())
    }

    pub(super) fn abort(&mut self, root: &Path, store: &ObjectStore) -> Result<(), LitError> {
        self.apply(root, store, true)?;
        // Remove only empty directories that a merge write could have created.
        let mut directories: Vec<_> = self.created_dirs.iter().collect();
        directories
            .sort_by_key(|directory| std::cmp::Reverse(Path::new(directory).components().count()));
        for directory in directories {
            match fs::remove_dir(checked_path(root, directory)?) {
                Ok(()) => {}
                Err(e)
                    if matches!(e.kind(), ErrorKind::NotFound | ErrorKind::DirectoryNotEmpty) => {}
                Err(e) => {
                    return Err(format!("Could not remove merge directory {directory}: {e}").into())
                }
            }
        }
        Ok(())
    }

    fn apply(&mut self, root: &Path, store: &ObjectStore, abort: bool) -> Result<(), LitError> {
        let directory = root.join(".lit/merge");
        let mut index = Index::load(root)?;
        let staging = tempfile::tempdir_in(root.join(".lit")).map_err(|e| e.to_string())?;
        let mut writes = Vec::new();
        let mut index_changed = false;
        for (number, (path, change)) in self.changes.iter().enumerate() {
            if (!abort && !change.pending) || change.before == change.after {
                continue;
            }
            let target = checked_path(root, path)?;
            let before_matches = matches_file(&target, change.before.as_ref())?;
            let after_matches = matches_file(&target, change.after.as_ref())?;
            let staged = index.entries.get(path);
            if !(before_matches || after_matches)
                || (staged != change.before.as_ref() && staged != change.after.as_ref())
            {
                return Err(format!("Merge will not overwrite edits to {path}. Save them elsewhere and restore the merge version before retrying.").into());
            }
            let (desired, matches) = if abort {
                (&change.before, before_matches)
            } else {
                (&change.after, after_matches)
            };
            index_changed |= staged != desired.as_ref();
            if let Some(entry) = desired {
                index.entries.insert(path.clone(), entry.clone());
            } else {
                index.remove(path);
            }
            if matches {
                continue;
            }
            let replacement = if let Some(entry) = desired {
                let Object::Blob(blob) = store.read(&ObjectHash::from_hex(entry.hash.clone()))?
                else {
                    return Err(format!("Expected a blob for {path}").into());
                };
                let prepared = staging.path().join(number.to_string());
                super::worktree::materialize(&prepared, &entry.mode, &blob.content)?;
                #[cfg(unix)]
                if entry.mode != "120000" {
                    use std::os::unix::fs::PermissionsExt;
                    if let Some(permissions) = change.permissions {
                        let permissions = match entry.mode.as_str() {
                            _ if abort
                                || change
                                    .before
                                    .as_ref()
                                    .is_some_and(|before| before.mode == entry.mode) =>
                            {
                                permissions
                            }
                            "100755" => permissions | ((permissions & 0o444) >> 2),
                            _ => permissions & !0o111,
                        };
                        fs::set_permissions(&prepared, fs::Permissions::from_mode(permissions))
                            .map_err(|e| e.to_string())?;
                    }
                }
                Some(prepared)
            } else {
                None
            };
            writes.push((
                target,
                replacement,
                if before_matches {
                    &change.before
                } else {
                    &change.after
                },
            ));
        }
        // Encode before changing files and preserve staged changes outside the merge.
        let index_bytes = if let Some(original) = &self.original_index {
            if abort && Index::decode(root, original, None)?.entries == index.entries {
                Some(original.clone())
            } else if index_changed {
                Some(index.encode(root, None)?)
            } else {
                None
            }
        } else if index_changed {
            Some(index.encode(root, None)?)
        } else {
            None
        };
        if abort && !self.aborting {
            // Persist the direction before the first change so only abort can
            // resume after a partially restored checkout.
            self.aborting = true;
            self.save(&directory)?;
        }
        for (target, replacement, expected) in writes {
            if !matches_file(&target, expected.as_ref())? {
                return Err(
                    format!("Path changed during merge update: {}", target.display()).into(),
                );
            }
            if let Some(replacement) = replacement {
                fs::create_dir_all(target.parent().ok_or("Merge path has no parent")?)
                    .map_err(|e| e.to_string())?;
                if expected.is_none() {
                    fs::hard_link(replacement, target).map_err(|e| e.to_string())?;
                } else {
                    fs::rename(replacement, target).map_err(|e| e.to_string())?;
                }
            } else {
                fs::remove_file(target).map_err(|e| e.to_string())?;
            }
        }
        if let Some(bytes) = index_bytes {
            super::worktree::replace(&root.join(".lit/index"), &bytes)?;
        }
        Ok(())
    }
}

fn index_entry(path: &str, entry: Option<&TreeEntry>) -> Option<IndexEntry> {
    entry.map(|entry| IndexEntry {
        path: path.to_owned(),
        hash: entry.hash.to_string(),
        mode: entry.mode.clone(),
    })
}

fn checked_path(root: &Path, path: &str) -> Result<PathBuf, LitError> {
    if path
        .split('/')
        .any(|part| part.is_empty() || part == "." || part == "..")
    {
        return Err(format!("Invalid merge path {path}").into());
    }
    super::worktree::safe_path(root, path)
}

fn matches_file(path: &Path, expected: Option<&IndexEntry>) -> Result<bool, LitError> {
    if let Some(entry) = expected {
        return Ok(super::worktree::working_bytes(path, &entry.mode)
            .is_ok_and(|bytes| Object::Blob(Blob::new(bytes)).hash().as_str() == entry.hash));
    }
    match path.symlink_metadata() {
        Ok(_) => Ok(false),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(true),
        Err(e) => Err(format!("Could not inspect {}: {e}", path.display()).into()),
    }
}
