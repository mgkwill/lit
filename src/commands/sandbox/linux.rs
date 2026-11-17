use crate::errors::LitError;
use std::collections::HashMap;
use std::io::{self, Seek, Write};
use std::os::fd::AsRawFd;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, Output};

const WORKSPACE: &str = "/workspace";
const ERRNO: u32 = libc::SECCOMP_RET_ERRNO | libc::EPERM as u32;

pub(super) fn execute_strict(
    root: &Path,
    cmd: &[String],
    env: &HashMap<String, String>,
) -> Result<Output, LitError> {
    if cmd[0].starts_with('-') {
        return Err("Specify a path for a command whose name starts with '-'".into());
    }
    let launcher = ["/usr/bin/bwrap", "/bin/bwrap"]
        .into_iter()
        .find(|path| {
            std::fs::metadata(path).is_ok_and(|m| {
                m.is_file() && m.uid() == 0 && m.mode() & 0o6022 == 0 && m.mode() & 0o111 != 0
            })
        })
        .ok_or_else(|| unavailable("Linux strict mode requires a trusted, non-setuid Bubblewrap (bwrap) in /usr/bin or /bin"))?;

    let mut filter = tempfile::tempfile().map_err(setup_error)?;
    for instruction in network_filter() {
        filter.write_all(&instruction).map_err(setup_error)?;
    }
    filter.rewind().map_err(setup_error)?;
    let fd = filter.as_raw_fd();

    let mut command = Command::new(launcher);
    command.args([
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-net",
        "--disable-userns",
        "--cap-drop",
        "ALL",
        "--new-session",
        "--die-with-parent",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
    ]);
    for path in [
        "/usr/bin",
        "/usr/lib",
        "/usr/libexec",
        "/usr/lib64",
        "/bin",
        "/lib",
        "/lib64",
        "/etc/ld.so.cache",
    ] {
        command.args(["--ro-bind-try", path, path]);
    }
    command
        .arg("--bind")
        .arg(root)
        .arg(WORKSPACE)
        .args(["--chdir", WORKSPACE, "--seccomp"])
        .arg(fd.to_string())
        .args([
            "--",
            "/bin/sh",
            "-c",
            super::STRICT_BOOTSTRAP,
            "lit-sandbox",
        ])
        .args(cmd)
        .env_clear()
        .envs(env)
        .env("HOME", WORKSPACE)
        .env("TMPDIR", "/workspace/tmp")
        .env("TEMP", "/workspace/tmp")
        .env("TMP", "/workspace/tmp");

    // SAFETY: Only async-signal-safe syscalls run between fork and exec.
    // Keep the exec-error pipe until exec and pass only bwrap's filter FD.
    unsafe {
        command.pre_exec(move || {
            if libc::syscall(
                libc::SYS_close_range,
                3_u32,
                u32::MAX,
                libc::CLOSE_RANGE_CLOEXEC,
            ) == -1
                || libc::fcntl(fd, libc::F_SETFD, 0) == -1
            {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let output = command.output().map_err(setup_error)?;
    super::strict_output(output, "Linux Bubblewrap")
}

pub(super) fn reject_workspace_mounts(root: &Path) -> Result<(), LitError> {
    let mountinfo = std::fs::read("/proc/self/mountinfo").map_err(setup_error)?;
    for line in mountinfo
        .split(|byte| *byte == b'\n')
        .filter(|line| !line.is_empty())
    {
        let raw = line
            .split(|byte| *byte == b' ')
            .nth(4)
            .ok_or_else(|| unavailable("Could not read Linux mount points"))?;
        let mut decoded = Vec::new();
        let mut bytes = raw.iter();
        while let Some(&byte) = bytes.next() {
            if byte == b'\\' {
                let escaped = [bytes.next(), bytes.next(), bytes.next()];
                let value = match escaped {
                    [Some(b'0'), Some(b'4'), Some(b'0')] => b' ',
                    [Some(b'0'), Some(b'1'), Some(b'1')] => b'\t',
                    [Some(b'0'), Some(b'1'), Some(b'2')] => b'\n',
                    [Some(b'1'), Some(b'3'), Some(b'4')] => b'\\',
                    _ => return Err(unavailable("Could not decode a Linux mount point")),
                };
                decoded.push(value);
            } else {
                decoded.push(byte);
            }
        }
        if Path::new(std::ffi::OsStr::from_bytes(&decoded)).starts_with(root) {
            return Err(unavailable(
                "Strict sandbox workspace contains a host mount",
            ));
        }
    }
    Ok(())
}

fn network_filter() -> Vec<[u8; 8]> {
    // seccomp_data begins with syscall number and audit architecture.
    #[cfg(target_arch = "aarch64")]
    const ARCH: u32 = 0xc000_00b7;
    #[cfg(target_arch = "x86_64")]
    const ARCH: u32 = 0xc000_003e;
    let mut filter = vec![
        instruction(libc::BPF_LD | libc::BPF_W | libc::BPF_ABS, 0, 0, 4),
        instruction(libc::BPF_JMP | libc::BPF_JEQ | libc::BPF_K, 1, 0, ARCH),
        instruction(
            libc::BPF_RET | libc::BPF_K,
            0,
            0,
            libc::SECCOMP_RET_KILL_PROCESS,
        ),
        instruction(libc::BPF_LD | libc::BPF_W | libc::BPF_ABS, 0, 0, 0),
    ];
    #[cfg(target_arch = "x86_64")]
    {
        // x32 shares the x86-64 audit architecture but remaps syscall numbers.
        filter.push(instruction(
            libc::BPF_JMP | libc::BPF_JSET | libc::BPF_K,
            0,
            1,
            0x4000_0000,
        ));
        filter.push(instruction(libc::BPF_RET | libc::BPF_K, 0, 0, ERRNO));
    }
    // Network namespaces alone leave some socket families available.
    // io_uring can perform socket operations without a socket syscall.
    for nr in [
        libc::SYS_socket,
        libc::SYS_socketpair,
        libc::SYS_io_uring_setup,
    ] {
        filter.push(instruction(
            libc::BPF_JMP | libc::BPF_JEQ | libc::BPF_K,
            0,
            1,
            nr as u32,
        ));
        filter.push(instruction(libc::BPF_RET | libc::BPF_K, 0, 0, ERRNO));
    }
    filter.push(instruction(
        libc::BPF_RET | libc::BPF_K,
        0,
        0,
        libc::SECCOMP_RET_ALLOW,
    ));
    filter
}

fn instruction(code: u32, jt: u8, jf: u8, value: u32) -> [u8; 8] {
    let [c0, c1] = (code as u16).to_ne_bytes();
    let [v0, v1, v2, v3] = value.to_ne_bytes();
    [c0, c1, jt, jf, v0, v1, v2, v3]
}

fn unavailable(message: &str) -> LitError {
    LitError::SandboxUnavailable(message.into())
}

fn setup_error(error: io::Error) -> LitError {
    unavailable(&format!(
        "Cannot initialize Linux Bubblewrap isolation: {error}"
    ))
}
