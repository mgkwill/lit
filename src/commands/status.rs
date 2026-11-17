use crate::core::{find_repo_root, Blob, Object, ObjectHash, TreeEntry};
use crate::response::StatusResponse;
use crate::storage::{Index, ObjectStore};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::ErrorKind;
use std::path::Path;
use walkdir::WalkDir;

const MAX_WALK_FILES: usize = 50_000;

pub fn execute() -> Result<StatusResponse, crate::errors::LitError> {
    let repo_root = find_repo_root()?;
    let index = Index::load(&repo_root)?;
    let branch = crate::core::get_current_branch(&repo_root).ok();
    let head = crate::core::read_head(&repo_root).ok();
    let committed = head_entries(&repo_root, head.as_deref())?;
    let ignore_dirs = load_ignore_dirs(&repo_root);

    let mut staged = Vec::new();
    let mut modified = Vec::new();
    for (path, entry) in &index.entries {
        if committed
            .get(path)
            .is_none_or(|old| old.hash.as_str() != entry.hash || old.mode != entry.mode)
        {
            staged.push(path.clone());
        }
        let path_on_disk = repo_root.join(path);
        let changed = if entry.mode == "120000" {
            super::worktree::working_bytes(&path_on_disk, &entry.mode)
                .map(|bytes| Object::Blob(Blob::new(bytes)).hash().as_str() != entry.hash)
                .unwrap_or(true)
        } else {
            match fs::metadata(&path_on_disk) {
                Ok(meta) if meta.is_file() => {
                    let blob = Object::Blob(Blob::new(
                        fs::read(&path_on_disk).map_err(|e| e.to_string())?,
                    ));
                    blob.hash().as_str() != entry.hash || mode_changed(&meta, &entry.mode)
                }
                Ok(_) => true,
                Err(e) if matches!(e.kind(), ErrorKind::NotFound | ErrorKind::NotADirectory) => {
                    true
                }
                Err(e) => return Err(format!("Failed to inspect {path}: {e}").into()),
            }
        };
        if changed {
            modified.push(path.clone());
        }
    }
    staged.extend(
        committed
            .into_keys()
            .filter(|path| !index.entries.contains_key(path)),
    );

    let mut untracked = Vec::new();
    let mut scan_complete = true;
    let mut visited = 0;
    for entry in WalkDir::new(&repo_root)
        .into_iter()
        .filter_entry(|entry| entry.depth() == 0 || !should_skip_entry(entry, &ignore_dirs))
    {
        let entry = match entry {
            Ok(entry) => entry,
            Err(_) => {
                scan_complete = false;
                continue;
            }
        };
        if entry.file_type().is_file() || entry.file_type().is_symlink() {
            visited += 1;
            if visited > MAX_WALK_FILES {
                scan_complete = false;
                break;
            }
            let path = entry
                .path()
                .strip_prefix(&repo_root)
                .expect("walk starts at repo root");
            let path = path.to_string_lossy().replace('\\', "/");
            if !index.entries.contains_key(&path) {
                untracked.push(path);
            }
        }
    }

    staged.sort();
    modified.sort();
    untracked.sort();
    let clean = scan_complete && staged.is_empty() && modified.is_empty() && untracked.is_empty();
    Ok(StatusResponse {
        branch,
        head,
        staged,
        modified,
        untracked,
        clean,
        scan_complete,
    })
}

pub(crate) fn head_entries(
    repo_root: &Path,
    head: Option<&str>,
) -> Result<HashMap<String, TreeEntry>, crate::errors::LitError> {
    let Some(head) = head else {
        return Ok(HashMap::new());
    };
    let store = ObjectStore::new(repo_root);
    let Object::Commit(commit) = store.read(&ObjectHash::from_hex(head.to_owned()))? else {
        return Err("HEAD does not reference a commit".into());
    };
    let Object::Tree(tree) = store.read(&commit.tree)? else {
        return Err("HEAD commit does not reference a tree".into());
    };
    Ok(crate::core::diff::collect_tree_entries(&tree, &store, "")?
        .into_iter()
        .collect())
}

pub(crate) fn regular_file_mode(meta: &fs::Metadata) -> &'static str {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if meta.permissions().mode() & 0o100 != 0 {
            return "100755";
        }
    }
    let _ = meta;
    "100644"
}

fn mode_changed(meta: &fs::Metadata, indexed: &str) -> bool {
    cfg!(unix) && matches!(indexed, "100644" | "100755") && regular_file_mode(meta) != indexed
}

/// Load directory names to ignore from built-in defaults + .litignore file.
fn load_ignore_dirs(repo_root: &Path) -> HashSet<String> {
    let mut dirs: HashSet<String> = [
        // VCS / build
        "target",
        "node_modules",
        "venv",
        "__pycache__",
        "dist",
        "out",
        "bin",
        "obj",
        // Toolchain caches
        ".cargo",
        ".rustup",
        ".npm",
        ".nvm",
        ".pyenv",
        ".conda",
        ".local",
        ".cache",
        ".thumbnails",
        // OS user-profile directories (home-dir-as-repo safety)
        "AppData",
        "Application Data",
        "Library",
        "Caches",
        // Cloud sync
        "OneDrive",
        "Dropbox",
        "Google Drive",
        // Windows user folders
        "Documents",
        "Downloads",
        "Desktop",
        "Music",
        "Pictures",
        "Videos",
        "Contacts",
        "Favorites",
        "Links",
        "Saved Games",
        "Searches",
        "3D Objects",
        "scoop",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();

    // Read .litignore (one directory name per line, # for comments)
    let ignore_path = repo_root.join(".litignore");
    if let Ok(content) = fs::read_to_string(&ignore_path) {
        for line in content.lines() {
            let line = line.trim().trim_end_matches('/');
            if !line.is_empty() && !line.starts_with('#') {
                dirs.insert(line.to_string());
            }
        }
    }

    dirs
}

/// Decide whether a walkdir entry should be pruned.
fn should_skip_entry(entry: &walkdir::DirEntry, ignore_dirs: &HashSet<String>) -> bool {
    let name = entry.file_name().to_string_lossy();

    // Always skip hidden (dot-prefixed)
    if name.starts_with('.') {
        return true;
    }

    // Skip directories by name
    entry.file_type().is_dir() && ignore_dirs.contains(name.as_ref())
}
