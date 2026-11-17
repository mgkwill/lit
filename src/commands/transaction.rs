use crate::core::{find_repo_root, read_head, Blob, Object, ObjectHash};
use crate::errors::LitError;
use crate::response::TransactionResponse;
use crate::storage::{Index, ObjectStore};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Write};
use std::path::{Component, Path, PathBuf};

#[derive(Serialize, Deserialize)]
struct TransactionState {
    tx_id: String,
    #[serde(default)]
    rolling_back: bool,
    // A legacy journal has no checkpoint and cannot safely be rolled back.
    checkpoint: Option<Checkpoint>,
}

#[derive(Deserialize)]
struct LegacyJournal {
    tx_id: String,
    #[serde(rename = "started_at")]
    _started_at: i64,
    #[serde(rename = "wal")]
    _wal: Vec<LegacyEntry>,
}

#[derive(Deserialize)]
struct LegacyEntry {
    #[serde(rename = "op")]
    _op: String,
    #[serde(rename = "path")]
    _path: String,
    #[serde(rename = "original")]
    _original: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct Checkpoint {
    #[serde(deserialize_with = "nullable_bytes")]
    head: Option<Vec<u8>>,
    #[serde(deserialize_with = "nullable_bytes")]
    index: Option<Vec<u8>>,
    branch: String,
    #[serde(deserialize_with = "nullable_bytes")]
    branch_ref: Option<Vec<u8>>,
    tracked: BTreeMap<String, Option<WorkingFile>>,
}

// A checkpoint must explicitly record when a file was absent.
fn nullable_bytes<'de, D>(deserializer: D) -> Result<Option<Vec<u8>>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    Option::deserialize(deserializer)
}

#[derive(Serialize, Deserialize)]
struct WorkingFile {
    blob: ObjectHash,
    #[cfg(unix)]
    mode: u32,
    #[cfg(not(unix))]
    readonly: bool,
}

// A stable inode lets separate CLI and server processes serialize the
// supported writes. Closing the descriptor also releases it after a crash.
pub(crate) fn lock_writes(repo_root: &Path) -> Result<File, LitError> {
    let file = lock_repository(repo_root)?;
    crate::crypto::encryption::require_rotation_complete(repo_root)?;
    if read_phase(&repo_root.join(".lit/transaction.json"))? {
        return Err(recovery_required());
    }
    Ok(file)
}

pub(super) fn lock_repository(repo_root: &Path) -> Result<File, LitError> {
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(repo_root.join(".lit/transaction.guard"))
        .map_err(|e| LitError::io(format!("Open transaction guard: {e}")))?;
    file.lock()
        .map_err(|e| LitError::io(format!("Lock repository writes: {e}")))?;
    Ok(file)
}

// Skip the checkpoint payload when a normal writer only needs the phase.
fn read_phase(path: &Path) -> Result<bool, LitError> {
    #[derive(Deserialize)]
    struct Phase {
        #[serde(default)]
        rolling_back: bool,
    }

    read_journal_bytes(path)?
        .map(|bytes| {
            serde_json::from_slice::<Phase>(&bytes)
                .map(|phase| phase.rolling_back)
                .map_err(invalid_journal)
        })
        .transpose()
        .map(|phase| phase.unwrap_or(false))
}

fn recovery_required() -> LitError {
    "Rollback is in progress. Run 'lit tx rollback' to resume it before writing.".into()
}

pub(super) fn require_inactive(root: &Path, operation: &str) -> Result<(), LitError> {
    if root.join(".lit/transaction.json").try_exists().map_err(io_error)? {
        return Err(format!("Finish the active transaction before {operation}").into());
    }
    Ok(())
}

pub fn execute_begin() -> Result<TransactionResponse, LitError> {
    let repo_root = find_repo_root()?;
    let _guard = lock_repository(&repo_root)?;
    begin_locked(&repo_root, false)
}

/// Own the repository write lock from checkpoint creation through commit or
/// rollback. A killed process leaves a rollback-required journal behind.
pub(super) struct BatchTransaction<'a> {
    root: &'a Path,
    _guard: File,
}

impl<'a> BatchTransaction<'a> {
    pub(super) fn begin(root: &'a Path) -> Result<Self, LitError> {
        let guard = lock_repository(root)?;
        begin_locked(root, true)?;
        Ok(Self { root, _guard: guard })
    }

