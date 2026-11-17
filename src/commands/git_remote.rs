use super::git_interop::{checked_output, git, text, valid_oid};
use crate::core::{delete_ref, get_current_branch, list_refs, read_head, read_ref};
use crate::errors::LitError;
use crate::network::transport::update_remote_tracking_ref;
use crate::response::{FetchResponse, PushResponse};
use std::fs;
use std::io::Write;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

pub(super) fn is_git(url: &str) -> bool {
    if let Some(path) = url.strip_prefix("file://") {
        let path = path.to_ascii_lowercase();
        if !path.starts_with('/')
            || path.starts_with("//")
            || path.starts_with("/%2f")
            || path.starts_with("/%5c")
            || path.starts_with("/\\")
            || crate::network::transport::resolve_path(url).is_ok()
        {
            return false;
        }
        return std::env::current_dir().ok().is_some_and(|cwd| {
            transport_output(git_transport(&cwd, url).args(["ls-remote", "--", url]), "discover")
                .is_ok()
        });
    }
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return false;
    }
    let endpoint = format!(
        "{}/info/refs?service=git-upload-pack",
        url.trim_end_matches('/')
    );
    let agent = ureq::AgentBuilder::new()
        .redirects(0)
        .timeout(Duration::from_secs(10))
        .build();
    agent.get(&endpoint).call().ok().is_some_and(|response| {
        response
            .header("Content-Type")
            .and_then(|value| value.split(';').next())
            .is_some_and(|value| {
                value
                    .trim()
                    .eq_ignore_ascii_case("application/x-git-upload-pack-advertisement")
            })
    })
}

fn git_transport(root: &Path, url: &str) -> Command {
    let protocol = if url.starts_with("file://") {
        "file"
    } else if url.starts_with("https://") {
        "https"
    } else {
        "http"
    };
    let mut command = git(root);
    command
        .args([
            "-c",
            "http.followRedirects=false",
            "-c",
            "credential.helper=",
        ])
        .env("GIT_ALLOW_PROTOCOL", protocol)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "")
        .env_remove("GIT_CONFIG_COUNT")
        .env_remove("GIT_CONFIG_PARAMETERS")
        .env_remove("GIT_ASKPASS");
    command
}

fn transport_output(
    command: &mut Command,
    operation: &str,
) -> Result<std::process::Output, LitError> {
    command.stdin(std::process::Stdio::null());
    let output = command
        .output()
        .map_err(|error| LitError::io(format!("Could not start Git: {error}")))?;
    if !output.status.success() {
        return Err(format!(
            "Git {operation} failed. Check the remote URL, access permissions, and branch history."
        )
        .into());
    }
    Ok(output)
}

pub(super) fn clone_into(root: &Path, url: &str) -> Result<(Vec<String>, usize), LitError> {
    let temp = tempfile::tempdir().map_err(|e| e.to_string())?;
    let mirror = temp.path().join("mirror.git");
    let source = mirror.to_str().ok_or("Temporary Git path is not UTF-8")?;
    transport_output(git_transport(temp.path(), url).args([
        "clone",
        "--mirror",
        "--no-local",
        "--",
        url,
        source,
    ]), "clone")?;
    let imported = super::import_git::execute_at(source.to_owned(), Some(root))?;
    let branches = list_refs(root, "heads")?;
    let default_branch = get_current_branch(root).ok();
    for branch in &branches {
        update_remote_tracking_ref(root, "origin", &branch.name, &branch.hash)?;
        if default_branch.as_deref() != Some(&branch.name) {
            delete_ref(root, &format!("heads/{}", branch.name))?;
        }
    }
    if let Ok(head) = read_head(root) {
        super::worktree::install(root, None, &head, None)?;
    }
    Ok((
        branches.into_iter().map(|branch| branch.name).collect(),
        imported.objects_imported as usize,
    ))
}

