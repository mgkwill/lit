#![allow(unused_assignments)]
use super::key_state::{sync_directory, KeyState};
/// Encryption Module - FIPS 140-3 Compliant AES-256-GCM
/// Provides secure at-rest encryption for repository data
///
/// Standards Compliance:
/// - FIPS 140-3 (ISO/IEC 19790:2012) - Cryptographic Module Validation
/// - AES-256-GCM (FIPS 197, NIST SP 800-38D) - Authenticated Encryption
/// - PBKDF2-HMAC-SHA512 (NIST SP 800-132) - Password-Based Key Derivation
/// - DRBG (NIST SP 800-90A Rev. 1) - Deterministic Random Bit Generation
/// - Key Management (NIST SP 800-57 Part 1 Rev. 5) - Cryptographic Key Management
use aes_gcm::{
    aead::{Aead, KeyInit, OsRng},
    Aes256Gcm, Nonce,
};
use lazy_static::lazy_static;
use pbkdf2::pbkdf2_hmac;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256, Sha512};
use std::collections::HashMap;
use std::fs;
use std::io::{ErrorKind, Write};
use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, SystemTime};
use zeroize::{ZeroizeOnDrop, Zeroizing};

/// AES-256 key size in bytes
const KEY_SIZE: usize = 32;

/// Default passphrase cache timeout (5 minutes)
const DEFAULT_CACHE_TIMEOUT: Duration = Duration::from_secs(300);

/// Cached passphrase entry with expiration
/// SECURITY: Uses Zeroizing to ensure passphrase is cleared from memory on drop
struct CachedPassphrase {
    passphrase: Zeroizing<String>,
    expires_at: SystemTime,
}

lazy_static! {
    /// Global passphrase cache with thread-safe access
    static ref PASSPHRASE_CACHE: Mutex<HashMap<String, CachedPassphrase>> = Mutex::new(HashMap::new());

}

/// AES-GCM nonce size in bytes (96 bits recommended)
const NONCE_SIZE: usize = 12;

/// PBKDF2 iteration count for FIPS 140-3 compliance
/// NIST SP 800-132 (2010) recommends minimum 10,000
/// NIST SP 800-63B (2024) recommends minimum 210,000
/// We use 600,000 for enhanced security against modern GPU attacks
/// This provides ~2.85x the current NIST recommendation
const PBKDF2_ITERATIONS: u32 = 600_000;

/// Salt size for PBKDF2 (16 bytes = 128 bits)
/// Meets NIST SP 800-132 requirement for >= 128 bits
const SALT_SIZE: usize = 16;

/// Encrypted data header version
pub(crate) const ENCRYPTION_VERSION: u8 = 1;
const MANAGED_KEY_VERSION: u8 = 2;
const VERIFIER_SIZE: usize = 32;

/// Encryption configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EncryptionConfig {
    /// Enable encryption for repository data
    pub enabled: bool,
    /// Path to encrypted key file
    pub key_file: String,
    /// FIPS 140-3 mode (strict algorithm compliance)
    pub fips_mode: bool,
    /// Passphrase cache timeout in seconds (0 to disable caching)
    #[serde(default = "default_cache_timeout")]
    pub cache_timeout_secs: u64,
}

fn default_cache_timeout() -> u64 {
    300 // 5 minutes
}

impl Default for EncryptionConfig {
    fn default() -> Self {
        EncryptionConfig {
            enabled: false,
            key_file: "~/.lit/encryption.key".to_string(),
            fips_mode: true,
            cache_timeout_secs: default_cache_timeout(),
        }
    }
}

impl EncryptionConfig {
    /// Load configuration from repository
    pub fn load(repo_path: &Path) -> Result<Self, String> {
        let config_path = repo_path.join(".lit").join("encryption.toml");

        if !config_path.exists() {
            return Ok(Self::default());
        }

        let content = fs::read_to_string(&config_path)
            .map_err(|e| format!("Failed to read encryption config: {}", e))?;

        let mut config: Self = toml::from_str(&content)
            .map_err(|e| format!("Failed to parse encryption config: {e}"))?;
        let key = Path::new(&config.key_file);
        if !key.is_absolute() && !config.key_file.starts_with('~') {
            config.key_file = repo_path
                .join(key)
                .to_str()
                .ok_or("Repository key path is not UTF-8")?
                .to_owned();
        }
        Ok(config)
    }

    /// Save configuration to repository
    pub fn save(&self, repo_path: &Path) -> Result<(), String> {
        let config_path = repo_path.join(".lit").join("encryption.toml");

        let content = toml::to_string_pretty(self)
            .map_err(|e| format!("Failed to serialize encryption config: {}", e))?;

        fs::write(&config_path, content)
            .map_err(|e| format!("Failed to write encryption config: {}", e))
    }
}

enum KeyOrigin {
    Fresh,
    Managed,
    Legacy(Option<KeyState>),
}

/// Secure encryption key with automatic zeroization
#[derive(ZeroizeOnDrop)]
#[allow(unused_assignments)]
pub struct EncryptionKey {
    key_bytes: [u8; KEY_SIZE],
    /// Salt used to derive this key (needed for saving)
    #[zeroize(skip)]
    salt: [u8; SALT_SIZE],
    #[zeroize(skip)]
    origin: KeyOrigin,
}

