use crate::errors::LitError;
use crate::response::SandboxResponse;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use walkdir::WalkDir;

#[cfg(all(
    target_os = "linux",
    any(target_arch = "aarch64", target_arch = "x86_64")
))]
mod linux;
#[cfg(all(
    target_os = "linux",
    any(target_arch = "aarch64", target_arch = "x86_64")
))]
use linux::execute_strict;

#[cfg(any(
    target_os = "macos",
    all(
        target_os = "linux",
        any(target_arch = "aarch64", target_arch = "x86_64")
    )
))]
const STRICT_READY: &[u8] = b"__lit_sandbox_ready__\n";
#[cfg(any(
    target_os = "macos",
    all(
        target_os = "linux",
        any(target_arch = "aarch64", target_arch = "x86_64")
    )
))]
const STRICT_BOOTSTRAP: &str = r#"case "$1" in -*) exit 125;; esac; printf '%s
' '__lit_sandbox_ready__' || exit 125; exec "$@""#;

/// Metadata file placed inside every sandbox root.
const SANDBOX_META: &str = ".sandbox.toml";

/// SECURITY: Validate sandbox name to prevent path traversal.
/// Only allows alphanumeric characters, hyphens, underscores, and dots.
/// Rejects empty names, names starting with a dot, and any path separators.
fn validate_sandbox_name(name: &str) -> Result<(), LitError> {
    if name.is_empty() || name.len() > 128 {
        return Err(LitError::general(
            "sandbox name must be 1-128 characters".to_string(),
        ));
    }
    if name.starts_with('.') {
        return Err(LitError::general(
            "sandbox name must not start with '.'".to_string(),
        ));
    }
    if !name
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
    {
        return Err(LitError::general(
            "sandbox name contains invalid characters (allowed: a-z, A-Z, 0-9, -, _, .)"
                .to_string(),
        ));
    }
    Ok(())
}

/// Default sandbox base directory under the repo's `.lit/` folder.
fn sandbox_base(repo_root: &Path) -> PathBuf {
    repo_root.join(".lit").join("sandboxes")
}

/// Resolve the sandbox root for a given name.
fn sandbox_dir(repo_root: &Path, name: &str) -> PathBuf {
    sandbox_base(repo_root).join(name)
}

// ── public entry points ────────────────────────────────────────────

/// Create a new sandbox from the current repo working tree.
pub fn execute_init(name: Option<String>) -> Result<SandboxResponse, LitError> {
    let repo_root = crate::core::find_repo_root()?;

    // Use CWD as the source tree when it is inside repo_root.
    // This avoids copying the entire home directory when ~/.lit exists.
    let cwd =
        std::env::current_dir().map_err(|e| LitError::io(format!("cannot determine cwd: {e}")))?;
    let source = if cwd.starts_with(&repo_root) && cwd != repo_root {
        cwd.clone()
    } else {
        repo_root.clone()
    };

    let name =
        name.unwrap_or_else(|| format!("sandbox-{}", chrono::Utc::now().format("%Y%m%d-%H%M%S")));
    validate_sandbox_name(&name)?;

    let sb_dir = sandbox_dir(&repo_root, &name);
    if sb_dir.exists() {
        return Err(LitError::general(format!(
            "sandbox '{}' already exists at {}",
            name,
            sb_dir.display()
        )));
    }
    fs::create_dir_all(&sb_dir)
        .map_err(|e| LitError::io(format!("failed to create sandbox dir: {e}")))?;

    // Copy the source tree (skip .lit/ and sandbox dir itself)
    copy_tree(&source, &sb_dir, &repo_root)?;

    // Write sandbox metadata
    let meta = format!(
        "# Lit sandbox metadata\ncreated = \"{}\"\nsource = \"{}\"\nname = \"{}\"\n",
        chrono::Utc::now().to_rfc3339(),
        source.display(),
        name,
    );
    fs::write(sb_dir.join(SANDBOX_META), &meta)
        .map_err(|e| LitError::io(format!("failed to write sandbox metadata: {e}")))?;

    Ok(SandboxResponse {
        action: "init".into(),
        name: name.clone(),
        path: sb_dir.display().to_string(),
        message: format!("sandbox '{}' created", name),
        output: None,
        exit_code: None,
        controls_enforced: Vec::new(),
        controls_unenforced: Vec::new(),
    })
}

