use super::pack::{Catalog, Location};
use crate::core::{Object, ObjectHash};
use crate::crypto::encryption::{
    lock_key_writes, require_rotation_complete, EncryptionConfig, EncryptionManager,
};
use flate2::read::ZlibDecoder;
use flate2::write::ZlibEncoder;
use flate2::Compression;
use std::collections::HashSet;
use std::fs;
use std::io::{ErrorKind, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

/// Object storage - handles reading/writing objects to disk with optional encryption
pub struct ObjectStore {
    repo: PathBuf,
    objects_dir: PathBuf,
    packs_dir: PathBuf,
    catalog: Mutex<Option<Arc<Catalog>>>,
    encryption: Mutex<Option<EncryptionManager>>,
}

impl ObjectStore {
    /// Create a new object store
    pub fn new(repo_path: &Path) -> Self {
        Self {
            repo: repo_path.to_owned(),
            objects_dir: repo_path.join(".lit/objects"),
            packs_dir: repo_path.join(".lit/packs"),
            catalog: Mutex::new(None),
            encryption: Mutex::new(None),
        }
    }

    /// Create an object store using the supplied credentials.
    pub fn new_with_encryption(repo_path: &Path, passphrase: Option<&str>) -> Result<Self, String> {
        let mut store = Self::new(repo_path);
        let manager =
            EncryptionManager::for_repository(repo_path, passphrase, passphrase.is_some())?;
        store.encryption = Mutex::new(Some(manager));
        Ok(store)
    }

    pub(crate) fn encryption_enabled(&self) -> Result<bool, String> {
        require_rotation_complete(&self.repo)?;
        Ok(EncryptionConfig::load(&self.repo)?.enabled)
    }

    pub(crate) fn with_cipher<T>(
        &self,
        create_key: bool,
        operation: impl FnOnce(&EncryptionManager) -> Result<T, String>,
    ) -> Result<T, String> {
        require_rotation_complete(&self.repo)?;
        let mut cipher = self.encryption.lock().map_err(|e| e.to_string())?;
        if cipher.is_none() {
            *cipher = Some(EncryptionManager::for_repository(
                &self.repo, None, create_key,
            )?);
        }
        let cipher = cipher.as_ref().expect("repository cipher was initialized");
        if create_key && !cipher.matches_config(&EncryptionConfig::load(&self.repo)?) {
            return Err(
                "Repository encryption changed. Reopen the object store before writing.".into(),
            );
        }
        operation(cipher)
    }

    /// Get the path for an object by its hash
    /// Uses first 4 chars for directory sharding (65,536 shards for better distribution)
    fn object_path(&self, hash: &ObjectHash) -> PathBuf {
        let hash_str = hash.as_str();
        let (dir, file) = hash_str.split_at(4);
        self.objects_dir.join(dir).join(file)
    }

    /// Write an object to storage
    pub fn write(&self, object: &Object) -> Result<ObjectHash, String> {
        let _key_guard = if self.encryption_enabled()? {
            Some(
                lock_key_writes(&self.repo, false)
                    .map_err(|e| format!("Lock encryption key: {e}"))?,
            )
        } else {
            None
        };
        let hash = object.hash();
        let path = self.object_path(&hash);

        // Create parent directory
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("Failed to create object directory: {}", e))?;
        }

        // Serialize and compress
        let data = object.to_bytes();
        let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
        encoder
            .write_all(&data)
            .map_err(|e| format!("Failed to compress object: {}", e))?;
        let compressed = encoder
            .finish()
            .map_err(|e| format!("Failed to finish compression: {}", e))?;

        let final_data = self.with_cipher(true, |cipher| cipher.encrypt(&compressed))?;

        fs::write(&path, final_data).map_err(|e| format!("Failed to write object: {}", e))?;

        Ok(hash)
    }

    /// Loose objects take precedence over packs.
    pub fn read(&self, hash: &ObjectHash) -> Result<Object, String> {
        if let Some(bytes) = self.loose_bytes(hash)? {
            return Object::from_bytes(&bytes);
        }
        self.read_packed(hash)
    }

    pub(crate) fn read_verified(&self, hash: &ObjectHash) -> Result<Object, String> {
        if let Some(bytes) = self.loose_bytes(hash)? {
            return Object::from_bytes(&verify_bytes(hash, bytes)?);
        }
        self.read_packed(hash)
    }

    pub(crate) fn verified_loose_bytes(&self, hash: &ObjectHash) -> Result<Vec<u8>, String> {
        let bytes = self
            .loose_bytes(hash)?
            .ok_or_else(|| format!("Loose object {} not found", hash.short()))?;
        verify_bytes(hash, bytes)
    }

    fn loose_bytes(&self, hash: &ObjectHash) -> Result<Option<Vec<u8>>, String> {
        require_rotation_complete(&self.repo)?;
        if !ObjectHash::is_full_hex(hash.as_str()) {
            return Err("Invalid full object ID".into());
        }
        let encrypted_data = match fs::read(self.object_path(hash)) {
            Ok(bytes) => bytes,
            // The object may have been packed since the caller enumerated it.
            Err(e) if e.kind() == ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(format!("Failed to read loose object: {e}")),
        };
        let compressed = self.with_cipher(false, |cipher| cipher.decrypt(&encrypted_data))?;
        Ok(Some(decompress_loose(&compressed)?))
    }

    pub(crate) fn validate_compressed(hash: &ObjectHash, compressed: &[u8]) -> Result<(), String> {
        Object::from_bytes(&verify_bytes(hash, decompress_loose(compressed)?)?)?;
        Ok(())
    }

    fn refresh_catalog(&self) -> Result<Arc<Catalog>, String> {
        let mut cache = self.catalog.lock().map_err(|e| e.to_string())?;
        let catalog = Catalog::scan(&self.packs_dir, None)?;
        *cache = Some(Arc::clone(&catalog));
        Ok(catalog)
    }

    fn catalog_for(&self, hash: &ObjectHash) -> Result<Arc<Catalog>, String> {
        let mut cache = self.catalog.lock().map_err(|e| e.to_string())?;
        if let Some(catalog) = cache
            .as_ref()
            .filter(|catalog| catalog.objects.contains_key(hash))
        {
            return Ok(Arc::clone(catalog));
        }
        // A miss may race with GC publishing a new index. Existing indexes
        // are immutable, so reread them only when the published set changes.
        let catalog = Catalog::scan(&self.packs_dir, cache.as_ref())?;
        *cache = Some(Arc::clone(&catalog));
        Ok(catalog)
    }

    fn read_packed(&self, hash: &ObjectHash) -> Result<Object, String> {
        let catalog = self.catalog_for(hash)?;
        if let Some(locations) = catalog.objects.get(hash) {
            let mut error = None;
            for location in locations {
                match self.read_pack_copy(hash, location) {
                    Ok(object) => return Ok(object),
                    Err(failure) => {
                        error.get_or_insert(failure);
                    }
                }
            }
            if let Some(error) = error {
                return Err(error);
            }
        }
        if let Some(error) = catalog.errors.first() {
            return Err(format!(
                "Cannot locate object {} in native packs: {error}",
                hash.short()
            ));
        }
        Err(format!("Object {} not found", hash.short()))
    }

    fn read_pack_copy(&self, hash: &ObjectHash, location: &Location) -> Result<Object, String> {
        self.with_cipher(false, |cipher| location.read(hash, Some(cipher)))
    }

    /// Check whether a loose or indexed object is available.
    pub fn exists(&self, hash: &ObjectHash) -> bool {
        if !ObjectHash::is_full_hex(hash.as_str()) || require_rotation_complete(&self.repo).is_err()
        {
            return false;
        }
        self.object_path(hash).is_file()
            || self
                .catalog_for(hash)
                .is_ok_and(|catalog| catalog.objects.contains_key(hash))
    }

    /// List unique object IDs across loose objects and published packs.
    pub fn list(&self) -> Result<Vec<ObjectHash>, String> {
        let mut objects: HashSet<_> = self.list_loose()?.into_iter().collect();
        let catalog = self.refresh_catalog()?;
        if let Some(error) = catalog.errors.first() {
            return Err(error.clone());
        }
        objects.extend(catalog.objects.keys().cloned());
        let mut objects: Vec<_> = objects.into_iter().collect();
        objects.sort_unstable_by(|left, right| left.as_str().cmp(right.as_str()));
        Ok(objects)
    }

    /// Visit each physical copy so a duplicate cannot hide a damaged pack.
    pub(crate) fn visit_verified(
        &self,
        migration: bool,
        mut visit: impl FnMut(&ObjectHash, Result<Object, String>),
    ) -> Result<Vec<String>, String> {
        let loose = self.list_loose()?;
        let catalog = self.refresh_catalog()?;
        for hash in &loose {
            let result = self.loose_bytes(hash).and_then(|bytes| match bytes {
                Some(bytes) => Object::from_bytes(&verify_bytes(hash, bytes)?),
                None => self.read_packed(hash),
            });
            visit(hash, result);
        }
        let mut packed: Vec<_> = catalog.objects.iter().collect();
        packed.sort_unstable_by(|(left, _), (right, _)| left.as_str().cmp(right.as_str()));
        for (hash, locations) in packed {
            for location in locations {
                let result = if migration {
                    self.with_cipher(false, |cipher| location.read_for_migration(hash, cipher))
                } else {
                    self.read_pack_copy(hash, location)
                };
                visit(hash, result);
            }
        }
        Ok(catalog.errors.clone())
    }

    pub(crate) fn list_loose(&self) -> Result<Vec<ObjectHash>, String> {
        let mut objects = Vec::new();
        for entry in walkdir::WalkDir::new(&self.objects_dir)
            .min_depth(2)
            .max_depth(2)
        {
            let entry = match entry {
                Ok(entry) => entry,
                Err(e)
                    if e.io_error()
                        .is_some_and(|e| e.kind() == ErrorKind::NotFound) =>
                {
                    continue
                }
                Err(e) => return Err(format!("Failed to list loose objects: {e}")),
            };
            if entry.file_type().is_file() {
                let shard = entry
                    .path()
                    .parent()
                    .and_then(Path::file_name)
                    .ok_or("Loose object is missing a shard name")?;
                let shard = shard.to_string_lossy();
                let name = entry.file_name().to_string_lossy();
                if shard.len() != 4 || name.len() != 188 {
                    return Err(format!(
                        "Invalid loose object path {}",
                        entry.path().display()
                    ));
                }
                objects.push(ObjectHash::from_hex(format!("{shard}{name}")));
            }
        }
        objects.sort_unstable_by(|left, right| left.as_str().cmp(right.as_str()));
        Ok(objects)
    }

    pub(crate) fn remove_loose(&self, hash: &ObjectHash) -> Result<bool, String> {
        match fs::remove_file(self.object_path(hash)) {
            Ok(()) => Ok(true),
            Err(error) if error.kind() == ErrorKind::NotFound => Ok(false),
            Err(error) => Err(format!(
                "Cannot remove loose object {} after packing: {error}",
                hash.short()
            )),
        }
    }

    pub(crate) fn loose_size(&self, hash: &ObjectHash) -> Result<u64, String> {
        fs::metadata(self.object_path(hash))
            .map(|metadata| metadata.len())
            .map_err(|error| format!("Cannot measure loose object {}: {error}", hash.short()))
    }
}