impl EncryptionKey {
    /// Derive key from passphrase using PBKDF2-HMAC-SHA512
    pub fn from_passphrase(passphrase: &str, salt: &[u8]) -> Result<Self, String> {
        validate_passphrase_strength(passphrase)?;
        Self::derive(passphrase, salt)
    }

    fn derive(passphrase: &str, salt: &[u8]) -> Result<Self, String> {
        if salt.len() != SALT_SIZE {
            return Err(format!(
                "Invalid salt size: expected {}, got {}",
                SALT_SIZE,
                salt.len()
            ));
        }

        let mut key_bytes = [0u8; KEY_SIZE];
        pbkdf2_hmac::<Sha512>(
            passphrase.as_bytes(),
            salt,
            PBKDF2_ITERATIONS,
            &mut key_bytes,
        );

        let mut salt_array = [0u8; SALT_SIZE];
        salt_array.copy_from_slice(salt);

        Ok(EncryptionKey {
            key_bytes,
            salt: salt_array,
            origin: KeyOrigin::Fresh,
        })
    }

    /// Generate a random salt for key derivation
    pub fn generate_salt() -> [u8; SALT_SIZE] {
        use aes_gcm::aead::rand_core::RngCore;
        let mut salt = [0u8; SALT_SIZE];
        OsRng.fill_bytes(&mut salt);
        salt
    }

    /// Authenticate a passphrase against the stored key verifier.
    pub fn load(key_file: &Path, passphrase: &str) -> Result<Self, String> {
        let data =
            fs::read(key_file).map_err(|e| format!("Failed to read encryption key file: {e}"))?;
        let version = *data.get(SALT_SIZE).ok_or("Invalid key file format")?;
        let salt = &data[..SALT_SIZE];
        let verifier: Option<[u8; VERIFIER_SIZE]> = match (version, data.len()) {
            (ENCRYPTION_VERSION, size) if size == SALT_SIZE + 1 => None,
            (ENCRYPTION_VERSION | MANAGED_KEY_VERSION, size)
                if size == SALT_SIZE + 1 + VERIFIER_SIZE =>
            {
                Some(
                    data[SALT_SIZE + 1..]
                        .try_into()
                        .map_err(|_| "Invalid key verifier")?,
                )
            }
            _ => return Err("Unsupported encryption key file format".into()),
        };
        let identity = verifier.unwrap_or_else(|| {
            let mut hash = Sha256::new();
            hash.update(b"lit-legacy-salt-only-v1");
            hash.update(salt);
            hash.finalize().into()
        });
        let state = KeyState::new(&identity)?;
        let attempt = state.authenticate()?;
        // Strength rules apply when choosing a new passphrase. Every guess,
        // including a weak one, goes through the same verifier and backoff.
        let mut key = Self::derive(passphrase, salt)?;
        if let Some(verifier) = verifier {
            use subtle::ConstantTimeEq;
            let authenticated = bool::from(key.verifier().ct_eq(&verifier));
            attempt.finish(authenticated)?;
            if !authenticated {
                return Err("Invalid passphrase".into());
            }
            key.origin = if version == MANAGED_KEY_VERSION {
                KeyOrigin::Managed
            } else {
                KeyOrigin::Legacy(None)
            };
        } else {
            // Salt-only keys can only prove authentication by decrypting
            // ciphertext. Consume the attempt until AEAD verifies it.
            attempt.finish(false)?;
            key.origin = KeyOrigin::Legacy(Some(state));
        }
        Ok(key)
    }

    fn verifier(&self) -> [u8; VERIFIER_SIZE] {
        let mut hash = Sha256::new();
        hash.update(b"lit-passphrase-verification-v1");
        hash.update(self.key_bytes);
        hash.finalize().into()
    }

    /// Publish a verifier for a freshly issued key.
    pub fn save(&self, key_file_str: &str, _passphrase: &str) -> Result<(), String> {
        let expanded = shellexpand::tilde(key_file_str);
        self.write_verifier(Path::new(expanded.as_ref()), true)
            .map(|_| ())
    }

