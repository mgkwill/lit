//! Durable local accounting shared by Lit processes that open the same key.
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub(super) const MAX_ENCRYPTIONS_PER_KEY: u64 = 1u64 << 32;
const RESERVATION: u64 = 32;
const MAX_FAILURES: u32 = 5;
const LOCKOUT_MS: u64 = 300_000;

#[derive(Clone)]
pub(super) struct KeyState {
    directory: PathBuf,
    id: String,
}

impl KeyState {
    pub(super) fn new(id: &[u8; 32]) -> Result<Self, String> {
        let home =
            dirs::home_dir().ok_or("Cannot locate home directory for encryption key state")?;
        Ok(Self {
            directory: home.join(".lit/key-state"),
            id: hex::encode(id),
        })
    }

    fn path(&self, suffix: &str) -> PathBuf {
        self.directory.join(format!("{}.{suffix}", self.id))
    }

    fn guard(&self, suffix: &str) -> Result<File, String> {
        let mut builder = fs::DirBuilder::new();
        builder.recursive(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        builder.create(&self.directory).map_err(state_error)?;
        if !self
            .directory
            .symlink_metadata()
            .map_err(state_error)?
            .is_dir()
        {
            return Err("Encryption key state must be a real directory".into());
        }
        let path = self.path(suffix);
        if path.symlink_metadata().is_ok_and(|entry| !entry.is_file()) {
            return Err("Encryption key state lock must be a regular file".into());
        }
        let guard = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)
            .map_err(state_error)?;
        guard.lock().map_err(state_error)?;
        Ok(guard)
    }

    /// Initialize only when issuing a new key, before publishing its verifier.
    pub(super) fn issue(&self) -> Result<(), String> {
        let _guard = self.guard("usage.lock")?;
        let path = self.path("usage");
        match read::<Usage>(&path)? {
            Some(usage) => usage.validate(),
            None => persist(
                &path,
                &Usage {
                    version: 1,
                    reserved: 0,
                },
            ),
        }
    }

    pub(super) fn reserve(&self) -> Result<u64, String> {
        let _guard = self.guard("usage.lock")?;
        let path = self.path("usage");
        let mut usage = match read::<Usage>(&path)? {
            Some(usage) => usage,
            None => {
                return Err(
                    "Encryption usage ledger is missing. Rotate the key before writing.".into(),
                )
            }
        };
        usage.validate()?;
        let reserved = RESERVATION.min(MAX_ENCRYPTIONS_PER_KEY - usage.reserved);
        if reserved == 0 {
            return Err("Encryption limit reached. Rotate the key before writing.".into());
        }
        usage.reserved += reserved;
        // Never refund reservations, even if this process exits before using them.
        persist(&path, &usage)?;
        Ok(reserved)
    }

    pub(super) fn authenticate(&self) -> Result<Attempt, String> {
        let guard = self.guard("attempts.lock")?;
        let path = self.path("attempts");
        let mut policy = read::<Attempts>(&path)?.unwrap_or_default();
        let now = now_ms()?;
        if policy.pending {
            // The previous verifier exited before resolving its attempt.
            policy.finish_failure(now)?;
            persist(&path, &policy)?;
        }
        policy.admit(now)?;
        persist(&path, &policy)?;
        Ok(Attempt {
            _guard: guard,
            path,
            policy,
        })
    }

    pub(super) fn clear_attempts(&self) -> Result<(), String> {
        let _guard = self.guard("attempts.lock")?;
        persist(&self.path("attempts"), &Attempts::default())
    }
}

#[derive(Serialize, Deserialize)]
struct Usage {
    version: u32,
    reserved: u64,
}

impl Usage {
    fn validate(&self) -> Result<(), String> {
        if self.version != 1 || self.reserved > MAX_ENCRYPTIONS_PER_KEY {
            return Err("Invalid encryption usage ledger. Rotate the key before writing.".into());
        }
        Ok(())
    }
}

#[derive(Default, Serialize, Deserialize)]
struct Attempts {
    failures: u32,
    retry_at: u64,
    pending: bool,
}

impl Attempts {
    fn admit(&mut self, now: u64) -> Result<(), String> {
        if self.failures > MAX_FAILURES {
            return Err("Invalid passphrase attempt state".into());
        }
        if now < self.retry_at {
            return Err(format!(
                "Passphrase rate limit: retry in {} seconds.",
                (self.retry_at - now).div_ceil(1000),
            ));
        }
        if self.failures == MAX_FAILURES {
            self.failures = 0;
        }
        self.failures += 1;
        self.pending = true;
        Ok(())
    }

    fn finish_failure(&mut self, now: u64) -> Result<(), String> {
        if !(1..=MAX_FAILURES).contains(&self.failures) {
            return Err("Invalid passphrase attempt state".into());
        }
        let wait_ms = if self.failures == MAX_FAILURES {
            LOCKOUT_MS
        } else {
            1000 << self.failures
        };
        self.retry_at = now.saturating_add(wait_ms);
        self.pending = false;
        Ok(())
    }
}

pub(super) struct Attempt {
    _guard: File,
    path: PathBuf,
    policy: Attempts,
}

impl Attempt {
    pub(super) fn finish(mut self, authenticated: bool) -> Result<(), String> {
        if authenticated {
            self.policy = Attempts::default();
        } else {
            self.policy.finish_failure(now_ms()?)?;
        }
        persist(&self.path, &self.policy)
    }
}

fn now_ms() -> Result<u64, String> {
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| format!("Cannot obtain time for passphrase rate limiting: {e}"))?;
    u64::try_from(elapsed.as_millis()).map_err(|e| e.to_string())
}

fn read<T: DeserializeOwned>(path: &Path) -> Result<Option<T>, String> {
    match path.symlink_metadata() {
        Ok(entry) if !entry.is_file() => return Err("Expected a regular key state file".into()),
        Ok(_) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(state_error(error)),
    }
    let bytes = fs::read(path).map_err(state_error)?;
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(state_error)
}

fn persist(path: &Path, data: &impl Serialize) -> Result<(), String> {
    let directory = path.parent().ok_or("Key state path has no parent")?;
    let mut temporary = tempfile::NamedTempFile::new_in(directory).map_err(state_error)?;
    serde_json::to_writer(&mut temporary, data).map_err(state_error)?;
    temporary.flush().map_err(state_error)?;
    temporary.as_file().sync_all().map_err(state_error)?;
    temporary.persist(path).map_err(|e| state_error(e.error))?;
    sync_directory(directory).map_err(state_error)
}

pub(super) fn sync_directory(path: &Path) -> io::Result<()> {
    #[cfg(unix)]
    File::open(path)?.sync_all()?;
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

fn state_error(error: impl std::fmt::Display) -> String {
    format!("Encryption key state: {error}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_and_lockout_use_explicit_deadlines() {
        let mut state = Attempts::default();
        let mut now = 10;
        for delay in [2_000, 4_000, 8_000, 16_000, LOCKOUT_MS] {
            state.admit(now).unwrap();
            state.finish_failure(now + 100).unwrap();
            assert_eq!(state.retry_at, now + 100 + delay);
            assert!(state
                .admit(state.retry_at - 1)
                .unwrap_err()
                .contains("retry in 1 seconds"));
            now = state.retry_at;
        }
        state.admit(now).unwrap();
        assert_eq!(state.failures, 1);
    }
}
