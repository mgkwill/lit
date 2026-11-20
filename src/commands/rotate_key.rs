//! Re-encrypt repository data under a new, repository-local key.
use crate::core::refs;
use crate::crypto::encryption::{
    clear_passphrase_cache, lock_key_writes, prompt_for_new_passphrase, prompt_for_passphrase,
    EncryptionConfig, EncryptionKey, EncryptionManager, ROTATION_DIR,
};
use crate::errors::LitError;
use crate::response::RotateKeyResponse;
use crate::storage::{pack, ObjectStore};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Component, Path, PathBuf};

#[derive(Serialize, Deserialize)]
struct Rotation {
    files: Vec<Replacement>,
    objects_rotated: usize,
    refs_rotated: usize,
}

#[derive(Serialize, Deserialize)]
struct Replacement {
    path: PathBuf,
    digest: String,
}

pub fn rotate_key() -> Result<RotateKeyResponse, LitError> {
    let repo = refs::find_repo_root()?;

    at(&repo, None)
}

pub(super) fn at(
    repo: &Path,
    packed_refs: Option<&BTreeMap<String, String>>,
) -> Result<RotateKeyResponse, LitError> {
    let _guard = super::transaction::lock_repository(repo)?;
    let journal = repo.join(ROTATION_DIR);
    match journal.symlink_metadata() {
        Ok(metadata) if metadata.is_dir() => {
            let _key_guard = lock_key_writes(repo, true)?;
            let passphrase = prompt_for_new_passphrase()?;
            return finish(repo, &journal, &passphrase, |_| Ok(()));
        }
        Ok(_) => return Err("Expected key rotation journal to be a directory".into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }
    super::merge::require_inactive(repo)?;
    if repo.join(".lit/transaction.json").try_exists()? {
        return Err("Finish the active transaction before rotating the key.".into());
    }

    let config = EncryptionConfig::load(repo)?;
    if !config.enabled {
        return Err("Encryption is not enabled for this repository".into());
    }
    let _key_guard = lock_key_writes(repo, true)?;
    let old_passphrase = prompt_for_passphrase(
        repo.to_str().ok_or("Repository path is not UTF-8")?,
        &config,
        "Enter current passphrase: ",
    )?;
    let old = EncryptionManager::open_existing(config.clone(), &old_passphrase)?;
    let new_passphrase = prompt_for_new_passphrase()?;
    if *old_passphrase == *new_passphrase {
        return Err("Choose a new passphrase different from the current one.".into());
    }

    let key = EncryptionKey::from_passphrase(&new_passphrase, &EncryptionKey::generate_salt())?;
    prepare(repo, config, &old, &key, &new_passphrase, packed_refs)?;
    finish(repo, &journal, &new_passphrase, |_| Ok(()))
}

fn prepare(
    repo: &Path,
    mut config: EncryptionConfig,
    old: &EncryptionManager,
    key: &EncryptionKey,
    passphrase: &str,
    packed_refs: Option<&BTreeMap<String, String>>,
) -> Result<(), LitError> {
    let metadata = repo.join(".lit");
    let workspace = tempfile::tempdir_in(&metadata)?;
    let stage = workspace.path();
    let files = stage.join("files");
    fs::create_dir(&files)?;
    config.key_file = format!(".lit/keys/{}.key", uuid::Uuid::new_v4());
    let key_relative = Path::new(&config.key_file)
        .strip_prefix(".lit")
        .map_err(|e| e.to_string())?;
    let replacement_key = files.join(key_relative);
    fs::create_dir_all(replacement_key.parent().ok_or("Missing key directory")?)?;
    key.save(
        replacement_key
            .to_str()
            .ok_or("Replacement key path is not UTF-8")?,
        passphrase,
    )?;

    let new = EncryptionManager::from_key(config.clone(), key)?;
    let mut paths = vec![key_relative.to_path_buf()];
    let store = ObjectStore::new(repo);
    let mut objects_rotated = 0;
    for hash in store.list_loose()? {
        let id = hash.as_str();
        if !crate::core::ObjectHash::is_full_hex(id) {
            return Err("Invalid loose object ID during key rotation".into());
        }
        let path = Path::new("objects").join(&id[..4]).join(&id[4..]);
        let source = checked_path(&metadata, &path)?;
        let bytes = old.decrypt(&fs::read(source)?)?;
        ObjectStore::validate_compressed(&hash, &bytes)?;
        stage_bytes(&files.join(&path), &new.encrypt(&bytes)?)?;
        paths.push(path);
        objects_rotated += 1;
    }
    let packs = metadata.join("packs");
    let mut indexes = Vec::new();
    if packs.try_exists()? {
        for file in fs::read_dir(&packs)? {
            let file = file?;
            if file.path().extension().is_some_and(|value| value == "idx") {
                indexes.push(Path::new("packs").join(file.file_name()));
            }
        }
    }
    indexes.sort();
    for index in indexes {
        let old_index = checked_path(&metadata, &index)?;
        let new_index = files.join(&index);
        fs::create_dir_all(new_index.parent().ok_or("Missing pack directory")?)?;
        objects_rotated += pack::rotate(&old_index, &new_index, old, &new)?;
        paths.push(index.with_extension("pack"));
        paths.push(index);
    }

    let index = Path::new("index");
    if metadata.join(index).try_exists()? {
        let bytes = fs::read(checked_path(&metadata, index)?)?;
        let plaintext = old.decrypt(&bytes)?;
        stage_bytes(&files.join(index), &new.encrypt(&plaintext)?)?;
        paths.push(index.into());
    }

    let mut refs_rotated = 0;
    let mut references = vec![PathBuf::from("HEAD")];
    let refs = metadata.join("refs");
    if refs.try_exists()? {
        for entry in walkdir::WalkDir::new(&refs).sort_by_file_name() {
            let entry = entry.map_err(|e| e.to_string())?;
            if !entry.file_type().is_dir() {
                if packed_refs.is_some_and(|packed| {
                    entry
                        .path()
                        .strip_prefix(&refs)
                        .ok()
                        .and_then(Path::to_str)
                        .is_some_and(|name| packed.contains_key(name))
                }) {
                    continue;
                }
                references.push(
                    entry
                        .path()
                        .strip_prefix(&metadata)
                        .map_err(|e| e.to_string())?
                        .to_owned(),
                );
            }
        }
    }
    for path in references {
        let source = checked_path(&metadata, &path)?;
        let bytes = match fs::read(source) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == io::ErrorKind::NotFound && path == Path::new("HEAD") => {
                continue
            }
            Err(error) => return Err(error.into()),
        };
        if refs::is_encrypted_ref(&bytes) {
            stage_bytes(&files.join(&path), &new.encrypt(&old.decrypt(&bytes)?)?)?;
            paths.push(path);
            refs_rotated += 1;
        } else {
            // Native text refs retain their existing on-disk representation.
            std::str::from_utf8(&bytes)
                .map_err(|e| format!("Invalid reference {}: {e}", path.display()))?;
        }
    }
    if let Some(packed) = packed_refs {
        for (name, target) in packed {
            super::remote::validate_ref(name)?;
            let path = Path::new("refs").join(name);
            let staged = files.join(&path);
            if staged.try_exists()? {
                return Err(format!("Reference path collision while migrating {name}").into());
            }
            stage_bytes(&staged, &new.encrypt(format!("{target}\n").as_bytes())?)?;
            paths.push(path);
            refs_rotated += 1;
        }
    }

    let config_path = PathBuf::from("encryption.toml");
    stage_bytes(
        &files.join(&config_path),
        toml::to_string_pretty(&config)
            .map_err(|e| e.to_string())?
            .as_bytes(),
    )?;
    paths.push(config_path);
    let files = paths
        .into_iter()
        .map(|path| {
            let digest = file_digest(&files.join(&path))?;
            Ok(Replacement { path, digest })
        })
        .collect::<Result<Vec<_>, LitError>>()?;
    let manifest = Rotation {
        files,
        objects_rotated,
        refs_rotated,
    };
    let bytes = serde_json::to_vec(&manifest).map_err(|e| e.to_string())?;
    stage_bytes(&stage.join("manifest"), &new.encrypt(&bytes)?)?;
    // The staged verifier allows authenticated recovery after the config changes.
    fs::copy(&replacement_key, stage.join("replacement.key"))?;
    File::open(stage.join("replacement.key"))?.sync_all()?;
    File::open(replacement_key)?.sync_all()?;
    for entry in walkdir::WalkDir::new(stage).contents_first(true) {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry.file_type().is_dir() {
            super::gc::sync_directory(entry.path())?;
        }
    }

    fs::rename(stage, repo.join(ROTATION_DIR))?;
    super::gc::sync_directory(&metadata)?;
    Ok(())
}