    fn write_verifier(&self, key_file: &Path, replace: bool) -> Result<bool, String> {
        // An old key has unknown lifetime usage. Rewriting the verifier
        // would falsely certify that the old key has a fresh budget.
        if !matches!(self.origin, KeyOrigin::Fresh) {
            return Err("Issue a fresh key with a new salt before saving a verifier.".into());
        }
        let verifier = self.verifier();
        KeyState::new(&verifier)?.issue()?;

        let parent = key_file
            .parent()
            .filter(|path| !path.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        fs::create_dir_all(parent).map_err(|e| format!("Create key directory: {e}"))?;
        let mut temp = tempfile::NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
        temp.write_all(&self.salt)
            .and_then(|_| temp.write_all(&[MANAGED_KEY_VERSION]))
            .and_then(|_| temp.write_all(&verifier))
            .map_err(|e| e.to_string())?;
        temp.as_file().sync_all().map_err(|e| e.to_string())?;
        if replace {
            temp.persist(key_file)
                .map_err(|e| format!("Publish key verifier: {}", e.error))?;
        } else if let Err(error) = temp.persist_noclobber(key_file) {
            if error.error.kind() == ErrorKind::AlreadyExists {
                return Ok(false);
            }
            return Err(format!("Publish key verifier: {}", error.error));
        }
        sync_directory(parent).map_err(|e| format!("Sync encryption key directory: {e}"))?;
        Ok(true)
    }

    /// Get raw key bytes (used internally)
    fn as_bytes(&self) -> &[u8; KEY_SIZE] {
        &self.key_bytes
    }
}

struct Budget {
    state: KeyState,
    remaining: Mutex<u64>,
}

impl Budget {
    fn consume(&self) -> Result<(), String> {
        let mut remaining = self
            .remaining
            .lock()
            .map_err(|_| "Encryption usage reservation lock poisoned")?;
        if *remaining == 0 {
            *remaining = self.state.reserve()?;
        }
        *remaining -= 1;
        Ok(())
    }
}

/// Encryption engine using AES-256-GCM.
pub struct EncryptionEngine {
    cipher: Aes256Gcm,
    budget: Option<Budget>,
    legacy_attempt: Mutex<Option<KeyState>>,
}

impl EncryptionEngine {
    /// Create a new encryption engine with the supplied key.
    pub fn new(key: &EncryptionKey) -> Result<Self, String> {
        let cipher = Aes256Gcm::new_from_slice(key.as_bytes())
            .map_err(|e| format!("Failed to create cipher: {e}"))?;
        let (budget, legacy_attempt) = match &key.origin {
            KeyOrigin::Fresh | KeyOrigin::Managed => {
                let state = KeyState::new(&key.verifier())?;
                if matches!(key.origin, KeyOrigin::Fresh) {
                    state.issue()?;
                }
                (
                    Some(Budget {
                        state,
                        remaining: Mutex::new(0),
                    }),
                    None,
                )
            }
            KeyOrigin::Legacy(attempt) => (None, attempt.clone()),
        };
        Ok(Self {
            cipher,
            budget,
            legacy_attempt: Mutex::new(legacy_attempt),
        })
    }

    /// Encrypt data with authenticated encryption (AES-256-GCM)
    ///
    /// Format: [version: 1 byte][nonce: 12 bytes][ciphertext + auth tag]
    /// Uses a fresh 96-bit OS-random nonce for each invocation.
    #[allow(deprecated)]
    pub fn encrypt(&self, plaintext: &[u8]) -> Result<Vec<u8>, String> {
        self.budget
            .as_ref()
            .ok_or("Legacy key has no lifetime usage ledger. Run 'lit rotate-key' before writing.")?
            .consume()?;

        use aes_gcm::aead::rand_core::RngCore;
        let mut nonce_bytes = [0u8; NONCE_SIZE];
        OsRng
            .try_fill_bytes(&mut nonce_bytes)
            .map_err(|e| format!("Could not generate encryption nonce: {e}"))?;
        let nonce = Nonce::from_slice(&nonce_bytes);

        // Encrypt with authenticated encryption
        let ciphertext = self
            .cipher
            .encrypt(nonce, plaintext)
            .map_err(|e| format!("Encryption failed: {}", e))?;

        // Build output: version + nonce + ciphertext
        let mut output = Vec::with_capacity(1 + NONCE_SIZE + ciphertext.len());
        output.push(ENCRYPTION_VERSION);
        output.extend_from_slice(&nonce_bytes);
        output.extend_from_slice(&ciphertext);

        Ok(output)
    }

