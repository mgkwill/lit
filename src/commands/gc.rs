use crate::core::find_repo_root;
use crate::response::GcResponse;
use crate::storage::{pack, ObjectStore};
use std::fs;
use std::path::Path;

// Preserve the existing library entry points for native packs.
#[allow(unused_imports)]
pub use crate::storage::pack::{
    load_pack_index, read_pack_object, write_pack, write_pack_index, PackEntry, PackIndexEntry,
};

/// Execute the `gc` (garbage collection) command.
/// Packs all loose objects into a single pack file and removes the loose files.
pub fn execute() -> Result<GcResponse, crate::errors::LitError> {
    let repo_root = find_repo_root()?;
    let _guard = super::transaction::lock_writes(&repo_root)?;
    if repo_root
        .join(".lit/transaction.json")
        .try_exists()
        .map_err(|e| e.to_string())?
    {
        return Err("Finish the active transaction before packing objects.".into());
    }
    super::merge::require_inactive(&repo_root)?;
    let store = ObjectStore::new(&repo_root);
    let format = if store.encryption_enabled()? {
        pack::Format::Encrypted
    } else {
        pack::Format::Plain
    };

    let all_hashes = store
        .list_loose()
        .map_err(|e| format!("Failed to list objects: {}", e))?;

    if all_hashes.is_empty() {
        return Ok(GcResponse {
            objects_packed: 0,
            packs_created: 0,
            loose_removed: 0,
            bytes_saved: 0,
            message: "No objects to pack".to_string(),
        });
    }

    let loose_bytes = all_hashes.iter().try_fold(0u64, |bytes, hash| {
        store
            .loose_size(hash)
            .map(|size| bytes.saturating_add(size))
    })?;
    let packs_dir = repo_root.join(".lit/packs");
    fs::create_dir_all(&packs_dir)?;
    let staging = tempfile::tempdir_in(&packs_dir)?;
    let staged_pack = staging.path().join("objects.pack");
    let staged_index = staging.path().join("objects.idx");
    let entries = pack::write_loose(&store, &all_hashes, &staged_pack, format)?;
    pack::write_index(&entries, &staged_index, format)?;

    for (hash, location) in pack::indexed_objects(&staged_index)? {
        store.with_cipher(false, |cipher| location.read(&hash, Some(cipher)))?;
    }

    let pack_bytes = fs::metadata(&staged_pack)?.len();
    let index_bytes = fs::metadata(&staged_index)?.len();
    let name = format!("pack-{}", uuid::Uuid::new_v4());
    let pack_path = packs_dir.join(format!("{name}.pack"));
    let index_path = packs_dir.join(format!("{name}.idx"));

    // Durably publish the pack before its index, then remove loose copies.
    sync_directory(&repo_root.join(".lit"))?;
    fs::rename(staged_pack, &pack_path)?;
    sync_directory(&packs_dir)?;
    fs::rename(staged_index, &index_path)?;
    sync_directory(&packs_dir)?;

    let mut loose_removed = 0;
    for hash in &all_hashes {
        if store.remove_loose(hash)? {
            loose_removed += 1;
        }
    }

    let bytes_saved = loose_bytes.saturating_sub(pack_bytes + index_bytes);

    Ok(GcResponse {
        objects_packed: all_hashes.len() as u64,
        packs_created: 1,
        loose_removed,
        bytes_saved,
        message: format!(
            "Packed {} objects into {} ({} bytes saved)",
            all_hashes.len(),
            pack_path.display(),
            bytes_saved
        ),
    })
}

pub(super) fn sync_directory(path: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    fs::File::open(path)?.sync_all()?;
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}