    pub(super) fn finish(self, rollback: bool) -> Result<(), LitError> {
        if rollback {
            finish_locked(self.root, true, || {})?;
        } else {
            fs::remove_file(self.root.join(".lit/transaction.json")).map_err(io_error)?;
        }
        Ok(())
    }
}

fn begin_locked(repo_root: &Path, batch: bool) -> Result<TransactionResponse, LitError> {
    crate::crypto::encryption::require_rotation_complete(repo_root)?;
    super::merge::require_inactive(repo_root)?;
    let lit = repo_root.join(".lit");
    let journal = lit.join("transaction.json");
    if journal.try_exists().map_err(io_error)? {
        return Err("Another transaction is in progress. Finish it with 'lit tx commit' or 'lit tx rollback'.".into());
    }
    let branch = if batch {
        crate::core::get_current_branch(repo_root)
            .map_err(|_| "Batch rollback requires a checked-out branch")?
    } else {
        crate::core::get_current_branch(repo_root).unwrap_or_else(|_| "main".to_owned())
    };
    let ref_path = checked_path(&lit.join("refs/heads"), &branch)?;
    let store = ObjectStore::new(repo_root);
    // Batch writes are confined to the index and current branch ref. They
    // never own working files, including files another process edits directly.
    let tracked = if batch {
        BTreeMap::new()
    } else {
        snapshot_working_files(repo_root, &store)?
    };
    let checkpoint = Checkpoint {
        head: read_optional(&lit.join("HEAD"))?,
        index: read_optional(&lit.join("index"))?,
        branch,
        branch_ref: read_optional(&ref_path)?,
        tracked,
    };
    if batch {
        validate_checkpoint(repo_root, &store, &checkpoint)?;
    }
    let state = TransactionState {
        tx_id: uuid::Uuid::new_v4().to_string(),
        rolling_back: batch,
        checkpoint: Some(checkpoint),
    };
    let bytes = serde_json::to_vec(&state).map_err(|e| e.to_string())?;
    write_file(&journal, &bytes, None)?;
    Ok(response(state, "begin", "Transaction started"))
}

fn snapshot_working_files(
    repo_root: &Path,
    store: &ObjectStore,
) -> Result<BTreeMap<String, Option<WorkingFile>>, LitError> {
    let index = Index::load(repo_root)?;
    let head = read_head(repo_root).ok();
    let mut tracked = BTreeMap::new();
    for path in index
        .entries
        .into_keys()
        .chain(super::status::head_entries(repo_root, head.as_deref())?.into_keys())
    {
        if tracked.contains_key(&path) {
            continue;
        }
        let target = working_path(repo_root, &path)?;
        let file = match fs::symlink_metadata(&target) {
            Ok(meta) if meta.is_file() => {
                let bytes = fs::read(&target).map_err(io_error)?;
                let object = Object::Blob(Blob::new(bytes));
                let blob = object.hash();
                if !store.exists(&blob) {
                    store.write(&object)?;
                }
                #[cfg(unix)]
                use std::os::unix::fs::PermissionsExt;
                Some(WorkingFile {
                    blob,
                    #[cfg(unix)]
                    mode: meta.permissions().mode() & 0o777,
                    #[cfg(not(unix))]
                    readonly: meta.permissions().readonly(),
                })
            }
            Ok(_) => {
                return Err(format!("Cannot checkpoint non-regular tracked path: {path}").into())
            }
            Err(e) if e.kind() == ErrorKind::NotFound => None,
            Err(e) => return Err(io_error(e)),
        };
        tracked.insert(path, file);
    }
    Ok(tracked)
}

pub fn execute_commit_tx() -> Result<TransactionResponse, LitError> {
    finish(&find_repo_root()?, false, || {})
}

pub fn execute_rollback() -> Result<TransactionResponse, LitError> {
    finish(&find_repo_root()?, true, || {})
}

fn finish(
    repo_root: &Path,
    rollback: bool,
    after_ref_restore: impl FnOnce(),
) -> Result<TransactionResponse, LitError> {
    let _guard = lock_repository(repo_root)?;
    finish_locked(repo_root, rollback, after_ref_restore)
}