    /// Decrypt data with authentication verification
    #[allow(deprecated)]
    pub fn decrypt(&self, encrypted: &[u8]) -> Result<Vec<u8>, String> {
        if encrypted.len() < 1 + NONCE_SIZE {
            return Err("Invalid encrypted data: too short".to_string());
        }

        // Extract version
        let version = encrypted[0];
        if version != ENCRYPTION_VERSION {
            return Err(format!("Unsupported encryption version: {}", version));
        }

        // Extract nonce
        let nonce_bytes = &encrypted[1..1 + NONCE_SIZE];
        let nonce = Nonce::from_slice(nonce_bytes);

        // Extract ciphertext
        let ciphertext = &encrypted[1 + NONCE_SIZE..];

        // Decrypt and verify authentication tag
        let plaintext = self
            .cipher
            .decrypt(nonce, ciphertext)
            .map_err(|e| format!("Decryption failed (possible tampering): {}", e))?;

        let mut legacy = self
            .legacy_attempt
            .lock()
            .map_err(|_| "Legacy authentication lock poisoned")?;
        if let Some(attempt) = legacy.as_ref() {
            attempt.clear_attempts()?;
            *legacy = None;
        }
        Ok(plaintext)
    }
}

/// Passphrase cache operations
impl CachedPassphrase {
    /// Check if cached passphrase is still valid
    fn is_valid(&self) -> bool {
        SystemTime::now() < self.expires_at
    }
}

/// Store passphrase in cache with timeout
/// SECURITY: Passphrase stored in Zeroizing wrapper for automatic memory clearing
pub fn cache_passphrase(repo_path: &str, passphrase: String, timeout: Option<Duration>) {
    let timeout = timeout.unwrap_or(DEFAULT_CACHE_TIMEOUT);
    let expires_at = SystemTime::now() + timeout;

    if let Ok(mut cache) = PASSPHRASE_CACHE.lock() {
        cache.insert(
            repo_path.to_string(),
            CachedPassphrase {
                passphrase: Zeroizing::new(passphrase),
                expires_at,
            },
        );
    }
}

/// Retrieve cached passphrase if valid
/// SECURITY: Returns clone of Zeroizing-wrapped passphrase
pub fn get_cached_passphrase(repo_path: &str) -> Option<Zeroizing<String>> {
    if let Ok(mut cache) = PASSPHRASE_CACHE.lock() {
        if let Some(entry) = cache.get(repo_path) {
            if entry.is_valid() {
                return Some(entry.passphrase.clone());
            } else {
                // Remove expired entry (passphrase auto-zeroized on drop)
                cache.remove(repo_path);
            }
        }
    }
    None
}

/// Clear all cached passphrases
pub fn clear_passphrase_cache() {
    if let Ok(mut cache) = PASSPHRASE_CACHE.lock() {
        cache.clear();
    }
}

/// Clear cached passphrase for specific repository
pub fn clear_cached_passphrase(repo_path: &str) {
    if let Ok(mut cache) = PASSPHRASE_CACHE.lock() {
        cache.remove(repo_path);
    }
}

/// Get passphrase from non-interactive sources
///
/// Priority: LIT_PASSPHRASE env var > LIT_PASSPHRASE_FILE env var > cache
/// Returns None if no non-interactive source is available.
/// SECURITY: Returns Zeroizing<String> to ensure passphrase is cleared from memory.
fn get_passphrase_non_interactive(
    repo_path: &str,
    config: &EncryptionConfig,
) -> Option<Zeroizing<String>> {
    // 1. Check LIT_PASSPHRASE env var
    if let Ok(pass) = std::env::var("LIT_PASSPHRASE") {
        if !pass.is_empty() {
            return Some(Zeroizing::new(pass));
        }
    }

    // 2. Check LIT_PASSPHRASE_FILE env var
    if let Ok(path) = std::env::var("LIT_PASSPHRASE_FILE") {
        if let Ok(pass) = std::fs::read_to_string(&path) {
            let pass = pass
                .trim_end_matches('\n')
                .trim_end_matches('\r')
                .to_string();
            if !pass.is_empty() {
                return Some(Zeroizing::new(pass));
            }
        }
    }

    // 3. Check cache
    if config.cache_timeout_secs > 0 {
        if let Some(cached) = get_cached_passphrase(repo_path) {
            return Some(cached);
        }
    }

    None
}

/// Prompt user for passphrase securely via CLI
///
/// Priority: LIT_PASSPHRASE env > LIT_PASSPHRASE_FILE > cache > interactive prompt.
/// In non-interactive mode (default for agents), returns error if no passphrase
/// is available from env/file/cache.
pub fn prompt_for_passphrase(
    repo_path: &str,
    config: &EncryptionConfig,
    prompt_text: &str,
) -> Result<Zeroizing<String>, String> {
    // Try non-interactive sources first
    if let Some(pass) = get_passphrase_non_interactive(repo_path, config) {
        return Ok(pass);
    }

    // Fall back to interactive prompt
    rpassword::prompt_password(prompt_text)
        .map(Zeroizing::new)
        .map_err(|e| format!("Failed to read passphrase: {}", e))
}

/// Minimum passphrase length (NIST SP 800-63B recommendation for high security)
const MIN_PASSPHRASE_LENGTH: usize = 16;

/// Validate passphrase strength
///
/// Requirements:
/// - Minimum 16 characters (NIST SP 800-63B)
/// - At least 3 of: uppercase, lowercase, digits, special characters
fn validate_passphrase_strength(passphrase: &str) -> Result<(), String> {
    if passphrase.len() < MIN_PASSPHRASE_LENGTH {
        return Err(format!(
            "Passphrase must be at least {} characters (recommended: 20+)",
            MIN_PASSPHRASE_LENGTH
        ));
    }

    // Check complexity
    let has_upper = passphrase.chars().any(|c| c.is_uppercase());
    let has_lower = passphrase.chars().any(|c| c.is_lowercase());
    let has_digit = passphrase.chars().any(|c| c.is_numeric());
    let has_special = passphrase.chars().any(|c| !c.is_alphanumeric());

    let complexity_count = [has_upper, has_lower, has_digit, has_special]
        .iter()
        .filter(|&&x| x)
        .count();

    if complexity_count < 3 {
        return Err(
            "Passphrase must include at least 3 of: uppercase, lowercase, digits, special characters"
                .to_string(),
        );
    }

    Ok(())
}

/// Prompt for passphrase confirmation (for new passphrases)
///
/// Priority: LIT_PASSPHRASE env > LIT_PASSPHRASE_FILE > interactive prompt (with confirmation).
pub fn prompt_for_passphrase_confirmation(prompt_text: &str) -> Result<Zeroizing<String>, String> {
    // Check LIT_PASSPHRASE env var
    if let Ok(pass) = std::env::var("LIT_PASSPHRASE") {
        if !pass.is_empty() {
            validate_passphrase_strength(&pass)?;
            return Ok(Zeroizing::new(pass));
        }
    }

    // Check LIT_PASSPHRASE_FILE env var
    if let Ok(path) = std::env::var("LIT_PASSPHRASE_FILE") {
        if let Ok(pass) = std::fs::read_to_string(&path) {
            let pass = pass
                .trim_end_matches('\n')
                .trim_end_matches('\r')
                .to_string();
            if !pass.is_empty() {
                validate_passphrase_strength(&pass)?;
                return Ok(Zeroizing::new(pass));
            }
        }
    }

    // Interactive prompt with confirmation
    let pass1 = rpassword::prompt_password(prompt_text)
        .map_err(|e| format!("Failed to read passphrase: {}", e))?;

    let pass2 = rpassword::prompt_password("Confirm passphrase: ")
        .map_err(|e| format!("Failed to read passphrase confirmation: {}", e))?;

    if pass1 != pass2 {
        return Err("Passphrases do not match".to_string());
    }

    validate_passphrase_strength(&pass1)?;

    Ok(Zeroizing::new(pass1))
}

pub(crate) fn prompt_for_new_passphrase() -> Result<Zeroizing<String>, String> {
    if let Some(path) = std::env::var_os("LIT_NEW_PASSPHRASE_FILE") {
        let mut passphrase = Zeroizing::new(
            fs::read_to_string(path)
                .map_err(|e| format!("Failed to read new passphrase file: {e}"))?,
        );
        let length = passphrase.trim_end_matches(['\r', '\n']).len();
        passphrase.truncate(length);
        validate_passphrase_strength(&passphrase)?;
        return Ok(passphrase);
    }
    let first = Zeroizing::new(
        rpassword::prompt_password("Enter new passphrase: ")
            .map_err(|e| format!("Failed to read new passphrase: {e}"))?,
    );
    let confirmation = Zeroizing::new(
        rpassword::prompt_password("Confirm new passphrase: ")
            .map_err(|e| format!("Failed to read passphrase confirmation: {e}"))?,
    );
    if *first != *confirmation {
        return Err("Passphrases do not match".into());
    }
    validate_passphrase_strength(&first)?;
    Ok(first)
}

pub(crate) const ROTATION_DIR: &str = ".lit/key-rotation";

pub(crate) fn require_rotation_complete(repo: &Path) -> Result<(), String> {
    match repo.join(ROTATION_DIR).symlink_metadata() {
        Ok(_) => Err(
            "Key rotation is in progress. Run 'lit rotate-key' with the new passphrase to resume."
                .into(),
        ),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("Cannot inspect key rotation state: {e}")),
    }
}