pub(super) fn fetch(
    root: &Path,
    remote: String,
    url: &str,
    branch: Option<String>,
) -> Result<FetchResponse, LitError> {
    let temp = tempfile::tempdir().map_err(|e| e.to_string())?;
    let stage = temp.path();
    init_bare(stage)?;
    let refspec = if let Some(branch) = &branch {
        format!("+refs/heads/{branch}:refs/heads/{branch}")
    } else {
        "+refs/heads/*:refs/heads/*".to_owned()
    };
    transport_output(
        git_transport(stage, url).args(["fetch", "--no-tags", "--", url, &refspec]),
        "fetch",
    )?;
    let output = checked_output(git(stage).args([
        "for-each-ref",
        "--format=%(objectname)%09%(refname:strip=2)",
        "refs/heads",
    ]))?;
    let mut refs = Vec::new();
    for line in text(&output.stdout)?.lines() {
        let (oid, branch) = line
            .split_once('\t')
            .ok_or("Invalid Git branch advertisement")?;
        if !valid_oid(oid) {
            return Err("Only SHA-1 Git remotes are supported".into());
        }
        super::remote::validate_ref(branch)?;
        refs.push((oid, branch));
    }
    let roots = refs.iter().map(|(oid, _)| (*oid).to_owned()).collect();
    let (objects, count) = super::import_git::read_objects(stage, root, roots, false)?;
    let mut branches_updated = Vec::new();
    for (oid, branch) in refs {
        let hash = objects.get(oid).ok_or("Missing Git branch object")?;
        let tracking = format!("remotes/{remote}/{branch}");
        if read_ref(root, &tracking).ok().as_deref() != Some(hash.as_str()) {
            update_remote_tracking_ref(root, &remote, branch, hash.as_str())?;
            branches_updated.push(format!("{} -> {remote}/{branch}", hash.short()));
        }
    }
    Ok(FetchResponse {
        message: format!(
            "Fetched from {remote}: {count} new Lit objects, {} branches updated",
            branches_updated.len()
        ),
        remote,
        branches_updated,
        objects_transferred: count,
    })
}

pub(super) fn push(
    root: &Path,
    remote: String,
    url: &str,
    branch: String,
    force: bool,
) -> Result<PushResponse, LitError> {
    let _lock = super::transaction::lock_writes(root)?;
    let local_hash = read_ref(root, &format!("heads/{branch}"))?;
    let temp = tempfile::tempdir().map_err(|e| e.to_string())?;
    let stage = temp.path();
    init_bare(stage)?;
    let (objects, _) = super::export_git::write_objects(root, stage, vec![local_hash.clone()])?;
    let tip = objects
        .get(&local_hash)
        .ok_or("Missing converted Git branch tip")?;
    checked_output(git(stage).args(["update-ref", "refs/heads/main", tip]))?;
    checked_output(git(stage).args(["fsck", "--full", "--strict"]))?;
    // Preserve local identities when a future fetch sees a Git object we
    // exported. Record them before push so a successful push is recoverable.
    let mapping = root.join(".lit/git-export-map");
    fs::create_dir_all(&mapping).map_err(|e| e.to_string())?;
    for (lit_id, git_id) in &objects {
        let path = mapping.join(git_id);
        if !path.exists() || lit_id == &local_hash {
            let mut file = tempfile::NamedTempFile::new_in(&mapping).map_err(|e| e.to_string())?;
            writeln!(file, "{lit_id}").map_err(|e| e.to_string())?;
            file.persist(path).map_err(|e| e.error.to_string())?;
        }
    }
    let refspec = format!(
        "{}refs/heads/main:refs/heads/{branch}",
        if force { "+" } else { "" }
    );
    let result = transport_output(git_transport(stage, url).args([
        "push",
        "--porcelain",
        "--",
        url,
        &refspec,
    ]), "push")?;
    let stdout = text(&result.stdout)?;
    let updated = stdout.lines().any(|line| line.starts_with([' ', '*', '+']));
    update_remote_tracking_ref(root, &remote, &branch, &local_hash)?;
    let message = if updated {
        "Git branch updated (pack object count unavailable)"
    } else {
        "Everything up to date"
    };
    Ok(PushResponse {
        remote,
        branch,
        objects_transferred: 0,
        updated,
        message: message.into(),
    })
}

fn init_bare(root: &Path) -> Result<(), LitError> {
    checked_output(git(root).args([
        "init",
        "--bare",
        "--initial-branch=main",
        "--object-format=sha1",
        "--template=",
    ]))?;
    Ok(())
}