fn finish_locked(
    repo_root: &Path,
    rollback: bool,
    after_ref_restore: impl FnOnce(),
) -> Result<TransactionResponse, LitError> {
    crate::crypto::encryption::require_rotation_complete(repo_root)?;
    let lit = repo_root.join(".lit");
    let journal = lit.join("transaction.json");
    let mut state = read_journal(&journal)?.ok_or("No transaction in progress")?;
    if !rollback && state.rolling_back {
        return Err(recovery_required());
    }
    if rollback {
        let checkpoint = state.checkpoint.as_ref().ok_or(
            "This legacy transaction has no rollback checkpoint. Use 'lit tx commit' to close it.",
        )?;

        if read_optional(&lit.join("HEAD"))? != checkpoint.head {
            return Err("HEAD has changed since transaction begin. Restore the original checkout before rolling back.".into());
        }
        let store = ObjectStore::new(repo_root);
        validate_checkpoint(repo_root, &store, checkpoint)?;
        if !state.rolling_back {
            state.rolling_back = true;
            let bytes = serde_json::to_vec(&state).map_err(|e| e.to_string())?;
            write_file(&journal, &bytes, None)?;
        }
        for (path, saved) in &checkpoint.tracked {
            let path = working_path(repo_root, path)?;
            if let Some(saved) = saved {
                let Object::Blob(blob) = store.read(&saved.blob)? else {
                    return Err("Transaction checkpoint does not contain a blob".into());
                };
                write_file(&path, &blob.content, Some(saved))?;
            } else {
                restore(&path, None)?;
            }
        }
        let ref_path = checked_path(&lit.join("refs/heads"), &checkpoint.branch)?;
        restore(&ref_path, checkpoint.branch_ref.as_deref())?;
        restore(&lit.join("HEAD"), checkpoint.head.as_deref())?;
        after_ref_restore();
        restore(&lit.join("index"), checkpoint.index.as_deref())?;
    }
    // Keep the journal until all writes succeed so a partial rollback can be retried.
    if state.checkpoint.is_none() {
        restore(&lit.join("transaction.lock"), None)?;
    }
    fs::remove_file(journal).map_err(io_error)?;
    Ok(if rollback {
        response(state, "rollback", "Transaction rolled back")
    } else {
        response(state, "commit", "Transaction committed")
    })
}

pub(super) fn verify_state(root: &Path, store: &ObjectStore) -> Result<Option<String>, LitError> {
    let metadata = root.join(".lit");
    let state = read_journal(&metadata.join("transaction.json"))?;
    let legacy_lock = read_optional(&metadata.join("transaction.lock"))?;
    match state {
        Some(state) => match state.checkpoint {
            Some(checkpoint) => {
                if legacy_lock.is_some() {
                    return Err(invalid_journal(
                        "Unexpected legacy transaction.lock alongside checkpoint",
                    ));
                }
                validate_checkpoint(root, store, &checkpoint)?;
                Ok(Some(
                    if state.rolling_back {
                        "Rollback was interrupted. Run 'lit tx rollback' to resume it."
                    } else {
                        "Active transaction checkpoint is valid"
                    }
                    .into(),
                ))
            }
            None => {
                if legacy_lock.as_deref() != Some(state.tx_id.as_bytes()) {
                    return Err(invalid_journal("Legacy transaction.lock disagrees with the journal. Restore matching metadata or close the transaction with 'lit tx commit'"));
                }
                Ok(Some(
                    "Legacy transaction is active. Use 'lit tx commit' to close it".into(),
                ))
            }
        },
        None if legacy_lock.is_some() => Err(invalid_journal(
            "Orphaned transaction.lock. Restore the missing legacy journal from backup",
        )),
        None => Ok(None),
    }
}

fn read_journal_bytes(path: &Path) -> Result<Option<Vec<u8>>, LitError> {
    match path.symlink_metadata() {
        Ok(metadata) if metadata.is_file() => {}
        Ok(_) => return Err(invalid_journal("expected a regular transaction.json file")),
        Err(e) if e.kind() == ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(io_error(e)),
    }
    fs::read(path).map(Some).map_err(io_error)
}