/// Serialize rotation against object writes that originate outside the CLI
/// write guard, such as objects arriving through a transport.
pub(crate) fn lock_key_writes(repo: &Path, exclusive: bool) -> std::io::Result<fs::File> {
    let file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(repo.join(".lit/encryption.guard"))?;
    if exclusive {
        file.lock()?;
    } else {
        file.lock_shared()?;
    }
    Ok(file)
}

pub(crate) fn repository_is_pristine(repo: &Path) -> Result<bool, String> {
    let metadata = repo.join(".lit");
    for directory in ["objects", "refs", "packs"] {
        let directory = metadata.join(directory);
        if !directory.try_exists().map_err(|e| e.to_string())? {
            continue;
        }
        for entry in walkdir::WalkDir::new(directory) {
            if entry.map_err(|e| e.to_string())?.file_type().is_file() {
                return Ok(false);
            }
        }
    }
    match fs::read(metadata.join("index")) {
        Ok(index) => Ok(index == b"{\n  \"entries\": {}\n}"),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(e) => Err(format!("Cannot inspect initial index: {e}")),
    }
}

/// Encryption manager for repository
pub struct EncryptionManager {
    config: EncryptionConfig,
    engine: Option<EncryptionEngine>,
    repo_path: Option<String>,
}

impl EncryptionManager {
    /// Create new encryption manager
    pub fn new(config: EncryptionConfig) -> Self {
        EncryptionManager {
            config,
            engine: None,
            repo_path: None,
        }
    }

    pub(crate) fn for_repository(
        repo: &Path,
        passphrase: Option<&str>,
        create_key: bool,
    ) -> Result<Self, String> {
        require_rotation_complete(repo)?;
        let config = EncryptionConfig::load(repo)?;
        if !config.enabled {
            return Ok(Self::new(config));
        }
        let passphrase = passphrase
            .map(|pass| Zeroizing::new(pass.to_owned()))
            .or_else(|| get_passphrase_non_interactive(repo.to_str()?, &config))
            .ok_or("Passphrase required for this repository (use --passphrase-file)")?;
        let expanded = shellexpand::tilde(&config.key_file);
        let key_exists = Path::new(expanded.as_ref())
            .try_exists()
            .map_err(|e| e.to_string())?;
        if create_key && !key_exists {
            if !repository_is_pristine(repo)? {
                return Err("Encryption key file is missing for an existing repository. Restore it from backup.".into());
            }
            let mut manager = Self::new(config);
            manager.initialize(&passphrase)?;
            Ok(manager)
        } else {
            Self::open_existing(config, &passphrase)
        }
    }

    pub(crate) fn open_existing(
        config: EncryptionConfig,
        passphrase: &str,
    ) -> Result<Self, String> {
        let path = shellexpand::tilde(&config.key_file);
        let key = EncryptionKey::load(Path::new(path.as_ref()), passphrase)?;
        Self::from_key(config, &key)
    }

    pub(crate) fn from_key(config: EncryptionConfig, key: &EncryptionKey) -> Result<Self, String> {
        let mut manager = Self::new(config);
        manager.engine = Some(EncryptionEngine::new(key)?);
        Ok(manager)
    }

    pub(crate) fn matches_config(&self, config: &EncryptionConfig) -> bool {
        self.config.enabled == config.enabled
            && (!config.enabled || self.config.key_file == config.key_file)
    }