fn finish(
    repo: &Path,
    journal: &Path,
    passphrase: &str,
    mut after_replacement: impl FnMut(&Path) -> Result<(), LitError>,
) -> Result<RotateKeyResponse, LitError> {
    let verifier = journal.join("replacement.key");
    let unlock = EncryptionConfig {
        enabled: true,
        key_file: verifier
            .to_str()
            .ok_or("Replacement key path is not UTF-8")?
            .to_owned(),
        ..EncryptionConfig::default()
    };
    let manager = EncryptionManager::open_existing(unlock, passphrase)?;
    let bytes = manager.decrypt(&fs::read(journal.join("manifest"))?)?;
    let manifest: Rotation = serde_json::from_slice(&bytes)
        .map_err(|e| format!("Invalid key rotation manifest: {e}"))?;
    if !manifest
        .files
        .last()
        .is_some_and(|file| file.path == Path::new("encryption.toml"))
    {
        return Err("Key rotation manifest must publish its config last".into());
    }
    let metadata = repo.join(".lit");
    let files = journal.join("files");
    let mut replacements = Vec::new();
    for file in &manifest.files {
        let source = checked_path(&files, &file.path)?;
        let destination = checked_path(&metadata, &file.path)?;
        if file_digest(&source)? != file.digest {
            return Err(format!(
                "Key rotation checkpoint is damaged at {}",
                file.path.display()
            )
            .into());
        }
        replacements.push((source, destination));
    }
    for (source, destination) in replacements {
        install_file(&source, &destination)?;
        after_replacement(&destination)?;
    }

    let retired = tempfile::tempdir_in(&metadata)?;
    fs::rename(journal, retired.path().join("completed"))?;
    super::gc::sync_directory(&metadata)?;
    clear_passphrase_cache();
    Ok(RotateKeyResponse {
        objects_rotated: manifest.objects_rotated,
        refs_rotated: manifest.refs_rotated,
    })
}