fn read_journal(path: &Path) -> Result<Option<TransactionState>, LitError> {
    let Some(bytes) = read_journal_bytes(path)? else {
        return Ok(None);
    };
    let json: serde_json::Value =
        serde_json::from_slice(&bytes).map_err(|e| invalid_journal(e.to_string()))?;
    let state = if json.get("checkpoint").is_some() {
        let state: TransactionState =
            serde_json::from_value(json).map_err(|e| invalid_journal(e.to_string()))?;
        if state.checkpoint.is_none() {
            return Err(invalid_journal("checkpoint is null"));
        }
        state
    } else {
        let legacy: LegacyJournal = serde_json::from_value(json).map_err(|e| {
            invalid_journal(format!(
                "invalid legacy metadata or missing checkpoint: {e}"
            ))
        })?;
        TransactionState {
            tx_id: legacy.tx_id,
            rolling_back: false,
            checkpoint: None,
        }
    };
    uuid::Uuid::parse_str(&state.tx_id).map_err(|_| invalid_journal("invalid transaction ID"))?;
    Ok(Some(state))
}

fn validate_checkpoint(
    root: &Path,
    store: &ObjectStore,
    checkpoint: &Checkpoint,
) -> Result<(), LitError> {
    checked_path(&root.join(".lit/refs/heads"), &checkpoint.branch)
        .map_err(|e| invalid_journal(format!("invalid checkpoint branch: {e}")))?;
    if let Some(bytes) = &checkpoint.head {
        let head = crate::core::refs::decode_ref_bytes(root, bytes)
            .map_err(|_| invalid_journal("cannot decode checkpoint HEAD"))?;
        let head = head.trim();
        if let Some(branch) = head.strip_prefix("ref: refs/heads/") {
            if branch != checkpoint.branch {
                return Err(invalid_journal("checkpoint HEAD and branch disagree"));
            }
        } else {
            validate_commit(store, head, "HEAD")?;
        }
    }
    if let Some(bytes) = &checkpoint.branch_ref {
        let hash = crate::core::refs::decode_ref_bytes(root, bytes)
            .map_err(|_| invalid_journal("cannot decode checkpoint branch ref"))?;
        validate_commit(store, hash.trim(), "branch ref")?;
    }
    if let Some(bytes) = &checkpoint.index {
        let index = Index::decode(root, bytes, None)
            .map_err(|e| invalid_journal(format!("invalid checkpoint index: {e}")))?;
        super::verify::validate_index_entries(&index, store)
            .map_err(|e| invalid_journal(format!("invalid checkpoint index: {e}")))?;
    }
    for (path, saved) in &checkpoint.tracked {
        working_path(root, path)
            .map_err(|e| invalid_journal(format!("invalid checkpoint path: {e}")))?;
        if let Some(saved) = saved {
            #[cfg(unix)]
            if saved.mode & !0o777 != 0 {
                return Err(invalid_journal(format!(
                    "invalid checkpoint mode for {path}"
                )));
            }
            let valid_blob = matches!(store.read_verified(&saved.blob), Ok(Object::Blob(_)));
            if !valid_blob {
                return Err(invalid_journal(format!(
                    "invalid checkpoint blob for {path}"
                )));
            }
        }
    }
    Ok(())
}

fn validate_commit(store: &ObjectStore, hash: &str, field: &str) -> Result<(), LitError> {
    let expected = ObjectHash::from_hex(hash.to_owned());
    if matches!(store.read_verified(&expected), Ok(Object::Commit(_))) {
        return Ok(());
    }
    Err(invalid_journal(format!(
        "checkpoint {field} does not name a valid commit"
    )))
}

fn invalid_journal(details: impl std::fmt::Display) -> LitError {
    LitError::repository(format!("Invalid .lit/transaction.json: {details}"))
}

fn response(state: TransactionState, action: &str, message: &str) -> TransactionResponse {
    TransactionResponse {
        action: action.into(),
        tx_id: Some(state.tx_id),
        message: message.into(),
    }
}

fn read_optional(path: &Path) -> Result<Option<Vec<u8>>, LitError> {
    match fs::read(path) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
        Err(e) => Err(io_error(e)),
    }
}

fn working_path(repo_root: &Path, path: &str) -> Result<PathBuf, LitError> {
    if Path::new(path).starts_with(".lit") {
        return Err(format!("Invalid tracked path in transaction: {path}").into());
    }
    checked_path(repo_root, path)
}

fn checked_path(root: &Path, path: &str) -> Result<PathBuf, LitError> {
    if path.is_empty() {
        return Err("Empty transaction path".into());
    }
    let mut target = root.to_path_buf();
    for component in Path::new(path).components() {
        let Component::Normal(name) = component else {
            return Err(format!("Invalid transaction path: {path}").into());
        };
        target.push(name);
        match fs::symlink_metadata(&target) {
            Ok(meta) if meta.is_symlink() => {
                return Err(format!("Symlink obstructs transaction path: {path}").into())
            }
            Ok(_) => {}
            Err(e) if e.kind() == ErrorKind::NotFound => {}
            Err(e) => return Err(io_error(e)),
        }
    }
    Ok(target)
}