    /// Initialize encryption with passphrase
    pub fn initialize(&mut self, passphrase: &str) -> Result<(), String> {
        if !self.config.enabled {
            return Ok(());
        }

        let expanded = shellexpand::tilde(&self.config.key_file);
        let key_file = Path::new(expanded.as_ref());

        // Load or create encryption key
        let key = if key_file.exists() {
            EncryptionKey::load(key_file, passphrase)?
        } else {
            let key = EncryptionKey::from_passphrase(passphrase, &EncryptionKey::generate_salt())?;
            if key.write_verifier(key_file, false)? {
                key
            } else {
                // Another repository sharing this key path initialized it first.
                EncryptionKey::load(key_file, passphrase)?
            }
        };

        // Create encryption engine
        self.engine = Some(EncryptionEngine::new(&key)?);

        Ok(())
    }

    /// Initialize encryption with passphrase caching support
    pub fn initialize_with_cache(
        &mut self,
        repo_path: &str,
        passphrase: Option<&str>,
    ) -> Result<(), String> {
        if !self.config.enabled {
            return Ok(());
        }

        self.repo_path = Some(repo_path.to_string());

        // Try to get cached passphrase first
        let actual_passphrase: Zeroizing<String> = if let Some(pass) = passphrase {
            Zeroizing::new(pass.to_string())
        } else if let Some(cached) = get_cached_passphrase(repo_path) {
            cached
        } else {
            return Err("No passphrase provided and no valid cached passphrase found".to_string());
        };

        // Initialize encryption
        self.initialize(&actual_passphrase)?;

        // Cache the passphrase if caching is enabled
        if self.config.cache_timeout_secs > 0 {
            let timeout = Duration::from_secs(self.config.cache_timeout_secs);
            cache_passphrase(repo_path, (*actual_passphrase).clone(), Some(timeout));
        }

        Ok(())
    }

    /// Encrypt data if encryption is enabled
    pub fn encrypt(&self, plaintext: &[u8]) -> Result<Vec<u8>, String> {
        if !self.config.enabled {
            return Ok(plaintext.to_vec());
        }

        match &self.engine {
            Some(engine) => engine.encrypt(plaintext),
            None => Err(
                "Encryption not initialized. Call initialize() with passphrase first.".to_string(),
            ),
        }
    }

    /// Decrypt data if encryption is enabled
    pub fn decrypt(&self, encrypted: &[u8]) -> Result<Vec<u8>, String> {
        if !self.config.enabled {
            return Ok(encrypted.to_vec());
        }

        match &self.engine {
            Some(engine) => engine.decrypt(encrypted),
            None => Err(
                "Encryption not initialized. Call initialize() with passphrase first.".to_string(),
            ),
        }
    }