/// Run a command inside an existing sandbox with restricted environment.
pub fn execute_run(name: String, cmd: Vec<String>) -> Result<SandboxResponse, LitError> {
    execute_run_with_options(name, cmd, false)
}

pub fn execute_run_with_options(
    name: String,
    cmd: Vec<String>,
    strict: bool,
) -> Result<SandboxResponse, LitError> {
    let repo_root = crate::core::find_repo_root()?;
    validate_sandbox_name(&name)?;
    let sb_dir = sandbox_dir(&repo_root, &name);

    if !sb_dir.join(SANDBOX_META).exists() {
        return Err(LitError::general(format!(
            "sandbox '{}' not found (expected at {})",
            name,
            sb_dir.display()
        )));
    }

    if cmd.is_empty() {
        return Err(LitError::general(String::from(
            "no command specified - use: lit sandbox run <name> -- <command> [args...]",
        )));
    }

    let sb_dir = if strict {
        strict_root(&repo_root, &sb_dir)?
    } else {
        sb_dir
    };
    let env = sandboxed_env(&sb_dir)?;
    let result = if strict {
        execute_strict(&sb_dir, &cmd, &env)?
    } else {
        Command::new(&cmd[0])
            .args(&cmd[1..])
            .current_dir(&sb_dir)
            .env_clear()
            .envs(&env)
            .output()
            .map_err(|e| LitError::io(format!("failed to spawn command: {e}")))?
    };

    let stdout = String::from_utf8_lossy(&result.stdout).to_string();
    let stderr = String::from_utf8_lossy(&result.stderr).to_string();
    let combined = if stderr.is_empty() {
        stdout
    } else {
        format!("{stdout}\n{stderr}")
    };

    let code = result.status.code().unwrap_or(-1);

    Ok(SandboxResponse {
        action: "run".into(),
        name,
        path: sb_dir.display().to_string(),
        message: if result.status.success() {
            "command completed successfully".into()
        } else {
            format!("command exited with code {code}")
        },
        output: Some(combined),
        exit_code: Some(code),
        controls_enforced: if strict {
            vec![
                "environment-scrub".into(),
                "filesystem-isolation".into(),
                "network-deny".into(),
            ]
        } else {
            vec!["environment-scrub".into()]
        },
        controls_unenforced: if strict {
            Vec::new()
        } else {
            vec!["filesystem-isolation".into(), "network-isolation".into()]
        },
    })
}

/// List all sandboxes in the current repo.
pub fn execute_list() -> Result<SandboxResponse, LitError> {
    let repo_root = crate::core::find_repo_root()?;
    let base = sandbox_base(&repo_root);

    let mut entries = Vec::new();
    if base.exists() {
        for entry in fs::read_dir(&base)
            .map_err(|e| LitError::io(format!("failed to read sandbox dir: {e}")))?
        {
            let entry = entry.map_err(|e| LitError::io(format!("failed to read entry: {e}")))?;
            if entry.path().join(SANDBOX_META).exists() {
                entries.push(entry.file_name().to_string_lossy().to_string());
            }
        }
    }
    entries.sort();

    let message = if entries.is_empty() {
        "no sandboxes".into()
    } else {
        entries.join("\n")
    };

    Ok(SandboxResponse {
        action: "list".into(),
        name: String::new(),
        path: base.display().to_string(),
        message,
        output: None,
        exit_code: None,
        controls_enforced: Vec::new(),
        controls_unenforced: Vec::new(),
    })
}

