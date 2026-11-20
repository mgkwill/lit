use serde::{Deserialize, Serialize};
use std::fs;
use std::io::ErrorKind;
use std::path::Path;

pub(crate) const FILE: &str = ".lit/repository-format.json";
pub(crate) const LEGACY_REFS_FILE: &str = ".lit/refs.enc";
pub(crate) const VERSION: u32 = 1;

#[derive(Debug, PartialEq, Eq, Deserialize, Serialize)]
pub(crate) struct Receipt {
    pub version: u32,
    pub original_path_sha256: String,
    pub source_sha256: String,
}

pub(crate) fn read(repo: &Path) -> Result<Option<Receipt>, String> {
    let path = repo.join(FILE);
    match path.symlink_metadata() {
        Ok(meta) if meta.is_file() => {}
        Ok(_) => return Err("Lit repository format marker must be a regular file".into()),
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("Cannot inspect Lit repository format: {error}")),
    }
    let data =
        fs::read(path).map_err(|error| format!("Cannot read Lit repository format: {error}"))?;
    let receipt: Receipt = serde_json::from_slice(&data)
        .map_err(|error| format!("Invalid Lit repository format marker: {error}"))?;
    if receipt.version != VERSION {
        return Err(format!(
            "Unsupported Lit repository format {}",
            receipt.version
        ));
    }
    for digest in [&receipt.original_path_sha256, &receipt.source_sha256] {
        if digest.len() != 64 || !digest.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err("Invalid Lit repository migration receipt".into());
        }
    }
    Ok(Some(receipt))
}

pub(crate) fn require_supported(repo: &Path) -> Result<(), String> {
    read(repo)?;
    match repo.join(LEGACY_REFS_FILE).symlink_metadata() {
        Ok(_) => Err("Packed encrypted refs require 'lit migrate-repo'".into()),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.to_string()),
    }
}