fn restore(path: &Path, bytes: Option<&[u8]>) -> Result<(), LitError> {
    if let Some(bytes) = bytes {
        return write_file(path, bytes, None);
    }
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(()),
        Err(e) => Err(io_error(e)),
    }
}

fn write_file(path: &Path, bytes: &[u8], saved: Option<&WorkingFile>) -> Result<(), LitError> {
    let parent = path.parent().ok_or("Transaction path has no parent")?;
    fs::create_dir_all(parent).map_err(io_error)?;
    let mut file = tempfile::NamedTempFile::new_in(parent).map_err(io_error)?;
    file.write_all(bytes).map_err(io_error)?;
    if let Some(saved) = saved {
        #[cfg(unix)]
        let permissions = {
            use std::os::unix::fs::PermissionsExt;
            fs::Permissions::from_mode(saved.mode)
        };
        #[cfg(not(unix))]
        let permissions = {
            let mut permissions = file.as_file().metadata().map_err(io_error)?.permissions();
            permissions.set_readonly(saved.readonly);
            permissions
        };
        file.as_file()
            .set_permissions(permissions)
            .map_err(io_error)?;
    }
    file.as_file().sync_all().map_err(io_error)?;
    file.persist(path).map_err(|e| io_error(e.error))?;
    Ok(())
}