/// Destroy a sandbox.
pub fn execute_destroy(name: String) -> Result<SandboxResponse, LitError> {
    let repo_root = crate::core::find_repo_root()?;
    validate_sandbox_name(&name)?;
    let sb_dir = sandbox_dir(&repo_root, &name);

    if !sb_dir.join(SANDBOX_META).exists() {
        return Err(LitError::general(format!(
            "sandbox '{}' not found (expected at {})",
            name,
            sb_dir.display()
        )));
    }

    fs::remove_dir_all(&sb_dir)
        .map_err(|e| LitError::io(format!("failed to remove sandbox: {e}")))?;

    Ok(SandboxResponse {
        action: "destroy".into(),
        name: name.clone(),
        path: sb_dir.display().to_string(),
        message: format!("sandbox '{}' destroyed", name),
        output: None,
        exit_code: None,
        controls_enforced: Vec::new(),
        controls_unenforced: Vec::new(),
    })
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn strict_root(repo_root: &Path, sandbox: &Path) -> Result<PathBuf, LitError> {
    use std::os::unix::fs::MetadataExt;

    let repo = repo_root.canonicalize().map_err(|e| {
        LitError::SandboxUnavailable(format!("Cannot resolve repository path: {e}"))
    })?;
    let sandbox_name = sandbox.file_name().expect("validated sandbox name");
    let base = sandbox_base(&repo);
    let root = base.join(sandbox_name);
    for path in [repo.join(".lit"), base, root.clone()] {
        if !path
            .symlink_metadata()
            .is_ok_and(|metadata| metadata.is_dir())
        {
            return Err(LitError::SandboxUnavailable(
                "Strict sandbox roots must be real directories inside the repository".into(),
            ));
        }
    }
    #[cfg(all(
        target_os = "linux",
        any(target_arch = "aarch64", target_arch = "x86_64")
    ))]
    linux::reject_workspace_mounts(&root)?;
    for entry in WalkDir::new(&root) {
        let entry = entry.map_err(|e| {
            LitError::SandboxUnavailable(format!("Inspect sandbox filesystem: {e}"))
        })?;
        if !(entry.file_type().is_file()
            || entry.file_type().is_dir()
            || entry.file_type().is_symlink())
        {
            return Err(LitError::SandboxUnavailable(
                "Strict sandbox contains a special file".into(),
            ));
        }
        if entry.file_type().is_file()
            && entry
                .metadata()
                .map_err(|e| LitError::SandboxUnavailable(format!("Inspect sandbox file: {e}")))?
                .nlink()
                != 1
        {
            return Err(LitError::SandboxUnavailable(
                "Strict sandbox contains a hard-linked file. Recreate the sandbox before running."
                    .into(),
            ));
        }
    }
    Ok(root)
}

#[cfg(not(any(target_os = "linux", target_os = "macos")))]
fn strict_root(_repo_root: &Path, _sandbox: &Path) -> Result<PathBuf, LitError> {
    Err(LitError::SandboxUnavailable(
        "Strict filesystem and network isolation is unavailable on this platform".into(),
    ))
}