    /// Check if encryption is enabled
    pub fn is_enabled(&self) -> bool {
        self.config.enabled
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_key_derivation() {
        let passphrase = "test-passphrase-12345-A1!";
        let salt = EncryptionKey::generate_salt();

        let key1 = EncryptionKey::from_passphrase(passphrase, &salt).unwrap();
        let key2 = EncryptionKey::from_passphrase(passphrase, &salt).unwrap();

        // Same passphrase and salt should produce same key
        assert_eq!(key1.as_bytes(), key2.as_bytes());
    }

    #[test]
    fn test_encryption_decryption() {
        let passphrase = "test-secure-passphrase-A1!";
        let salt = EncryptionKey::generate_salt();
        let key = EncryptionKey::from_passphrase(passphrase, &salt).unwrap();

        let engine = EncryptionEngine::new(&key).unwrap();

        let plaintext = b"Hello, this is secret data!";

        // Encrypt
        let encrypted = engine.encrypt(plaintext).unwrap();

        // Verify encrypted data is different
        assert_ne!(encrypted.as_slice(), plaintext);

        // Decrypt
        let decrypted = engine.decrypt(&encrypted).unwrap();

        // Verify original data restored
        assert_eq!(decrypted.as_slice(), plaintext);
    }

    #[test]
    fn test_encryption_nonce_randomness() {
        let passphrase = "test-passphrase-A1!";
        let salt = EncryptionKey::generate_salt();
        let key = EncryptionKey::from_passphrase(passphrase, &salt).unwrap();

        let engine = EncryptionEngine::new(&key).unwrap();

        let plaintext = b"Same data";

        // Encrypt same data twice
        let encrypted1 = engine.encrypt(plaintext).unwrap();
        let encrypted2 = engine.encrypt(plaintext).unwrap();

        // Should produce different ciphertexts (different nonces)
        assert_ne!(encrypted1, encrypted2);

        // But both should decrypt to same plaintext
        assert_eq!(engine.decrypt(&encrypted1).unwrap(), plaintext);
        assert_eq!(engine.decrypt(&encrypted2).unwrap(), plaintext);
    }

    #[test]
    fn test_tampering_detection() {
        let passphrase = "test-passphrase-A1!";
        let salt = EncryptionKey::generate_salt();
        let key = EncryptionKey::from_passphrase(passphrase, &salt).unwrap();

        let engine = EncryptionEngine::new(&key).unwrap();

        let plaintext = b"Secret data";
        let mut encrypted = engine.encrypt(plaintext).unwrap();

        // Tamper with ciphertext
        let len = encrypted.len();
        encrypted[len - 1] ^= 0x01;

        // Decryption should fail due to authentication tag mismatch
        assert!(engine.decrypt(&encrypted).is_err());
    }

    #[test]
    fn test_encryption_manager_disabled() {
        let config = EncryptionConfig {
            enabled: false,
            ..Default::default()
        };

        let manager = EncryptionManager::new(config);

        let data = b"Some data";

        // When disabled, should return data as-is
        assert_eq!(manager.encrypt(data).unwrap(), data);
        assert_eq!(manager.decrypt(data).unwrap(), data);
    }

    #[test]
    fn test_passphrase_caching() {
        let repo_path = "/tmp/test-repo";
        let passphrase = "cache-test-passphrase".to_string();

        // Clear cache first
        clear_passphrase_cache();

        // Should return None when not cached
        assert!(get_cached_passphrase(repo_path).is_none());

        // Cache passphrase with 5 second timeout
        cache_passphrase(repo_path, passphrase.clone(), Some(Duration::from_secs(5)));

        // Should retrieve cached passphrase
        assert_eq!(&*get_cached_passphrase(repo_path).unwrap(), &passphrase);

        // Clear specific entry
        clear_cached_passphrase(repo_path);
        assert!(get_cached_passphrase(repo_path).is_none());
    }

    #[test]
    fn test_passphrase_cache_expiration() {
        let repo_path = "/tmp/test-repo-expire";
        let passphrase = "expire-test".to_string();

        clear_passphrase_cache();

        // Cache with short timeout
        cache_passphrase(
            repo_path,
            passphrase.clone(),
            Some(Duration::from_millis(500)),
        );

        // Immediately should be available
        assert_eq!(&*get_cached_passphrase(repo_path).unwrap(), &passphrase);

        // Wait for expiration
        std::thread::sleep(Duration::from_millis(1000));

        // Should be expired and removed
        assert!(get_cached_passphrase(repo_path).is_none());
    }

    #[test]
    fn test_passphrase_cache_multiple_repos() {
        let repo1 = "/tmp/multi-cache-repo1";
        let repo2 = "/tmp/multi-cache-repo2";
        let pass1 = "password1".to_string();
        let pass2 = "password2".to_string();

        clear_passphrase_cache();

        // Cache different passphrases for different repos
        cache_passphrase(repo1, pass1.clone(), Some(Duration::from_secs(60)));
        cache_passphrase(repo2, pass2.clone(), Some(Duration::from_secs(60)));

        // Should retrieve correct passphrase for each repo
        assert_eq!(&*get_cached_passphrase(repo1).unwrap(), &pass1);
        assert_eq!(&*get_cached_passphrase(repo2).unwrap(), &pass2);
    }

    #[test]
    fn test_encryption_manager_with_cache() {
        let repo = tempfile::tempdir().unwrap();
        let repo_str = repo.path().to_str().unwrap();
        let config = EncryptionConfig {
            enabled: true,
            key_file: repo.path().join("key").to_str().unwrap().to_owned(),
            cache_timeout_secs: 300,
            ..Default::default()
        };

        let mut manager = EncryptionManager::new(config);
        let passphrase = "test-cache-manager-pass-A1!";

        // Initialize with cache
        manager
            .initialize_with_cache(repo_str, Some(passphrase))
            .unwrap();

        // Passphrase should be cached
        assert_eq!(&*get_cached_passphrase(repo_str).unwrap(), passphrase);

        // Should be able to initialize again without providing passphrase
        let mut manager2 = EncryptionManager::new(manager.config.clone());
        manager2.initialize_with_cache(repo_str, None).unwrap();

        // Clear cache for cleanup
        PASSPHRASE_CACHE.lock().unwrap().remove(repo_str);
    }

    #[cfg(unix)]
    fn worker(home: &Path, action: &str, test: &str) -> std::process::Command {
        let mut command = std::process::Command::new(std::env::current_exe().unwrap());
        command
            .args(["--exact", test, "--nocapture"])
            .current_dir(home)
            .env("HOME", home)
            .env("LIT_CRYPTO_TEST_ACTION", action);
        command
    }

    #[cfg(unix)]
    #[test]
    fn test_rate_limiting() {
        use serde_json::json;
        const TEST: &str = "crypto::encryption::tests::test_rate_limiting";
        const PASS: &str = "Fixture-password-A1!";

        if let Ok(action) = std::env::var("LIT_CRYPTO_TEST_ACTION") {
            let key = Path::new("key");
            match action.as_str() {
                "prepare" => {
                    EncryptionKey::from_passphrase(PASS, &EncryptionKey::generate_salt())
                        .unwrap()
                        .save("key", PASS)
                        .unwrap();
                    fs::copy(key, "alias").unwrap();
                }
                "fail" => {
                    assert_eq!(
                        EncryptionKey::load(key, "short").err().unwrap(),
                        "Invalid passphrase"
                    );
                }
                "blocked" => {
                    let error = EncryptionKey::load(Path::new("alias"), "test-bypass")
                        .err()
                        .unwrap();
                    assert!(error.contains("Passphrase rate limit:"), "{error}");
                }
                "recover" => {
                    EncryptionKey::load(Path::new("alias"), PASS).unwrap();
                }
                _ => panic!("unexpected passphrase test worker"),
            }
            return;
        }

        let home = tempfile::tempdir().unwrap();
        let run = |action| {
            assert!(worker(home.path(), action, TEST)
                .status()
                .unwrap()
                .success())
        };
        run("prepare");
        run("fail");
        let key = fs::read(home.path().join("key")).unwrap();
        let identity = hex::encode(&key[SALT_SIZE + 1..]);
        let attempts = home
            .path()
            .join(".lit/key-state")
            .join(format!("{identity}.attempts"));
        let snapshot =
            || serde_json::from_slice::<serde_json::Value>(&fs::read(&attempts).unwrap()).unwrap();
        let initial = snapshot();
        assert_eq!(initial["pending"], false);
        assert_eq!(initial["failures"], 1);
        assert!(initial["retry_at"].as_u64().unwrap() > 0);
        // Widen the deadline in this disposable fixture to avoid depending on
        // process scheduling. The policy unit test checks the exact durations.
        let mut extended = initial;
        extended["retry_at"] = json!(u64::MAX - 1);
        fs::write(&attempts, serde_json::to_vec(&extended).unwrap()).unwrap();
        run("blocked");
        assert_eq!(snapshot(), extended);

        // A killed verifier left its reserved attempt pending.
        fs::write(&attempts, br#"{"failures":1,"retry_at":0,"pending":true}"#).unwrap();
        run("blocked");
        assert_eq!(snapshot()["pending"], false);
        assert!(snapshot()["retry_at"].as_u64().unwrap() > 0);

        fs::write(&attempts, br#"{"failures":1,"retry_at":0,"pending":false}"#).unwrap();
        run("recover");
        assert_eq!(
            snapshot(),
            json!({"failures":0,"retry_at":0,"pending":false})
        );
    }

    #[cfg(unix)]
    #[test]
    fn test_encryption_budget_across_processes() {
        use super::super::key_state::MAX_ENCRYPTIONS_PER_KEY;
        use serde_json::json;
        const TEST: &str = "crypto::encryption::tests::test_encryption_budget_across_processes";
        // A fixed test-only AES key avoids repeatedly deriving the same key in
        // children. Each worker still goes through the actual encrypt method.
        let mut key = EncryptionKey {
            key_bytes: [0x5a; KEY_SIZE],
            salt: [0x7b; SALT_SIZE],
            origin: KeyOrigin::Managed,
        };
        if let Ok(action) = std::env::var("LIT_CRYPTO_TEST_ACTION") {
            if action == "legacy" {
                key.origin = KeyOrigin::Legacy(None);
            }
            let engine = EncryptionEngine::new(&key).unwrap();
            match action.as_str() {
                "compete" => match engine.encrypt(b"reserved encryption") {
                    Ok(content) => fs::write("ciphertext", content).unwrap(),
                    Err(error) => {
                        assert!(error.contains("Encryption limit reached"), "{error}");
                        std::process::exit(2);
                    }
                },
                "crash" => {
                    fs::write(
                        "ciphertext",
                        engine.encrypt(b"reserved encryption").unwrap(),
                    )
                    .unwrap();
                    fs::write("ready", "").unwrap();
                    loop {
                        std::thread::park();
                    }
                }
                "missing" | "legacy" => {
                    let previous = fs::read("ciphertext").unwrap();
                    assert_eq!(engine.decrypt(&previous).unwrap(), b"reserved encryption");
                    let error = engine.encrypt(b"must not write").unwrap_err();
                    assert!(error.contains("ledger"), "{error}");
                }
                _ => panic!("unexpected budget test worker"),
            }
            return;
        }

        let home = tempfile::tempdir().unwrap();
        let state = home.path().join(".lit/key-state");
        fs::create_dir_all(&state).unwrap();
        let usage = state.join(format!("{}.usage", hex::encode(key.verifier())));
        let seed = |reserved| {
            fs::write(
                &usage,
                serde_json::to_vec(&json!({
                    "version": 1, "reserved": reserved
                }))
                .unwrap(),
            )
            .unwrap()
        };
        seed(MAX_ENCRYPTIONS_PER_KEY - 1);
        let mut first = worker(home.path(), "compete", TEST).spawn().unwrap();
        let mut second = worker(home.path(), "compete", TEST).spawn().unwrap();
        let (first, second) = (first.wait().unwrap(), second.wait().unwrap());
        assert_ne!(
            first.success(),
            second.success(),
            "only one process may use the last encryption"
        );
        assert_eq!(first.code().unwrap() + second.code().unwrap(), 2);
        assert!(worker(home.path(), "legacy", TEST)
            .status()
            .unwrap()
            .success());

        // An interrupted process must not refund its unused reservation.
        seed(MAX_ENCRYPTIONS_PER_KEY - 2);
        let mut crash = worker(home.path(), "crash", TEST).spawn().unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while !home.path().join("ready").is_file() && std::time::Instant::now() < deadline {
            if crash.try_wait().unwrap().is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        let reached = home.path().join("ready").is_file();
        let _ = crash.kill();
        assert!(!crash.wait().unwrap().success());
        assert!(reached, "process never reserved budget");
        assert_eq!(
            worker(home.path(), "compete", TEST)
                .status()
                .unwrap()
                .code(),
            Some(2)
        );
        fs::remove_file(&usage).unwrap();
        assert!(worker(home.path(), "missing", TEST)
            .status()
            .unwrap()
            .success());
    }
}