fn decompress_loose(compressed: &[u8]) -> Result<Vec<u8>, String> {
    let mut decoder = ZlibDecoder::new(compressed);
    let mut data = Vec::new();
    decoder
        .read_to_end(&mut data)
        .map_err(|e| format!("Failed to decompress object: {e}"))?;
    Ok(data)
}

fn verify_bytes(hash: &ObjectHash, bytes: Vec<u8>) -> Result<Vec<u8>, String> {
    if ObjectHash::from_bytes(&bytes) != *hash {
        return Err("Stored content does not match its object ID".into());
    }
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::Blob;
    use tempfile::TempDir;

    #[test]
    fn test_object_store() {
        let temp_dir = TempDir::new().unwrap();
        let repo_path = temp_dir.path();

        // Create .lit/objects directory
        fs::create_dir_all(repo_path.join(".lit").join("objects")).unwrap();

        let store = ObjectStore::new(repo_path);

        // Create and write a blob
        let content = b"Hello, world!".to_vec();
        let blob = Blob::new(content.clone());
        let object = Object::Blob(blob);

        let hash = store.write(&object).unwrap();

        // Read it back
        let read_object = store.read(&hash).unwrap();

        match read_object {
            Object::Blob(blob) => assert_eq!(blob.content, content),
            _ => panic!("Expected blob"),
        }
    }
}
