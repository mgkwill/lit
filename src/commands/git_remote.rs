use super::git_interop::{checked_output, git, text, valid_oid};
use crate::core::{delete_ref, get_current_branch, list_refs, read_head, read_ref};
use crate::errors::LitError;
use crate::network::transport::update_remote_tracking_ref;
use crate::response::{FetchResponse, PushResponse};
use std::borrow::Cow;
use std::fs;
use std::io::Write;
use std::path::Path;
use std::process::Command;
use std::time::Duration;
use url::Url;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Transport {
    File,
    Http,
    Https,
    AuthenticatedHttps,
    Ssh,
}

pub(super) struct GitRemote<'a> {
    url: Cow<'a, str>,
    transport: Transport,
}

pub(super) fn explicit(url: &str) -> Result<Option<GitRemote<'_>>, LitError> {
    let Some(raw) = url.strip_prefix("git+") else {
        return Ok(None);
    };
    let transport = if raw.starts_with("https://") {
        Transport::AuthenticatedHttps
    } else if raw.starts_with("ssh://") {
        Transport::Ssh
    } else {
        return Err("Explicit Git remotes use git+https:// or git+ssh://".into());
    };
    let invalid = || {
        LitError::network(
        "Invalid Git remote URL. Use a host and repository path without credentials, query, or fragment (SSH accepts a username).",
    )
    };
    // Parse before saving the URL or invoking any external client.
    if raw
        .chars()
        .any(|ch| ch.is_control() || ch.is_whitespace() || ch == '\\')
    {
        return Err(invalid());
    }
    let parsed = Url::parse(raw).map_err(|_| invalid())?;
    let host = parsed.host_str().ok_or_else(invalid)?;
    if host.starts_with('-')
        || host.contains('%')
        || parsed.path().trim_matches('/').is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
        || (transport == Transport::AuthenticatedHttps && !parsed.username().is_empty())
        || parsed.username().starts_with('-')
        || parsed.username().contains('%')
    {
        return Err(invalid());
    }
    Ok(Some(GitRemote {
        url: Cow::Owned(parsed.into()),
        transport,
    }))
}

pub(super) fn discover(url: &str) -> Result<Option<GitRemote<'_>>, LitError> {
    if let Some(remote) = explicit(url)? {
        return Ok(Some(remote));
    }
    if let Some(path) = url.strip_prefix("file://") {
        let path = path.to_ascii_lowercase();
        if !path.starts_with('/')
            || path.starts_with("//")
            || path.starts_with("/%2f")
            || path.starts_with("/%5c")
            || path.starts_with("/\\")
            || crate::network::transport::resolve_path(url).is_ok()
        {
            return Ok(None);
        }
        let remote = GitRemote {
            url: Cow::Borrowed(url),
            transport: Transport::File,
        };
        let cwd = std::env::current_dir()?;
        let found = transport_output(
            git_transport(&cwd, &remote)?.args(["ls-remote", "--", url]),
            "discover",
        )
        .is_ok();
        return Ok(found.then_some(remote));
    }
    let transport = if url.starts_with("https://") {
        Transport::Https
    } else if url.starts_with("http://") {
        Transport::Http
    } else {
        return Ok(None);
    };
    let endpoint = format!(
        "{}/info/refs?service=git-upload-pack",
        url.trim_end_matches('/')
    );
    let agent = ureq::AgentBuilder::new()
        .redirects(0)
        .timeout(Duration::from_secs(10))
        .build();
    let found = agent.get(&endpoint).call().ok().is_some_and(|response| {
        response
            .header("Content-Type")
            .and_then(|value| value.split(';').next())
            .is_some_and(|value| {
                value
                    .trim()
                    .eq_ignore_ascii_case("application/x-git-upload-pack-advertisement")
            })
    });
    Ok(found.then_some(GitRemote {
        url: Cow::Borrowed(url),
        transport,
    }))
}

