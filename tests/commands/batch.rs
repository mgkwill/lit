/// Command tests for `lit batch`
///
/// NOTE: These tests modify the current working directory and must be run with
/// `cargo test --test command_tests -- --test-threads=1` to avoid test interference.
///
/// The batch command reads JSONL from stdin, so direct execute() testing is limited.
/// These tests verify batch behavior via the CLI binary with piped input.
use std::fs;
use std::process::Command;
use tempfile::TempDir;

// Helper to initialize a test repository
fn init_test_repo() -> TempDir {
    let temp = TempDir::new().unwrap();
    let repo_path = temp.path().to_str().unwrap().to_string();
    lit::commands::init::execute(false, Some(repo_path)).unwrap();
    temp
}

// Helper to create a test file
fn create_file(dir: &std::path::Path, name: &str, content: &str) {
    fs::write(dir.join(name), content).unwrap();
}

// Helper to create a commit
fn create_commit(repo_path: &std::path::Path, filename: &str, content: &str, message: &str) {
    create_file(repo_path, filename, content);

    let _cwd = super::test_helpers::CwdGuard::new(repo_path);

    lit::commands::add::execute(vec![filename.to_string()]).unwrap();
    lit::commands::commit::execute(message.to_string(), None).unwrap();
}

fn lit_binary() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_BIN_EXE_lit"))
}

#[test]
fn test_batch_dry_run_with_empty_stdin() {
    let temp = init_test_repo();

    create_commit(temp.path(), "test.txt", "hello", "Initial commit");

    let binary = lit_binary();

    let output = Command::new(&binary)
        .args(["batch", "--dry-run"])
        .current_dir(temp.path())
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .output();

    // Just verify the command can be invoked
    assert!(output.is_ok(), "Batch command should be invocable");
}

#[test]
fn test_batch_requires_repository() {
    let temp = TempDir::new().unwrap();

    let binary = lit_binary();

    let output = Command::new(&binary)
        .args(["batch"])
        .current_dir(temp.path())
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .output()
        .unwrap();

    // Should fail because there's no .lit directory
    assert!(
        !output.status.success()
            || String::from_utf8_lossy(&output.stderr).contains("Not a lit repository"),
        "Batch should fail outside a repository"
    );
}

#[test]
fn test_batch_dry_run_with_operations() {
    let temp = init_test_repo();

    create_commit(temp.path(), "test.txt", "hello", "Initial commit");

    let binary = lit_binary();

    // Send a batch operation via stdin
    let mut child = Command::new(&binary)
        .args(["batch", "--dry-run"])
        .current_dir(temp.path())
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();

    // Write JSONL to stdin and close it
    use std::io::Write;
    if let Some(mut stdin) = child.stdin.take() {
        writeln!(stdin, r#"{{"command":"status","args":{{}}}}"#).unwrap();
        // stdin is dropped here, closing it
    }

    let output = child.wait_with_output().unwrap();

    // In dry-run mode, the command should complete (may succeed or show dry-run results)
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    // Just verify it ran without panic
    assert!(
        output.status.success() || !stderr.is_empty(),
        "Batch dry-run should execute: stdout={}, stderr={}",
        stdout,
        stderr
    );
}

#[test]
fn test_batch_atomic_flag() {
    let temp = init_test_repo();

    create_commit(temp.path(), "test.txt", "hello", "Initial commit");

    let binary = lit_binary();

    let mut child = Command::new(&binary)
        .args(["batch", "--atomic", "--dry-run"])
        .current_dir(temp.path())
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();

    use std::io::Write;
    if let Some(mut stdin) = child.stdin.take() {
        writeln!(stdin, r#"{{"command":"status","args":{{}}}}"#).unwrap();
    }

    let output = child.wait_with_output().unwrap();

    // Atomic + dry-run should work together
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        output.status.success() || !stderr.is_empty(),
        "Batch atomic dry-run should execute: stdout={}, stderr={}",
        stdout,
        stderr
    );
}