#[cfg(target_os = "macos")]
fn execute_strict(
    root: &Path,
    cmd: &[String],
    env: &HashMap<String, String>,
) -> Result<std::process::Output, LitError> {
    use std::ffi::OsString;

    // The system launcher installs this profile before executing the trusted
    // bootstrap. No unconfined command path is available from strict mode.
    const PROFILE: &str = r#"
(version 1)
(deny default)
(deny network*)
(allow process-fork)
(allow process-exec file-map-executable
    (subpath (param "LIT_ROOT"))
    (subpath "/bin")
    (subpath "/usr/bin")
    (subpath "/usr/lib")
    (subpath "/System/Library")
    (subpath "/System/Cryptexes/OS/usr/lib"))
(allow sysctl-read)
(allow file-read-data (literal "/"))
(allow file-read*
    (subpath (param "LIT_ROOT"))
    (subpath "/bin")
    (subpath "/usr/bin")
    (subpath "/usr/lib")
    (subpath "/System/Library")
    (subpath "/System/Cryptexes/OS/usr/lib")
    (literal "/dev/null")
    (literal "/dev/urandom"))
(allow file-write* (subpath (param "LIT_ROOT")) (literal "/dev/null"))
"#;
    let mut parameter = OsString::from("LIT_ROOT=");
    parameter.push(root);

    let mut command = Command::new("/usr/bin/sandbox-exec");
    command
        .args(["-p", PROFILE, "-D"])
        .arg(parameter)
        .args([
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            STRICT_BOOTSTRAP,
            "lit-sandbox",
        ])
        .args(cmd)
        .current_dir(root)
        .env_clear()
        .envs(env);
    close_inherited_descriptors(&mut command)?;
    let result = command
        .output()
        .map_err(|e| LitError::SandboxUnavailable(format!("Cannot start macOS Seatbelt: {e}")))?;
    strict_output(result, "macOS Seatbelt")
}

#[cfg(any(
    target_os = "macos",
    all(
        target_os = "linux",
        any(target_arch = "aarch64", target_arch = "x86_64")
    )
))]
fn strict_output(
    mut result: std::process::Output,
    backend: &str,
) -> Result<std::process::Output, LitError> {
    if !result.stdout.starts_with(STRICT_READY) {
        let detail = String::from_utf8_lossy(&result.stderr);
        return Err(LitError::SandboxUnavailable(format!(
            "{backend} did not start the confined command (launcher exit: {}): {}",
            result.status,
            detail.trim()
        )));
    }
    result.stdout.drain(..STRICT_READY.len());
    Ok(result)
}

#[cfg(target_os = "macos")]
fn close_inherited_descriptors(command: &mut Command) -> Result<(), LitError> {
    use std::os::unix::process::CommandExt;

    // The kernel limit also covers descriptors inherited above the caller's
    // current soft limit. Seatbelt can permit I/O through already-open handles.
    let mut limit: libc::c_int = 0;
    let mut size = std::mem::size_of_val(&limit);
    // SAFETY: sysctl writes at most size bytes into the live integer.
    let result = unsafe {
        libc::sysctlbyname(
            c"kern.maxfilesperproc".as_ptr(),
            (&mut limit as *mut libc::c_int).cast(),
            &mut size,
            std::ptr::null_mut(),
            0,
        )
    };
    if result != 0 || size != std::mem::size_of_val(&limit) || limit < 3 {
        return Err(LitError::SandboxUnavailable(
            "Cannot determine the macOS descriptor limit for a strict sandbox".into(),
        ));
    }
    // SAFETY: Only async-signal-safe fcntl calls and errno checks run after
    // fork. Marking descriptors close-on-exec preserves std's exec-error pipe
    // until the trusted launcher has actually been executed.
    unsafe {
        command.pre_exec(move || {
            for fd in 3..limit {
                if libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) == -1 {
                    let error = std::io::Error::last_os_error();
                    if error.raw_os_error() != Some(libc::EBADF) {
                        return Err(error);
                    }
                }
            }
            Ok(())
        });
    }
    Ok(())
}

#[cfg(not(any(
    target_os = "macos",
    all(
        target_os = "linux",
        any(target_arch = "aarch64", target_arch = "x86_64")
    )
)))]
fn execute_strict(
    _root: &Path,
    _cmd: &[String],
    _env: &HashMap<String, String>,
) -> Result<std::process::Output, LitError> {
    Err(LitError::SandboxUnavailable(
        "Strict filesystem and network isolation is unavailable on this platform".into(),
    ))
}

// ── helpers ────────────────────────────────────────────────────────

