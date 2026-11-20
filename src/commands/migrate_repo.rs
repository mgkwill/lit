use super::verify;
use super::worktree::publish_new_directory as promote;
use crate::core::repository_format::{self, Receipt};
use crate::crypto::encryption::{EncryptionConfig, EncryptionManager};
use crate::errors::LitError;
use crate::response::{CommandResponse, VerifyResponse};
use clap::Subcommand;
use serde::de::{Error as _, MapAccess, Visitor};
use serde::{Deserializer, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs::{self, DirBuilder, File, OpenOptions, Permissions};
use std::io::{self, ErrorKind, Read, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{symlink, DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use walkdir::WalkDir;

#[derive(Subcommand)]
pub enum Action {
    /// Verify a source repository without writing to it
    Inspect { source: PathBuf },
    /// Verify and publish a separate recovery copy
    Copy {
        source: PathBuf,
        destination: PathBuf,
    },
    /// Archive the recovery copy so work can resume in the original repository
    Rollback {
        original: PathBuf,
        migrated: PathBuf,
        archive: PathBuf,
    },
}

#[derive(Serialize)]
pub struct Response {
    action: &'static str,
    source: String,
    destination: Option<String>,
    format_version: u32,
    source_sha256: String,
    key_rotated: bool,
    key_rotation_required: bool,
    verification: Option<VerifyResponse>,
}

struct Verified {
    report: VerifyResponse,
    key_rotated: bool,
    key_rotation_required: bool,
}

impl CommandResponse for Response {
    fn command_name(&self) -> &'static str {
        "migrate-repo"
    }

    fn human_readable(&self) -> String {
        let mut output = format!(
            "{} {} (format {}, SHA-256 {})",
            self.action, self.source, self.format_version, self.source_sha256
        );
        if let Some(destination) = &self.destination {
            output.push_str(&format!(" -> {destination}"));
        }
        output
    }

    fn to_json_output(&self) -> String {
        let mut value = serde_json::to_value(self).expect("migration response serializes");
        value["status"] = "ok".into();
        value["command"] = self.command_name().into();
        serde_json::to_string_pretty(&value).expect("migration response serializes")
    }
}

pub fn execute(action: Action) -> Result<Response, LitError> {
    match action {
        Action::Inspect { source } => {
            let source = repository(&source)?;
            let receipt = fingerprint(&source)?;
            let scratch = tempfile::Builder::new()
                .permissions(Permissions::from_mode(0o700))
                .tempdir()?;
            let copy = scratch.path().join("repository");
            let verified = verify_copy(&source, &copy, &receipt, false)?;
            Ok(response("inspect", &source, None, &receipt, Some(verified)))
        }
        Action::Copy {
            source,
            destination,
        } => migrate(&source, &destination),
        Action::Rollback {
            original,
            migrated,
            archive,
        } => rollback(&original, &migrated, &archive),
    }
}

fn migrate(source: &Path, destination: &Path) -> Result<Response, LitError> {
    let source = repository(source)?;
    let (parent, destination) = new_path(destination)?;
    separate(&source, &destination)?;
    let parent_lock = lock_parent(&parent)?;

    let receipt = fingerprint(&source)?;
    let intent = serde_json::to_vec(&receipt).map_err(|error| LitError::io(error.to_string()))?;
    let stage = parent.join(format!(".lit-migration-{}", path_digest(&destination)));
    separate(&source, &stage)?;
    prepare_stage(&stage, &intent)?;
    let copy = stage.join("repository");

    // A previous process can leave a partial copy. Rebuild it from the pinned
    // source, keeping publication a single atomic step.
    remove_staged_copy(&copy)?;
    let verified = verify_copy(&source, &copy, &receipt, true)?;
    let marker = copy.join(repository_format::FILE);
    let mut file = tempfile::NamedTempFile::new_in(copy.join(".lit"))?;
    file.write_all(&intent)?;
    file.as_file()
        .set_permissions(Permissions::from_mode(0o644))?;
    file.as_file().sync_all()?;
    file.persist(&marker).map_err(|error| error.error)?;
    File::open(copy.join(".lit"))?.sync_all()?;
    promote(&copy, &destination)?;
    parent_lock.sync_all()?;

    fs::remove_file(stage.join("intent.json"))?;
    fs::remove_dir(stage)?;
    parent_lock.sync_all()?;
    Ok(response(
        "copy",
        &source,
        Some(&destination),
        &receipt,
        Some(verified),
    ))
}

fn rollback(original: &Path, migrated: &Path, archive: &Path) -> Result<Response, LitError> {
    let original = repository(original)?;
    let migrated = repository(migrated)?;
    let (parent, archive) = new_path(archive)?;
    for (left, right) in [
        (&original, &migrated),
        (&original, &archive),
        (&migrated, &archive),
    ] {
        separate(left, right)?;
    }

    let migrated_parent = migrated
        .parent()
        .ok_or_else(|| LitError::repository("Invalid migrated path"))?;
    let archive_lock = lock_parent(&parent)?;
    // Avoid locking two parent directories in opposite orders. An archive
    // alongside the migrated repo also guarantees a same-filesystem rename.
    if parent != migrated_parent {
        return Err(LitError::repository(
            "Rollback archive must be a sibling of the migrated repository",
        ));
    }
    let receipt = repository_format::read(&migrated)?
        .ok_or_else(|| LitError::repository("Migrated repository has no migration receipt"))?;
    if fingerprint(&original)? != receipt {
        return Err(LitError::repository(
            "Original repository differs from the migration snapshot",
        ));
    }
    promote(&migrated, &archive)?;
    archive_lock.sync_all()?;
    Ok(response(
        "rollback",
        &original,
        Some(&archive),
        &receipt,
        None,
    ))
}

fn response(
    action: &'static str,
    source: &Path,
    destination: Option<&Path>,
    receipt: &Receipt,
    verified: Option<Verified>,
) -> Response {
    Response {
        action,
        source: source.display().to_string(),
        destination: destination.map(|path| path.display().to_string()),
        format_version: receipt.version,
        source_sha256: receipt.source_sha256.clone(),
        key_rotated: verified.as_ref().is_some_and(|result| result.key_rotated),
        key_rotation_required: verified
            .as_ref()
            .is_some_and(|result| result.key_rotation_required),
        verification: verified.map(|result| result.report),
    }
}

fn repository(path: &Path) -> Result<PathBuf, LitError> {
    let path = path.canonicalize()?;
    if !path.join(".lit").symlink_metadata()?.is_dir() {
        return Err(LitError::repository("Expected a native .lit directory"));
    }
    repository_format::read(&path)?;
    Ok(path)
}

fn new_path(path: &Path) -> Result<(PathBuf, PathBuf), LitError> {
    let name = path
        .file_name()
        .ok_or_else(|| LitError::repository("Missing destination name"))?;
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let parent = parent.canonicalize()?;
    let destination = parent.join(name);
    match destination.symlink_metadata() {
        Ok(_) => Err(LitError::repository(format!(
            "Destination already exists: {}",
            destination.display()
        ))),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok((parent, destination)),
        Err(error) => Err(error.into()),
    }
}

fn separate(left: &Path, right: &Path) -> Result<(), LitError> {
    if left.starts_with(right) || right.starts_with(left) {
        return Err(LitError::repository(
            "Migration paths must be separate directories",
        ));
    }
    Ok(())
}

fn lock_parent(parent: &Path) -> Result<File, LitError> {
    let lock = File::open(parent)?;
    lock.try_lock().map_err(|error| {
        LitError::io(format!(
            "Migration destination directory is busy or cannot be locked: {error}"
        ))
    })?;
    Ok(lock)
}

fn path_digest(path: &Path) -> String {
    hex::encode(Sha256::digest(path.as_os_str().as_bytes()))
}

fn fingerprint(source: &Path) -> Result<Receipt, LitError> {
    Ok(Receipt {
        version: repository_format::VERSION,
        original_path_sha256: path_digest(source),
        source_sha256: snapshot(source, None)?,
    })
}

fn prepare_stage(stage: &Path, intent: &[u8]) -> Result<(), LitError> {
    let meta = match stage.symlink_metadata() {
        Ok(meta) => meta,
        Err(error) if error.kind() == ErrorKind::NotFound => {
            let parent = stage.parent().expect("stage has a parent");
            let workspace = tempfile::Builder::new()
                .permissions(Permissions::from_mode(0o700))
                .tempdir_in(parent)?;
            let mut file = File::create(workspace.path().join("intent.json"))?;
            file.write_all(intent)?;
            file.sync_all()?;
            File::open(workspace.path())?.sync_all()?;
            promote(workspace.path(), stage)?;
            File::open(parent)?.sync_all()?;
            return Ok(());
        }
        Err(error) => return Err(error.into()),
    };
    if !meta.is_dir() || meta.uid() != unsafe { libc::geteuid() } || meta.mode() & 0o077 != 0 {
        return Err(LitError::repository(
            "Migration staging directory is not private and owned",
        ));
    }

    let mut saved = Vec::new();
    open_regular(&stage.join("intent.json"))?
        .take(intent.len() as u64 + 1)
        .read_to_end(&mut saved)?;
    if saved != intent {
        return Err(LitError::repository(format!(
            "Staged migration has a different source snapshot. Preserve the staged data at {}.",
            stage.display()
        )));
    }
    Ok(())
}

fn remove_staged_copy(copy: &Path) -> Result<(), LitError> {
    match copy.symlink_metadata() {
        Ok(meta) if meta.is_dir() => {}
        Ok(_) => return Err(LitError::repository("Invalid migration staging tree")),
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error.into()),
    }
    for entry in WalkDir::new(copy) {
        let entry = entry.map_err(|error| LitError::io(error.to_string()))?;
        if entry.file_type().is_dir() {
            let mode = entry.path().symlink_metadata()?.permissions().mode();
            fs::set_permissions(entry.path(), Permissions::from_mode(mode | 0o700))?;
        }
    }
    fs::remove_dir_all(copy)?;
    Ok(())
}

fn verify_copy(
    source: &Path,
    copy: &Path,
    receipt: &Receipt,
    publish: bool,
) -> Result<Verified, LitError> {
    if snapshot(source, Some(copy))? != receipt.source_sha256
        || snapshot(copy, None)? != receipt.source_sha256
    {
        return Err(LitError::repository(
            "Source changed or staged copy did not match the original",
        ));
    }
    for path in [
        ".lit/merge",
        ".lit/transaction.json",
        ".lit/transaction.lock",
        ".lit/key-rotation",
        ".lit/rebase",
        ".lit/bisect.json",
    ] {
        match copy.join(path).symlink_metadata() {
            Ok(_) => {
                return Err(LitError::repository(format!(
                    "Finish or abort the active operation before migration ({path})"
                )))
            }
            Err(error) if error.kind() == ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    let legacy = read_legacy_encryption(copy)?;
    let rotation_required = legacy.is_some();
    let new_passphrase = if rotation_required && publish {
        let path = std::env::var_os("LIT_NEW_PASSPHRASE_FILE").ok_or_else(|| {
            LitError::encryption(
                "Legacy encryption requires rotation on the copy. Set LIT_NEW_PASSPHRASE_FILE.",
            )
        })?;
        Some(Path::new(&path).canonicalize()?)
    } else {
        None
    };
    let before = verify::at(copy, legacy.as_ref());
    require_valid(&before)?;
    let report = if let Some(path) = &new_passphrase {
        super::rotate_key::at(copy, legacy.as_ref())?;
        let table = copy.join(repository_format::LEGACY_REFS_FILE);
        if table.try_exists()? {
            fs::remove_file(table)?;
        }
        let after = verify_with_rotated_key(copy, path)?;
        require_valid(&after)?;
        if after.refs_checked != before.refs_checked
            || after.objects_checked != before.objects_checked
        {
            return Err(LitError::repository(
                "Migration changed the verified object or ref count",
            ));
        }
        after
    } else {
        before
    };
    if snapshot(source, None)? != receipt.source_sha256 {
        return Err(LitError::repository(
            "Source changed before migration completed",
        ));
    }
    Ok(Verified {
        report,
        key_rotated: new_passphrase.is_some(),
        key_rotation_required: rotation_required,
    })
}

fn require_valid(verification: &VerifyResponse) -> Result<(), LitError> {
    if !verification.valid {
        let failures: Vec<_> = verification
            .checks
            .iter()
            .filter(|check| check.status != "ok")
            .map(|check| {
                format!(
                    "{}: {}",
                    check.check,
                    check.details.as_deref().unwrap_or("verification failed")
                )
            })
            .collect();
        return Err(LitError::repository(failures.join("; ")));
    }
    Ok(())
}

fn read_legacy_encryption(copy: &Path) -> Result<Option<BTreeMap<String, String>>, LitError> {
    let config: EncryptionConfig = match fs::read_to_string(copy.join(".lit/encryption.toml")) {
        Ok(contents) => {
            toml::from_str(&contents).map_err(|error| LitError::config(error.to_string()))?
        }
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    if config.enabled {
        let key = Path::new(&config.key_file);
        if !key.starts_with(".lit")
            || key
                .components()
                .any(|part| !matches!(part, Component::Normal(_)))
        {
            return Err(LitError::encryption(
                "Migration requires a repository-local key under .lit",
            ));
        }
        let manager =
            EncryptionManager::for_repository(copy, None, false).map_err(LitError::encryption)?;
        let table = match fs::read(copy.join(repository_format::LEGACY_REFS_FILE)) {
            Ok(bytes) => Some(decode_ref_table(
                &manager.decrypt(&bytes).map_err(LitError::encryption)?,
            )?),
            Err(error) if error.kind() == ErrorKind::NotFound => None,
            Err(error) => return Err(error.into()),
        };
        if table.is_some()
            || manager.has_legacy_key()
            || crate::storage::pack::has_plaintext_pack(copy)?
        {
            return Ok(Some(table.unwrap_or_default()));
        }
    }
    Ok(None)
}

fn decode_ref_table(bytes: &[u8]) -> Result<BTreeMap<String, String>, LitError> {
    struct UniqueRefs;
    impl<'de> Visitor<'de> for UniqueRefs {
        type Value = BTreeMap<String, String>;

        fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str("an object with unique reference names")
        }

        fn visit_map<M: MapAccess<'de>>(self, mut map: M) -> Result<Self::Value, M::Error> {
            let mut refs = BTreeMap::new();
            while let Some((name, target)) = map.next_entry::<String, String>()? {
                super::remote::validate_ref(&name).map_err(M::Error::custom)?;
                if !crate::core::ObjectHash::is_full_hex(&target) {
                    return Err(M::Error::custom(format!(
                        "Invalid target for reference {name}"
                    )));
                }
                if refs.insert(name, target).is_some() {
                    return Err(M::Error::custom("Duplicate reference"));
                }
            }
            Ok(refs)
        }
    }

    let mut parser = serde_json::Deserializer::from_slice(bytes);
    let refs = parser
        .deserialize_map(UniqueRefs)
        .map_err(|error| LitError::repository(error.to_string()))?;
    parser
        .end()
        .map_err(|error| LitError::repository(error.to_string()))?;
    Ok(refs)
}

fn verify_with_rotated_key(copy: &Path, passphrase: &Path) -> Result<VerifyResponse, LitError> {
    let output = Command::new(std::env::current_exe()?)
        .args(["--json", "verify"])
        .current_dir(copy)
        .env_remove("LIT_PASSPHRASE")
        .env("LIT_PASSPHRASE_FILE", passphrase)
        .output()?;
    if !output.status.success() {
        return Err(LitError::repository(
            "Unable to verify the copy with the rotated key",
        ));
    }
    serde_json::from_slice(&output.stdout).map_err(|error| LitError::repository(error.to_string()))
}

fn open_regular(path: &Path) -> io::Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)?;
    if !file.metadata()?.is_file() {
        return Err(io::Error::new(
            ErrorKind::InvalidData,
            format!("Expected regular file at {}", path.display()),
        ));
    }
    Ok(file)
}