fn io_error(error: std::io::Error) -> LitError {
    LitError::io(format!("Transaction I/O: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::{Child, Command, ExitStatus, Stdio};
    use std::thread;
    use std::time::{Duration, Instant};

    const WORKER: &str = "LIT_TEST_TX_WORKER";

    struct Process(Child);

    impl Process {
        fn wait(&mut self) -> ExitStatus {
            let deadline = Instant::now() + Duration::from_secs(20);
            loop {
                if let Some(status) = self.0.try_wait().unwrap() {
                    return status;
                }
                assert!(Instant::now() < deadline, "transaction worker timed out");
                thread::sleep(Duration::from_millis(10));
            }
        }
    }

    impl Drop for Process {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    fn spawn(root: &Path, action: &str) -> Process {
        Process(
            Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "commands::transaction::tests::crash_recovery_and_competing_writers",
                    "--nocapture",
                ])
                .current_dir(root)
                .env(WORKER, action)
                .stdout(Stdio::null())
                .spawn()
                .unwrap(),
        )
    }

    fn wait_for(path: &Path, process: &mut Process) {
        let deadline = Instant::now() + Duration::from_secs(20);
        while !path.is_file() {
            assert!(
                process.0.try_wait().unwrap().is_none(),
                "worker exited before its checkpoint"
            );
            assert!(
                Instant::now() < deadline,
                "worker did not reach its checkpoint"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn park_at(path: &str) {
        fs::write(path, "").unwrap();
        loop {
            thread::park();
        }
    }

    #[test]
    fn crash_recovery_and_competing_writers() {
        if let Ok(action) = std::env::var(WORKER) {
            match action.as_str() {
                "begin" => {
                    super::super::add::execute(vec!["tracked".into()]).unwrap();
                    super::super::commit::execute("original".into(), None).unwrap();
                    execute_begin().unwrap();
                }
                "advance" => {
                    super::super::add::execute(vec!["tracked".into()]).unwrap();
                    super::super::commit::execute("inside transaction".into(), None).unwrap();
                    let error = super::super::gc::execute().unwrap_err();
                    assert!(error
                        .internal_message()
                        .contains("Finish the active transaction"));
                    park_at(".lit/test-advance-ready");
                }
                "interrupted-rollback" => {
                    finish(&find_repo_root().unwrap(), true, || {
                        park_at(".lit/test-rollback-ready")
                    })
                    .unwrap();
                }
                "competing-add" => {
                    fs::write(".lit/test-writer-ready", "").unwrap();
                    let error = super::super::add::execute(vec!["independent".into()]).unwrap_err();
                    assert!(error.internal_message().contains("Rollback is in progress"));
                    assert!(execute_commit_tx()
                        .unwrap_err()
                        .internal_message()
                        .contains("Rollback is in progress"));
                }
                "recover" => {
                    execute_rollback().unwrap();
                }
                "write" => {
                    super::super::add::execute(vec!["independent".into()]).unwrap();
                }
                "try-begin" => {
                    if execute_begin().is_err() {
                        std::process::exit(2);
                    }
                }
                _ => panic!("unknown transaction test worker"),
            }
            return;
        }

        let temporary = tempfile::tempdir().unwrap();
        let root = temporary.path();
        super::super::init::execute(false, Some(root.to_str().unwrap().to_owned())).unwrap();
        let metadata = root.join(".lit");
        let head = metadata.join("HEAD");
        let branch = metadata.join("refs/heads/main");
        let index = metadata.join("index");
        let journal = metadata.join("transaction.json");

        fs::write(root.join("tracked"), "original bytes").unwrap();
        assert!(spawn(root, "begin").wait().success());
        let old_head = fs::read(&head).unwrap();
        let old_branch = fs::read(&branch).unwrap();
        let old_index = fs::read(&index).unwrap();
        fs::write(root.join("tracked"), "transaction bytes").unwrap();
        let mut advance = spawn(root, "advance");
        wait_for(&metadata.join("test-advance-ready"), &mut advance);
        advance.0.kill().unwrap();
        assert!(!advance.wait().success());
        assert!(journal.is_file());
        let transaction_index = fs::read(&index).unwrap();
        assert_ne!(old_index, transaction_index);
        assert_ne!(fs::read(&branch).unwrap(), old_branch);
        fs::write(root.join("independent"), "independent bytes").unwrap();

        let mut rollback = spawn(root, "interrupted-rollback");
        wait_for(&metadata.join("test-rollback-ready"), &mut rollback);
        assert_eq!(fs::read(&head).unwrap(), old_head);
        assert_eq!(fs::read(&branch).unwrap(), old_branch);
        assert_eq!(fs::read(&index).unwrap(), transaction_index);
        assert_eq!(
            fs::read_to_string(root.join("tracked")).unwrap(),
            "original bytes"
        );

        let guard = OpenOptions::new()
            .read(true)
            .write(true)
            .open(metadata.join("transaction.guard"))
            .unwrap();
        assert!(matches!(
            guard.try_lock(),
            Err(std::fs::TryLockError::WouldBlock)
        ));
        let mut contender = spawn(root, "competing-add");
        wait_for(&metadata.join("test-writer-ready"), &mut contender);
        assert!(contender.0.try_wait().unwrap().is_none());

        // Kill the process with a real OS lock and only part of the state restored.
        rollback.0.kill().unwrap();
        assert!(!rollback.wait().success());
        assert!(contender.wait().success());
        assert_eq!(fs::read(&index).unwrap(), transaction_index);
        assert!(journal.is_file());
        let report = verify_state(root, &ObjectStore::new(root))
            .unwrap()
            .unwrap();
        assert!(report.contains("Rollback was interrupted"), "{report}");

        // Recover first, then verify the independent writer can stage its own file.
        assert!(spawn(root, "recover").wait().success());
        assert!(!journal.exists());
        assert_eq!(fs::read(&head).unwrap(), old_head);
        assert_eq!(fs::read(&branch).unwrap(), old_branch);
        assert_eq!(
            fs::read_to_string(root.join("tracked")).unwrap(),
            "original bytes"
        );
        assert_eq!(
            fs::read_to_string(root.join("independent")).unwrap(),
            "independent bytes"
        );
        assert_eq!(fs::read(&index).unwrap(), old_index);
        assert!(spawn(root, "write").wait().success());
        let before = Index::decode(root, &old_index, None).unwrap();
        let mut index_after = Index::load(root).unwrap();
        assert!(index_after.remove("independent").is_some());
        assert_eq!(index_after.entries, before.entries);
        assert!(guard.try_lock().is_ok(), "guard leaked after recovery");
        guard.unlock().unwrap();

        let mut first = spawn(root, "try-begin");
        let mut second = spawn(root, "try-begin");
        let (first, second) = (first.wait(), second.wait());
        assert_ne!(first.success(), second.success(), "one begin must win");
        assert_eq!(
            first.code().unwrap_or_default() + second.code().unwrap_or_default(),
            2
        );
        assert!(journal.is_file());
    }
}