fn git_transport(root: &Path, remote: &GitRemote<'_>) -> Result<Command, LitError> {
    let protocol = match remote.transport {
        Transport::File => "file",
        Transport::Http => "http",
        Transport::Https | Transport::AuthenticatedHttps => "https",
        Transport::Ssh => "ssh",
    };
    let mut command = git(root);
    command
        .args([
            "-c",
            "http.followRedirects=false",
            "-c",
            "http.sslVerify=true",
            "-c",
            "credential.helper=",
        ])
        .env("GIT_ALLOW_PROTOCOL", protocol)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "")
        .env_remove("GIT_CONFIG_COUNT")
        .env_remove("GIT_CONFIG_PARAMETERS")
        .env_remove("GIT_ASKPASS")
        .env_remove("SSH_ASKPASS")
        .env_remove("GIT_SSL_NO_VERIFY")
        .env_remove("GIT_TRACE_CURL")
        .env_remove("GIT_CURL_VERBOSE");
    match remote.transport {
        Transport::AuthenticatedHttps => {
            command.args(["-c", "credential.useHttpPath=true"]);
            if let Some(path) = local_path("LIT_GIT_ASKPASS")? {
                command.env("GIT_ASKPASS", path);
            }
        }
        Transport::Ssh => {
            command
                .env("GIT_SSH_COMMAND", ssh_command()?)
                .env("GIT_SSH_VARIANT", "ssh");
        }
        _ => {}
    }
    Ok(command)
}

fn local_path(variable: &str) -> Result<Option<String>, LitError> {
    let Some(path) = std::env::var_os(variable) else {
        return Ok(None);
    };
    let path = path
        .into_string()
        .map_err(|_| LitError::config(format!("{variable} must be a UTF-8 path")))?;
    if !Path::new(&path).is_absolute() || path.chars().any(char::is_control) {
        return Err(LitError::config(format!(
            "{variable} must be an absolute path"
        )));
    }
    Ok(Some(path))
}

fn shell_quote(argument: &str) -> String {
    format!("'{}'", argument.replace('\'', "'\\''"))
}

fn ssh_command() -> Result<String, LitError> {
    let mut command = String::from("ssh -F none -oBatchMode=yes -oStrictHostKeyChecking=yes -oUpdateHostKeys=no -oClearAllForwardings=yes");
    if let Some(path) = local_path("LIT_GIT_SSH_KNOWN_HOSTS")? {
        let path = path
            .replace('\\', "\\\\")
            .replace('"', "\\\"")
            .replace('%', "%%");
        command.push_str(" -oGlobalKnownHostsFile=none -o ");
        command.push_str(&shell_quote(&format!("UserKnownHostsFile=\"{path}\"")));
    }
    if let Some(path) = local_path("LIT_GIT_SSH_IDENTITY")? {
        command.push_str(" -oIdentitiesOnly=yes -i ");
        command.push_str(&shell_quote(&path.replace('%', "%%")));
    }
    Ok(command)
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

pub(super) fn clone_into(
    root: &Path,
    endpoint: &GitRemote<'_>,
) -> Result<(Vec<String>, usize), LitError> {
    let temp = tempfile::tempdir().map_err(|e| e.to_string())?;
    let mirror = temp.path().join("mirror.git");
    let source = mirror.to_str().ok_or("Temporary Git path is not UTF-8")?;
    transport_output(
        git_transport(temp.path(), endpoint)?.args([
            "clone",
            "--mirror",
            "--no-local",
            "--",
            endpoint.url.as_ref(),
            source,
        ]),
        "clone",
    )?;
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
    endpoint: &GitRemote<'_>,
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
        git_transport(stage, endpoint)?.args([
            "fetch",
            "--no-tags",
            "--",
            endpoint.url.as_ref(),
            &refspec,
        ]),
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
    endpoint: &GitRemote<'_>,
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
    let result = transport_output(
        git_transport(stage, endpoint)?.args([
            "push",
            "--porcelain",
            "--",
            endpoint.url.as_ref(),
            &refspec,
        ]),
        "push",
    )?;
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