fn field(hasher: &mut Sha256, bytes: &[u8]) {
    hasher.update((bytes.len() as u64).to_be_bytes());
    hasher.update(bytes);
}

fn snapshot(root: &Path, destination: Option<&Path>) -> Result<String, LitError> {
    let mut hasher = Sha256::new();
    hasher.update(b"lit-repository-snapshot-v1\0");
    let mut dirs = Vec::new();
    for entry in WalkDir::new(root)
        .sort_by_file_name()
        .follow_root_links(false)
    {
        let entry = entry.map_err(|error| LitError::io(error.to_string()))?;
        let path = entry.path();
        let name = path.strip_prefix(root).expect("entry below snapshot root");
        let mode = path.symlink_metadata()?.permissions().mode() & 0o777;
        let output = destination.map(|directory| directory.join(name));
        field(&mut hasher, name.as_os_str().as_bytes());
        hasher.update(mode.to_be_bytes());
        let kind = entry.file_type();
        if kind.is_dir() {
            hasher.update(b"d");
            if let Some(output) = output {
                DirBuilder::new().mode(0o700).create(&output)?;
                dirs.push((output, mode));
            }
        } else if kind.is_file() {
            hasher.update(b"f");
            let mut input = open_regular(path)?;
            let mut content = Sha256::new();
            if let Some(output) = output {
                let mut file = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(output)?;
                let mut buffer = [0; 64 * 1024];
                loop {
                    let size = input.read(&mut buffer)?;
                    if size == 0 {
                        break;
                    }
                    content.update(&buffer[..size]);
                    file.write_all(&buffer[..size])?;
                }
                file.set_permissions(Permissions::from_mode(mode))?;
                file.sync_all()?;
            } else {
                io::copy(&mut input, &mut content)?;
            }
            hasher.update(content.finalize());
        } else if kind.is_symlink() && !name.starts_with(".lit") {
            hasher.update(b"l");
            let target = fs::read_link(path)?;
            field(&mut hasher, target.as_os_str().as_bytes());
            if let Some(output) = output {
                symlink(target, output)?;
            }
        } else {
            return Err(LitError::repository(format!(
                "Unsupported migration entry: {}",
                name.display()
            )));
        }
    }
    for (path, mode) in dirs.into_iter().rev() {
        let directory = File::open(&path)?;
        fs::set_permissions(&path, Permissions::from_mode(mode))?;
        directory.sync_all()?;
    }
    Ok(hex::encode(hasher.finalize()))
}