fn checked_path(root: &Path, relative: &Path) -> Result<PathBuf, LitError> {
    let mut path = root.to_owned();
    for part in relative.components() {
        let Component::Normal(part) = part else {
            return Err("Invalid path in key rotation".into());
        };
        path.push(part);
        match path.symlink_metadata() {
            Ok(meta) if meta.is_symlink() => {
                return Err(format!("Symlink obstructs key rotation at {}", path.display()).into());
            }
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    if path == root {
        return Err("Empty key rotation path".into());
    }
    Ok(path)
}

fn file_digest(path: &Path) -> Result<String, LitError> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    io::copy(&mut file, &mut digest)?;
    Ok(hex::encode(digest.finalize()))
}

fn stage_bytes(path: &Path, bytes: &[u8]) -> Result<(), LitError> {
    let parent = path
        .parent()
        .ok_or("Missing key rotation staging directory")?;
    fs::create_dir_all(parent)?;
    let mut file = File::create(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

fn install_file(source: &Path, destination: &Path) -> Result<(), LitError> {
    let parent = destination
        .parent()
        .ok_or("Missing key rotation destination")?;
    fs::create_dir_all(parent)?;
    let mut staged = tempfile::NamedTempFile::new_in(parent)?;
    io::copy(&mut File::open(source)?, &mut staged)?;
    staged.as_file().sync_all()?;
    staged.persist(destination).map_err(|e| e.error)?;
    super::gc::sync_directory(parent)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::{Blob, Object};

    #[test]
    fn interrupted_rotation_resumes_after_first_object_replacement() {
        let root = tempfile::tempdir().unwrap();
        let repo = root.path();
        super::super::init::execute(false, Some(repo.to_str().unwrap().into())).unwrap();
        let old_pass = "test-rotation-interrupt-old-A1!";
        let new_pass = "test-rotation-interrupt-new-A1!";
        let config = EncryptionConfig {
            enabled: true,
            key_file: repo.join("shared.key").to_str().unwrap().into(),
            ..EncryptionConfig::default()
        };
        config.save(repo).unwrap();
        let old_key =
            EncryptionKey::from_passphrase(old_pass, &EncryptionKey::generate_salt()).unwrap();
        old_key.save(&config.key_file, old_pass).unwrap();
        let old = EncryptionManager::from_key(config.clone(), &old_key).unwrap();
        stage_bytes(
            &repo.join(".lit/index"),
            &old.encrypt(b"{\"entries\":{}}").unwrap(),
        )
        .unwrap();

        let blob = Object::Blob(Blob::new(b"rotation checkpoint".to_vec()));
        let store = ObjectStore::new_with_encryption(repo, Some(old_pass)).unwrap();
        let hash = store.write(&blob).unwrap();
        let exclusive = lock_key_writes(repo, true).unwrap();
        let writer = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(repo.join(".lit/encryption.guard"))
            .unwrap();
        assert!(matches!(
            writer.try_lock_shared(),
            Err(fs::TryLockError::WouldBlock)
        ));
        drop(exclusive);
        let new_key =
            EncryptionKey::from_passphrase(new_pass, &EncryptionKey::generate_salt()).unwrap();
        prepare(repo, config, &old, &new_key, new_pass, None).unwrap();
        let journal = repo.join(ROTATION_DIR);
        let before = fs::read(repo.join(".lit/encryption.toml")).unwrap();
        let error = finish(repo, &journal, new_pass, |path| {
            if path.starts_with(repo.join(".lit/objects")) {
                return Err("injected interruption after object replacement".into());
            }
            Ok(())
        })
        .unwrap_err();
        assert!(error.internal_message().contains("injected interruption"));
        assert_eq!(fs::read(repo.join(".lit/encryption.toml")).unwrap(), before);
        assert!(store
            .read(&hash)
            .unwrap_err()
            .contains("rotation is in progress"));
        let index = journal.join("files/index");
        let valid = fs::read(&index).unwrap();
        fs::write(&index, b"tampered staging file").unwrap();
        assert!(finish(repo, &journal, new_pass, |_| Ok(()))
            .unwrap_err()
            .internal_message()
            .contains("checkpoint is damaged"));
        fs::write(index, valid).unwrap();
        assert_eq!(
            finish(repo, &journal, new_pass, |_| Ok(()))
                .unwrap()
                .objects_rotated,
            1
        );
        assert!(!journal.exists());
        let store = ObjectStore::new_with_encryption(repo, Some(new_pass)).unwrap();
        assert_eq!(store.read(&hash).unwrap().to_bytes(), blob.to_bytes());
    }
}
