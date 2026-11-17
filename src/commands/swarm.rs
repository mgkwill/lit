use crate::core::find_repo_root;
use crate::errors::LitError;
use crate::response::SwarmResponse;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io::ErrorKind;
use std::path::{Component, Path, PathBuf};

/// Lease state for exclusive file access
#[derive(Debug, Clone, Serialize, Deserialize)]
struct FileLease {
    agent_id: String,
    path: String,
    acquired_at: i64,
    expires_at: i64,
}

/// Agent registration record
#[derive(Debug, Clone, Serialize, Deserialize)]
struct AgentRecord {
    agent_id: String,
    registered_at: i64,
    branches: Vec<String>,
    leases: Vec<String>,
}

fn swarm_dir(repo_root: &Path) -> PathBuf {
    repo_root.join(".lit").join("swarm")
}

fn leases_dir(repo_root: &Path) -> PathBuf {
    swarm_dir(repo_root).join("leases")
}

fn agents_dir(repo_root: &Path) -> PathBuf {
    swarm_dir(repo_root).join("agents")
}

/// Register an agent in the swarm — creates namespaced branch prefix
pub fn execute_register(agent_id: String) -> Result<SwarmResponse, crate::errors::LitError> {
    if agent_id.is_empty()
        || !agent_id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
    {
        return Err("Agent IDs must contain only letters, digits, hyphens, and underscores".into());
    }
    let repo_root = find_repo_root()?;
    let metadata = repo_root.join(".lit");
    if !metadata
        .symlink_metadata()
        .is_ok_and(|entry| entry.is_dir())
    {
        return Err("Expected a local .lit metadata directory".into());
    }
    let _guard = super::transaction::lock_writes(&repo_root)?;
    let agent_refs = super::worktree::safe_path(&metadata, &format!("refs/agents/{agent_id}"))?;
    let path = super::worktree::safe_path(&metadata, &format!("swarm/agents/{agent_id}.json"))?;
    let leases = super::worktree::safe_path(&metadata, "swarm/leases")?;
    if agent_refs
        .symlink_metadata()
        .is_ok_and(|entry| !entry.is_dir())
    {
        return Err("Agent ref namespace is blocked".into());
    }
    if leases.symlink_metadata().is_ok_and(|entry| !entry.is_dir()) {
        return Err("Swarm lease directory is blocked".into());
    }
    let record = match path.symlink_metadata() {
        Ok(entry) if entry.is_file() => {
            let record: AgentRecord =
                serde_json::from_slice(&fs::read(&path).map_err(|e| e.to_string())?)
                    .map_err(|e| format!("Invalid agent record: {e}"))?;
            if record.agent_id != agent_id {
                return Err("Agent ID conflicts with an existing registration".into());
            }
            None
        }
        Ok(_) => return Err("Agent registration path is blocked".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Some(AgentRecord {
            agent_id: agent_id.clone(),
            registered_at: chrono::Utc::now().timestamp(),
            branches: Vec::new(),
            leases: Vec::new(),
        }),
        Err(error) => return Err(crate::errors::LitError::io(error.to_string())),
    };
    fs::create_dir_all(agents_dir(&repo_root))
        .map_err(|e| format!("Failed to create swarm dir: {e}"))?;
    fs::create_dir_all(leases).map_err(|e| format!("Failed to create leases dir: {e}"))?;
    fs::create_dir_all(agent_refs).map_err(|e| format!("Failed to create agent refs: {e}"))?;
    if let Some(record) = record {
        let data = serde_json::to_vec_pretty(&record).map_err(|e| e.to_string())?;
        super::worktree::replace(&path, &data)?;
    }

    Ok(SwarmResponse {
        action: "register".to_string(),
        agent_id: Some(agent_id.clone()),
        message: format!(
            "Agent '{}' registered. Branch namespace: agents/{}/",
            agent_id, agent_id
        ),
        details: Some(serde_json::json!({
            "namespace": format!("agents/{}", agent_id),
            "refs_path": format!(".lit/refs/agents/{}", agent_id),
        })),
    })
}

/// List registered agents
pub fn execute_list() -> Result<SwarmResponse, crate::errors::LitError> {
    let repo_root = find_repo_root()?;
    let agents = agents_dir(&repo_root);

    if !agents.exists() {
        return Ok(SwarmResponse {
            action: "list".to_string(),
            agent_id: None,
            message: "No agents registered".to_string(),
            details: Some(serde_json::json!({ "agents": [] })),
        });
    }

    let mut agent_list = Vec::new();
    for entry in fs::read_dir(&agents).map_err(|e| format!("Failed to read agents dir: {}", e))? {
        let entry = entry.map_err(|e| e.to_string())?;
        if entry
            .path()
            .extension()
            .map(|e| e == "json")
            .unwrap_or(false)
        {
            let data = fs::read_to_string(entry.path()).map_err(|e| e.to_string())?;
            if let Ok(record) = serde_json::from_str::<AgentRecord>(&data) {
                agent_list.push(serde_json::json!({
                    "agent_id": record.agent_id,
                    "registered_at": record.registered_at,
                    "branches": record.branches.len(),
                    "leases": record.leases.len(),
                }));
            }
        }
    }

    Ok(SwarmResponse {
        action: "list".to_string(),
        agent_id: None,
        message: format!("{} agent(s) registered", agent_list.len()),
        details: Some(serde_json::json!({ "agents": agent_list })),
    })
}

/// Acquire an exclusive file lease
pub fn execute_lease_acquire(
    agent_id: String,
    file_path: String,
    duration_secs: u64,
) -> Result<SwarmResponse, crate::errors::LitError> {
    let file_path = lease_key(&file_path)?;
    let repo_root = find_repo_root()?;
    let state = lease_state_path(&repo_root)?;
    let _guard = super::transaction::lock_writes(&repo_root)?;
    let now = chrono::Utc::now().timestamp();
    let expires_at = i64::try_from(duration_secs)
        .ok()
        .filter(|duration| *duration > 0)
        .and_then(|duration| now.checked_add(duration))
        .ok_or("Lease duration must be positive and fit in the expiry timestamp")?;

    let mut leases = load_leases(&repo_root, &state)?;
    if let Some(existing) = leases.get(&file_path) {
        if existing.expires_at > now && existing.agent_id != agent_id {
            return Err(format!(
                "File '{}' is leased by agent '{}' until timestamp {}",
                file_path, existing.agent_id, existing.expires_at
            )
            .into());
        }
    }

    let lease = FileLease {
        agent_id: agent_id.clone(),
        path: file_path.clone(),
        acquired_at: now,
        expires_at,
    };
    leases.insert(file_path.clone(), lease);
    save_leases(&state, &leases)?;

    Ok(SwarmResponse {
        action: "lease-acquire".to_string(),
        agent_id: Some(agent_id),
        message: format!(
            "Lease acquired on '{}' for {} seconds",
            file_path, duration_secs
        ),
        details: Some(serde_json::json!({
            "path": file_path,
            "expires_at": expires_at,
        })),
    })
}

/// Release a file lease
pub fn execute_lease_release(
    agent_id: String,
    file_path: String,
) -> Result<SwarmResponse, crate::errors::LitError> {
    let file_path = lease_key(&file_path)?;
    let repo_root = find_repo_root()?;
    let state = lease_state_path(&repo_root)?;
    let _guard = super::transaction::lock_writes(&repo_root)?;
    let mut leases = load_leases(&repo_root, &state)?;
    let existing = leases
        .get(&file_path)
        .ok_or_else(|| format!("No lease exists for '{file_path}'"))?;

    if existing.agent_id != agent_id {
        return Err(format!(
            "Lease on '{}' is held by agent '{}', not '{}'",
            file_path, existing.agent_id, agent_id
        )
        .into());
    }

    leases.remove(&file_path);
    save_leases(&state, &leases)?;

    Ok(SwarmResponse {
        action: "lease-release".to_string(),
        agent_id: Some(agent_id),
        message: format!("Lease released on '{}'", file_path),
        details: None,
    })
}

/// List active and expired advisory leases.
pub fn execute_lease_list() -> Result<SwarmResponse, LitError> {
    let repo_root = find_repo_root()?;
    let state = lease_state_path(&repo_root)?;
    let now = chrono::Utc::now().timestamp();
    let leases: Vec<_> = load_leases(&repo_root, &state)?
        .into_values()
        .map(|lease| {
            serde_json::json!({
                "agent_id": lease.agent_id,
                "path": lease.path,
                "acquired_at": lease.acquired_at,
                "expires_at": lease.expires_at,
                "expired": lease.expires_at <= now,
            })
        })
        .collect();
    Ok(SwarmResponse {
        action: "lease-list".to_string(),
        agent_id: None,
        message: format!("{} lease(s)", leases.len()),
        details: Some(serde_json::json!({ "leases": leases })),
    })
}

fn lease_key(path: &str) -> Result<String, LitError> {
    if path.contains('\\') {
        return Err("Use a repository-relative path with / separators".into());
    }
    let mut parts = Vec::new();
    for part in Path::new(path).components() {
        match part {
            Component::CurDir => {}
            Component::Normal(name) => {
                let name = name.to_str().ok_or("Invalid lease path")?;
                if name.eq_ignore_ascii_case(".lit") || name.eq_ignore_ascii_case(".git") {
                    return Err("Repository metadata cannot be leased".into());
                }
                parts.push(name);
            }
            _ => return Err("Use a repository-relative path without parent traversal".into()),
        }
    }
    if parts.is_empty() {
        return Err("Provide a file path to lease".into());
    }
    Ok(parts.join("/"))
}

fn lease_state_path(root: &Path) -> Result<PathBuf, LitError> {
    let metadata = root.join(".lit");
    if !metadata
        .symlink_metadata()
        .is_ok_and(|entry| entry.is_dir())
    {
        return Err("Expected a local .lit metadata directory".into());
    }
    super::worktree::safe_path(&metadata, "swarm/file-leases-v2.json")
}

fn load_leases(root: &Path, state: &Path) -> Result<BTreeMap<String, FileLease>, LitError> {
    match state.symlink_metadata() {
        Ok(entry) if entry.is_file() => {
            let bytes = fs::read(state).map_err(|e| e.to_string())?;
            return serde_json::from_slice(&bytes)
                .map_err(|e| format!("Invalid swarm lease state: {e}").into());
        }
        Ok(_) => return Err("Swarm lease state path is blocked".into()),
        Err(error) if error.kind() == ErrorKind::NotFound => {}
        Err(error) => return Err(LitError::io(error.to_string())),
    }

    // Import legacy records by their stored path, which retains the original
    // spelling even where the old filename encoding was lossy.
    let legacy = leases_dir(root);
    match legacy.symlink_metadata() {
        Ok(entry) if entry.is_dir() => {}
        Ok(_) => return Err("Swarm lease directory is blocked".into()),
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(BTreeMap::new()),
        Err(error) => return Err(LitError::io(error.to_string())),
    }
    let now = chrono::Utc::now().timestamp();
    let mut leases = BTreeMap::<String, FileLease>::new();
    for entry in fs::read_dir(legacy).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        if !entry
            .path()
            .extension()
            .is_some_and(|value| value == "json")
        {
            continue;
        }
        if !entry.file_type().map_err(|e| e.to_string())?.is_file() {
            return Err("Legacy swarm lease path is blocked".into());
        }
        let bytes = fs::read(entry.path()).map_err(|e| e.to_string())?;
        let mut lease: FileLease = serde_json::from_slice(&bytes)
            .map_err(|e| format!("Invalid legacy swarm lease: {e}"))?;
        lease.path = lease_key(&lease.path)?;
        if let Some(existing) = leases.get(&lease.path) {
            if existing.agent_id != lease.agent_id
                && existing.expires_at.min(lease.expires_at) > now
            {
                return Err(format!("Conflicting active legacy leases for {}", lease.path).into());
            }
            if existing.expires_at >= lease.expires_at {
                continue;
            }
        }
        leases.insert(lease.path.clone(), lease);
    }
    Ok(leases)
}

fn save_leases(state: &Path, leases: &BTreeMap<String, FileLease>) -> Result<(), LitError> {
    fs::create_dir_all(state.parent().ok_or("Lease state has no parent")?)
        .map_err(|e| e.to_string())?;
    let data = serde_json::to_vec(leases).map_err(|e| e.to_string())?;
    super::worktree::replace(state, &data)
}