/// Copy the working tree from `src` to `dst`, skipping `.lit/` and hidden VCS dirs.
fn copy_tree(src: &Path, dst: &Path, repo_root: &Path) -> Result<(), LitError> {
    let skip_dirs: std::collections::HashSet<&str> =
        [".lit", ".git", ".hg", "node_modules", "target"]
            .iter()
            .copied()
            .collect();

    for entry in WalkDir::new(src).into_iter().filter_entry(|e| {
        let name = e.file_name().to_string_lossy();
        // skip sandbox base itself
        if e.path() == sandbox_base(repo_root) {
            return false;
        }
        if e.file_type().is_dir() && skip_dirs.contains(name.as_ref()) {
            return false;
        }
        true
    }) {
        let entry = match entry {
            Ok(e) => e,
            Err(_) => continue,
        };

        let rel = entry
            .path()
            .strip_prefix(src)
            .map_err(|e| LitError::io(format!("path strip error: {e}")))?;

        if rel.as_os_str().is_empty() {
            continue;
        }

        let target = dst.join(rel);

        if entry.file_type().is_dir() {
            fs::create_dir_all(&target)
                .map_err(|e| LitError::io(format!("mkdir {}: {e}", target.display())))?;
        } else if entry.file_type().is_file() {
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)
                    .map_err(|e| LitError::io(format!("mkdir {}: {e}", parent.display())))?;
            }
            fs::copy(entry.path(), &target).map_err(|e| {
                LitError::io(format!(
                    "copy {} -> {}: {e}",
                    entry.path().display(),
                    target.display()
                ))
            })?;
        }
    }
    Ok(())
}

/// Build a minimal environment map for the sandboxed process.
///
/// Only essential system paths and the sandbox HOME are exposed.
/// Secrets, cloud tokens, user shell config, etc. are stripped.
fn sandboxed_env(sandbox_root: &Path) -> Result<HashMap<String, String>, LitError> {
    let mut env = HashMap::new();

    // Minimal PATH — only system directories
    #[cfg(windows)]
    {
        let sys_root = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into());
        env.insert("PATH".into(), format!(r"{sys_root}\System32;{sys_root}"));
        env.insert("SystemRoot".into(), sys_root.clone());
        env.insert("SYSTEMDRIVE".into(), "C:".into());
        env.insert("COMSPEC".into(), format!(r"{sys_root}\System32\cmd.exe"));
    }
    #[cfg(not(windows))]
    {
        env.insert("PATH".into(), "/usr/bin:/bin".into());
    }

    // Redirect home-based configuration discovery into the copied tree.
    let sb = sandbox_root.display().to_string();
    env.insert("HOME".into(), sb.clone());
    #[cfg(windows)]
    env.insert("USERPROFILE".into(), sb.clone());

    // Disable Git's system configuration and interactive prompts.
    env.insert("GIT_CONFIG_NOSYSTEM".into(), "1".into());
    env.insert("GIT_TERMINAL_PROMPT".into(), "0".into());

    // Request JSON output and Lit's airgap policy from Lit subprocesses.
    env.insert("LIT_OUTPUT".into(), "json".into());
    env.insert("LIT_AIRGAPPED".into(), "1".into());

    // Timezone (informational)
    if let Ok(tz) = std::env::var("TZ") {
        env.insert("TZ".into(), tz);
    }

    // TEMP dirs inside sandbox
    let tmp = sandbox_root.join("tmp");
    match tmp.symlink_metadata() {
        Ok(metadata) if metadata.is_dir() => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => fs::create_dir(&tmp)
            .map_err(|e| LitError::io(format!("Cannot create sandbox temporary directory: {e}")))?,
        _ => {
            return Err(LitError::io(
                "Sandbox temporary path must be a real directory",
            ))
        }
    }
    let tmp_str = tmp.display().to_string();
    env.insert("TMPDIR".into(), tmp_str.clone());
    env.insert("TEMP".into(), tmp_str.clone());
    env.insert("TMP".into(), tmp_str);

    Ok(env)
}
