use serde_json::{json, Value};
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Output, Stdio};
use tempfile::TempDir;

struct Fixture {
    _root: TempDir,
    repo: PathBuf,
    home: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let root = TempDir::new().unwrap();
        let repo = root.path().join("repo");
        let home = root.path().join("home");
        fs::create_dir(&repo).unwrap();
        fs::create_dir(&home).unwrap();
        let fixture = Self {
            _root: root,
            repo,
            home,
        };
        fixture.run(&["init", "."], "");
        fixture
    }

    fn command(&self, arguments: &[&str]) -> Command {
        let mut child = Command::new(env!("CARGO_BIN_EXE_lit"));
        child.args(arguments)
            .current_dir(&self.repo)
            .env("HOME", &self.home)
            .env("USERPROFILE", &self.home)
            .env("LIT_OUTPUT", "json")
            .env_remove("LIT_PRETTY")
            .env_remove("LIT_PASSPHRASE")
            .env_remove("LIT_PASSPHRASE_FILE")
            .env_remove("LIT_NEW_PASSPHRASE_FILE");
        child
    }

    fn output(&self, arguments: &[&str], input: &str) -> Output {
        let mut child = self.command(arguments)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        child
            .stdin
            .take()
            .unwrap()
            .write_all(input.as_bytes())
            .unwrap();
        child.wait_with_output().unwrap()
    }

    fn run(&self, arguments: &[&str], input: &str) -> Value {
        let output = self.output(arguments, input);
        assert!(
            output.status.success(),
            "{arguments:?}: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        serde_json::from_slice(&output.stdout).unwrap()
    }

    fn seed(&self) -> Value {
        fs::write(self.repo.join("a"), "base").unwrap();
        self.run(&["add", "a"], "");
        self.run(&["commit", "-m", "base"], "")["hash"].clone()
    }
}

#[test]
fn status_compares_head_index_and_working_tree() {
    let f = Fixture::new();
    assert_eq!(f.run(&["status"], "")["clean"], true);
    let base = f.seed();
    let clean = f.run(&["status"], "");
    assert_eq!(clean["head"], base);
    assert_eq!(clean["staged"], json!([]));
    assert_eq!(clean["clean"], true);
    assert_eq!(clean["scan_complete"], true);

    fs::write(f.repo.join("a"), "staged").unwrap();
    f.run(&["add", "a"], "");
    fs::remove_file(f.repo.join("a")).unwrap();
    let dirty = f.run(&["status"], "");
    assert_eq!(dirty["staged"], json!(["a"]));
    assert_eq!(dirty["modified"], json!(["a"]));
    assert_eq!(dirty["untracked"], json!([]));

    let mut index = lit::storage::Index::load(&f.repo).unwrap();
    index.remove("a");
    index.save(&f.repo).unwrap();
    assert_eq!(f.run(&["status"], "")["staged"], json!(["a"]));
}

#[cfg(unix)]
#[test]
fn status_and_checkout_preserve_nested_executable_modes() {
    use std::os::unix::fs::PermissionsExt;
    let f = Fixture::new();
    fs::create_dir_all(f.repo.join("deep/bin")).unwrap();
    let script = f.repo.join("deep/bin/script");
    fs::write(&script, "#!/bin/sh\n").unwrap();
    fs::set_permissions(&script, fs::Permissions::from_mode(0o644)).unwrap();
    f.run(&["add", "deep"], "");
    f.run(&["commit", "-m", "regular"], "");
    fs::set_permissions(&script, fs::Permissions::from_mode(0o755)).unwrap();
    assert_eq!(
        f.run(&["status"], "")["modified"],
        json!(["deep/bin/script"])
    );
    f.run(&["add", "deep"], "");
    assert_eq!(f.run(&["status"], "")["staged"], json!(["deep/bin/script"]));
    f.run(&["commit", "-m", "executable"], "");
    fs::remove_file(&script).unwrap();
    f.run(&["checkout", "main"], "");
    assert_ne!(
        fs::metadata(script).unwrap().permissions().mode() & 0o100,
        0
    );
    assert_eq!(f.run(&["status"], "")["clean"], true);
}

#[test]
fn batch_checks_each_line_and_preserves_both_input_forms() {
    let f = Fixture::new();
    let original_head = f.seed();
    fs::write(f.repo.join("a"), "after").unwrap();
    let request = concat!(
        "{\"op\":\"add\",\"files\":[\"a\"]}\n",
        "\n",
        "malformed\n",
        "{\"command\":\"unknown\"}\n",
        "{\"command\":\"add\",\"args\":{\"files\":[2]}}\n",
        "{\"command\":\"commit\",\"args\":{\"message\":\"updated\"}}\n",
    );
    let result = f.run(&["batch", "--dry-run"], request);
    assert_eq!(
        (result["total"].as_u64(), result["failed"].as_u64()),
        (Some(5), Some(3))
    );
    assert_eq!(result["results"][1]["line"], 3);
    assert_eq!(
        result["results"][3]["error_code"],
        "INVALID_BATCH_OPERATION"
    );
    assert_eq!(f.run(&["status"], "")["head"], original_head);
    assert_eq!(f.run(&["status"], "")["staged"], json!([]));

    let stopped = f.run(&["batch", "--atomic"], request);
    assert_eq!(stopped["results"][4]["status"], "skipped");
    assert_eq!(f.run(&["status"], "")["staged"], json!(["a"]));
    assert_eq!(f.run(&["status"], "")["head"], original_head);
    let result = f.run(&["batch"], request);
    assert_eq!(result["failed"], 3);
    assert_eq!(result["succeeded"], 2);
    assert_ne!(f.run(&["status"], "")["head"], original_head);
    assert_eq!(f.run(&["status"], "")["clean"], true);
}

#[test]
fn transaction_restores_dirty_worktree_index_and_ref_and_can_retry() {
    let f = Fixture::new();
    let original = f.seed();
    fs::write(f.repo.join("a"), "original unstaged content").unwrap();
    let before_index = fs::read(f.repo.join(".lit/index")).unwrap();
    f.run(&["tx", "begin"], "");
    fs::write(f.repo.join("a"), "transaction content").unwrap();
    fs::write(f.repo.join("keep"), "keep this").unwrap();
    f.run(&["add", "a", "keep"], "");
    f.run(&["commit", "-m", "inside tx"], "");

    fs::remove_file(f.repo.join("a")).unwrap();
    fs::create_dir(f.repo.join("a")).unwrap();
    assert!(!f.output(&["tx", "rollback"], "").status.success());
    assert!(f.repo.join(".lit/transaction.json").exists());
    fs::remove_dir(f.repo.join("a")).unwrap();

    f.run(&["tx", "rollback"], "");
    assert_eq!(f.run(&["status"], "")["head"], original);
    assert_eq!(fs::read(f.repo.join(".lit/index")).unwrap(), before_index);
    assert_eq!(
        fs::read_to_string(f.repo.join("a")).unwrap(),
        "original unstaged content"
    );
    assert_eq!(
        fs::read_to_string(f.repo.join("keep")).unwrap(),
        "keep this"
    );
    assert_eq!(f.run(&["status"], "")["untracked"], json!(["keep"]));
    assert!(!f.repo.join(".lit/transaction.json").exists());
}

#[test]
fn swarm_registration_stays_inside_the_repository_and_is_idempotent() {
    let f = Fixture::new();
    let outside = f._root.path().join("outside");
    for name in [outside.to_str().unwrap(), "../../../../outside", r"..\outside"] {
        assert!(!f.output(&["swarm", "register", name], "").status.success());
        assert!(!outside.exists());
    }

    f.run(&["swarm", "register", "worker-1"], "");
    let record = f.repo.join(".lit/swarm/agents/worker-1.json");
    let mut data: Value = serde_json::from_slice(&fs::read(&record).unwrap()).unwrap();
    data["branches"] = json!(["existing"]);
    let saved = serde_json::to_vec(&data).unwrap();
    fs::write(&record, &saved).unwrap();
    f.run(&["swarm", "register", "worker-1"], "");
    assert_eq!(fs::read(record).unwrap(), saved);
}

#[cfg(unix)]
#[test]
fn swarm_registration_rejects_redirected_namespace() {
    let f = Fixture::new();
    let outside = f._root.path().join("outside");
    fs::create_dir(&outside).unwrap();
    std::os::unix::fs::symlink(&outside, f.repo.join(".lit/refs/agents")).unwrap();
    assert!(!f.output(&["swarm", "register", "worker-1"], "").status.success());
    assert!(!outside.join("worker-1").exists());
}

#[test]
fn checkout_cannot_invalidate_an_open_transaction() {
    let f = Fixture::new();
    let original = f.seed();
    f.run(&["tx", "begin"], "");
    fs::write(f.repo.join("a"), "keep my edit").unwrap();

    assert!(!f.output(&["checkout", "-b", "other"], "").status.success());
    assert!(!f.repo.join(".lit/refs/heads/other").exists());
    assert_eq!(fs::read_to_string(f.repo.join("a")).unwrap(), "keep my edit");
    f.run(&["tx", "rollback"], "");
    assert_eq!(f.run(&["status"], "")["head"], original);
}

#[test]
fn transaction_restores_unborn_branch_after_first_commit() {
    let f = Fixture::new();
    f.run(&["tx", "begin"], "");
    f.seed();
    f.run(&["tx", "rollback"], "");
    let status = f.run(&["status"], "");
    assert_eq!(status["head"], Value::Null);
    assert_eq!(status["untracked"], json!(["a"]));
}

#[test]
fn mcp_stdio_emits_only_json_rpc_responses() {
    let f = Fixture::new();
    fs::write(f.repo.join("a"), "fixture").unwrap();
    let input = concat!(
        "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"test\",\"version\":\"1\"}}}\n",
        "{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}\n",
        "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\"}\n",
        "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"lit_add\",\"arguments\":{\"files\":[\"a\"]}}}\n",
        "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"tools/call\",\"params\":{\"name\":\"unknown\"}}\n",
        "not json\n",
        "{\"jsonrpc\":\"2.0\",\"id\":true,\"method\":\"ping\"}\n",
        "{\"jsonrpc\":\"2.0\",\"id\":\"still-alive\",\"method\":\"ping\"}\n",
    );
    let output = f.output(&["mcp-serve", "--stdio"], input);
    assert!(output.status.success(), "{:?}", output.stderr);
    let stdout = String::from_utf8(output.stdout).unwrap();
    let responses: Vec<Value> = stdout
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(responses.len(), 7, "{stdout}");
    assert!(responses.iter().all(|message| message["jsonrpc"] == "2.0"));
    assert_eq!(responses[0]["result"]["protocolVersion"], "2024-11-05");
    assert_eq!(
        responses[1]["result"]["tools"].as_array().unwrap().len(),
        30
    );
    let added: Value = serde_json::from_str(
        responses[2]["result"]["content"][0]["text"]
            .as_str()
            .unwrap(),
    )
    .unwrap();
    assert_eq!(added["files_added"], 1);
    assert_eq!(responses[3]["result"]["isError"], true);
    assert_eq!(responses[4]["error"]["code"], -32700);
    assert_eq!(responses[5]["error"]["code"], -32600);
    assert_eq!(responses[4].get("id"), Some(&Value::Null));
    assert_eq!(responses[6]["id"], "still-alive");
    assert_eq!(f.run(&["status"], "")["staged"], json!(["a"]));
}

#[test]
fn merge_rejects_head_changes_and_resumes_the_same_two_parent_commit() {
    use lit::core::{Object, ObjectHash};
    use lit::storage::ObjectStore;

    for (strategy, chosen) in [("ours", "main"), ("theirs", "topic")] {
        let f = Fixture::new();
        f.seed();
        f.run(&["branch", "topic"], "");
        f.run(&["checkout", "topic"], "");
        fs::write(f.repo.join("a"), "topic").unwrap();
        f.run(&["add", "a"], "");
        let topic = f.run(&["commit", "-m", "topic"], "")["hash"].as_str().unwrap().to_owned();
        f.run(&["checkout", "main"], "");
        fs::write(f.repo.join("a"), "main").unwrap();
        f.run(&["add", "a"], "");
        let main = f.run(&["commit", "-m", "main"], "")["hash"].as_str().unwrap().to_owned();
        assert_eq!(f.run(&["merge", "topic"], "")["has_conflicts"], true);

        let merge = f.repo.join(".lit/merge");
        let branch = f.repo.join(".lit/refs/heads/main");
        let index = f.repo.join(".lit/index");
        let index_before = fs::read(&index).unwrap();
        let conflicted = fs::read(merge.join("conflicts.json")).unwrap();
        for command in [
            &["commit", "-m", "must refuse"][..],
            &["snapshot", "-m", "must refuse"],
            &["checkout", "topic"],
            &["merge", "topic"],
            &["resolve", "--continue"],
        ] {
            assert!(!f.output(command, "").status.success(), "{command:?}");
            assert_eq!(fs::read_to_string(&branch).unwrap().trim(), main);
            assert_eq!(fs::read(&index).unwrap(), index_before);
            assert_eq!(fs::read(merge.join("conflicts.json")).unwrap(), conflicted);
            assert_eq!(fs::read_to_string(f.repo.join("a")).unwrap(), "main");
        }

        fs::write(merge.join("conflicts.json"), b"broken").unwrap();
        assert!(!f.output(&["resolve", "--continue"], "").status.success());
        assert_eq!(f.run(&["verify"], "")["valid"], false);
        assert_eq!(fs::read_to_string(&branch).unwrap().trim(), main);
        fs::write(merge.join("conflicts.json"), conflicted).unwrap();
        assert_eq!(f.run(&["verify"], "")["valid"], true);
        let resolved = f.run(&["resolve", "a", "--strategy", strategy], "");
        assert_eq!(resolved["remaining_conflicts"], 0);
        assert_eq!(fs::read_to_string(f.repo.join("a")).unwrap(), chosen);
        assert_eq!(f.run(&["resolve", "--continue"], "")["merge_complete"], true);

        let finished = f.run(&["status"], "");
        assert_eq!(finished["clean"], true);
        let result = finished["head"].as_str().unwrap();
        let store = ObjectStore::new(&f.repo);
        let Object::Commit(commit) = store.read(&ObjectHash::from_hex(result.to_owned())).unwrap() else {
            panic!("expected merge commit");
        };
        assert_eq!(commit.parents.iter().map(|hash| hash.as_str()).collect::<Vec<_>>(), [&main, &topic]);
        assert!(!merge.exists());

        // Simulate interruption after the ref was published but before state was retired.
        fs::create_dir(&merge).unwrap();
        fs::write(merge.join("MERGE_HEAD"), &topic).unwrap();
        fs::write(merge.join("ORIG_HEAD"), &main).unwrap();
        fs::write(merge.join("conflicts.json"), b"[]").unwrap();
        fs::write(merge.join("RESULT"), result).unwrap();
        assert_eq!(f.run(&["resolve", "--finish"], "")["merge_complete"], true);
        assert_eq!(f.run(&["status"], "")["head"], result);
        assert!(!merge.exists());
        assert_eq!(f.run(&["verify"], "")["valid"], true);
    }
}

#[test]
fn verify_reports_bad_transaction_metadata_and_rollback_preserves_worktree() {
    let f = Fixture::new();
    f.seed();
    fs::write(f.repo.join("a"), "checkpoint bytes").unwrap();
    f.run(&["tx", "begin"], "");
    let journal = f.repo.join(".lit/transaction.json");
    let saved = fs::read(&journal).unwrap();
    let state: Value = serde_json::from_slice(&saved).unwrap();
    assert_eq!(f.run(&["verify"], "")["valid"], true);

    let mut invalid_blob = state.clone();
    invalid_blob["checkpoint"]["tracked"]["a"]["blob"] = json!("x");
    let mut invalid_index = state.clone();
    invalid_index["checkpoint"]["index"] = json!([123]);
    let mut invalid_branch = state.clone();
    invalid_branch["checkpoint"]["branch"] = json!("../escape");

    fs::write(f.repo.join("a"), "current bytes").unwrap();
    for (bytes, detail) in [
        (b"malformed".to_vec(), "transaction.json"),
        (serde_json::to_vec(&invalid_blob).unwrap(), "checkpoint blob"),
        (serde_json::to_vec(&invalid_index).unwrap(), "checkpoint index"),
        (serde_json::to_vec(&invalid_branch).unwrap(), "checkpoint branch"),
    ] {
        fs::write(&journal, &bytes).unwrap();
        let verify = f.run(&["verify"], "");
        assert_eq!(verify["valid"], false);
        assert!(verify["checks"].as_array().unwrap().iter().any(|check| {
            check["check"] == "transaction_journal"
                && check["status"] == "error"
                && check["details"].as_str().unwrap().contains(detail)
        }), "{verify}");
        assert!(!f.output(&["tx", "rollback"], "").status.success());
        assert_eq!(fs::read_to_string(f.repo.join("a")).unwrap(), "current bytes");
        assert_eq!(fs::read(&journal).unwrap(), bytes);
    }
    fs::write(&journal, &saved).unwrap();
    assert_eq!(f.run(&["verify"], "")["valid"], true);
    f.run(&["tx", "rollback"], "");
    assert_eq!(fs::read_to_string(f.repo.join("a")).unwrap(), "checkpoint bytes");
    assert!(!journal.exists());
    assert_eq!(f.run(&["verify"], "")["valid"], true);

    // A journal from the MIT baseline can still be verified and closed.
    let id = "a93b4aef-828d-4231-88a5-d6d6e1ebe6ce";
    let lock = f.repo.join(".lit/transaction.lock");
    fs::write(&journal, json!({"tx_id": id, "started_at": 0, "wal": []}).to_string()).unwrap();
    fs::write(&lock, id).unwrap();
    assert_eq!(f.run(&["verify"], "")["valid"], true);
    f.run(&["tx", "commit"], "");
    assert!(!journal.exists() && !lock.exists());
}

fn mixed_merge(f: &Fixture) -> String {
    for (path, content) in [
        ("a", "base\n"),
        ("incoming", "base\n"),
        ("removed", "restore me\n"),
        ("lines", "one\ntwo\nthree\nfour\n"),
        ("local", "tracked base\n"),
    ] {
        fs::write(f.repo.join(path), content).unwrap();
    }
    f.run(&["add", "."], "");
    f.run(&["commit", "-m", "base"], "");
    f.run(&["branch", "topic"], "");
    f.run(&["checkout", "topic"], "");
    fs::write(f.repo.join("a"), "topic\n").unwrap();
    fs::write(f.repo.join("incoming"), "incoming edit\n").unwrap();
    fs::write(f.repo.join("lines"), "one\ntwo\nthree\nFOUR\n").unwrap();
    fs::create_dir_all(f.repo.join("new/deep")).unwrap();
    fs::write(f.repo.join("new/deep/file"), "added\n").unwrap();
    f.run(&["add", "a", "incoming", "lines", "new/deep/file"], "");
    fs::remove_file(f.repo.join("removed")).unwrap();
    let mut index = lit::storage::Index::load(&f.repo).unwrap();
    index.remove("removed");
    index.save(&f.repo).unwrap();
    f.run(&["commit", "-m", "topic"], "");
    f.run(&["checkout", "main"], "");
    // Checkout leaves files that are absent on the destination branch.
    fs::remove_dir_all(f.repo.join("new")).unwrap();
    fs::write(f.repo.join("a"), "main\n").unwrap();
    fs::write(f.repo.join("lines"), "ONE\ntwo\nthree\nfour\n").unwrap();
    f.run(&["add", "a", "lines"], "");
    f.run(&["commit", "-m", "main"], "")["hash"].as_str().unwrap().to_owned()
}

#[test]
fn conflicted_merge_installs_and_commits_non_conflicting_changes() {
    let f = Fixture::new();
    let main = mixed_merge(&f);
    assert_eq!(f.run(&["merge", "topic"], "")["has_conflicts"], true);
    assert_eq!(f.run(&["status"], "")["head"], main);
    assert_eq!(f.run(&["verify"], "")["valid"], true);
    for (path, expected) in [
        ("a", "main\n"),
        ("incoming", "incoming edit\n"),
        ("lines", "ONE\ntwo\nthree\nFOUR\n"),
        ("new/deep/file", "added\n"),
    ] {
        assert_eq!(fs::read_to_string(f.repo.join(path)).unwrap(), expected);
    }
    assert!(!f.repo.join("removed").exists());
    f.run(&["resolve", "a", "--strategy", "theirs"], "");
    f.run(&["resolve", "--continue"], "");
    assert_eq!(f.run(&["status"], "")["clean"], true);
    assert_eq!(fs::read_to_string(f.repo.join("a")).unwrap(), "topic\n");
    assert_eq!(fs::read_to_string(f.repo.join("incoming")).unwrap(), "incoming edit\n");
    assert!(!f.repo.join(".lit/merge").exists());
    assert_eq!(f.run(&["verify"], "")["valid"], true);
}

#[test]
fn merge_abort_protects_local_edits_and_resumes_a_partial_restore() {
    let f = Fixture::new();
    let main = mixed_merge(&f);
    fs::write(f.repo.join("local"), "preexisting change\n").unwrap();
    f.run(&["add", "local"], "");
    assert_eq!(f.run(&["merge", "topic"], "")["has_conflicts"], true);
    f.run(&["resolve", "a", "--strategy", "theirs"], "");
    fs::write(f.repo.join("keep"), "after merge\n").unwrap();
    f.run(&["add", "keep"], "");
    fs::write(f.repo.join("incoming"), "keep my merge-time edit\n").unwrap();
    let index = fs::read(f.repo.join(".lit/index")).unwrap();
    let failure = f.output(&["merge", "--abort"], "");
    assert!(!failure.status.success());
    assert!(String::from_utf8_lossy(&failure.stderr).contains("incoming"));
    assert_eq!(fs::read(f.repo.join(".lit/index")).unwrap(), index);
    assert_eq!(fs::read_to_string(f.repo.join("a")).unwrap(), "topic\n");
    assert_eq!(
        fs::read_to_string(f.repo.join("incoming")).unwrap(),
        "keep my merge-time edit\n"
    );

    // Recreate an interrupted abort that already restored one path.
    fs::write(f.repo.join("incoming"), "base\n").unwrap();
    let journal = f.repo.join(".lit/merge/worktree.json");
    let mut state: Value = serde_json::from_slice(&fs::read(&journal).unwrap()).unwrap();
    state["aborting"] = json!(true);
    fs::write(&journal, serde_json::to_vec(&state).unwrap()).unwrap();
    assert!(!f.output(&["resolve", "--continue"], "").status.success());
    assert_eq!(f.run(&["merge", "--abort"], "")["strategy"], "abort");

    let status = f.run(&["status"], "");
    assert_eq!(status["head"], main);
    assert_eq!(status["staged"], json!(["keep", "local"]));
    assert_eq!(status["modified"], json!([]));
    assert_eq!(fs::read_to_string(f.repo.join("a")).unwrap(), "main\n");
    assert_eq!(fs::read_to_string(f.repo.join("incoming")).unwrap(), "base\n");
    assert_eq!(fs::read_to_string(f.repo.join("removed")).unwrap(), "restore me\n");
    assert_eq!(fs::read_to_string(f.repo.join("lines")).unwrap(), "ONE\ntwo\nthree\nfour\n");
    assert_eq!(fs::read_to_string(f.repo.join("keep")).unwrap(), "after merge\n");
    assert_eq!(fs::read_to_string(f.repo.join("local")).unwrap(), "preexisting change\n");
    assert!(!f.repo.join("new").exists());
    assert!(!f.repo.join(".lit/merge").exists());
    assert_eq!(f.run(&["verify"], "")["valid"], true);
}

#[test]
fn merge_abort_restores_premerge_index_and_keeps_preexisting_files() {
    let f = Fixture::new();
    let main = mixed_merge(&f);
    let path = f.repo.join("incoming");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
    }
    let previous_index = fs::read(f.repo.join(".lit/index")).unwrap();
    let untracked = f.repo.join("new/keep");
    fs::create_dir(untracked.parent().unwrap()).unwrap();
    fs::write(&untracked, "keep").unwrap();
    assert_eq!(f.run(&["merge", "topic"], "")["has_conflicts"], true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
    }
    f.run(&["resolve", "--abort"], "");
    assert_eq!(f.run(&["status"], "")["head"], main);
    assert_eq!(fs::read(f.repo.join(".lit/index")).unwrap(), previous_index);
    assert_eq!(fs::read_to_string(&path).unwrap(), "base\n");
    assert_eq!(fs::read_to_string(&untracked).unwrap(), "keep");
    assert!(!f.repo.join("new/deep").exists());
    assert!(!f.repo.join(".lit/merge").exists());
}

#[test]
fn verify_follows_nested_links_and_checks_the_bytes_under_each_object_id() {
    use lit::core::{Blob, Commit, Object, ObjectHash, Tree};
    use lit::storage::ObjectStore;

    let f = Fixture::new();
    let store = ObjectStore::new(&f.repo);
    let leaf = store.write(&Object::Blob(Blob::new(b"leaf".to_vec()))).unwrap();
    let different = store.write(&Object::Blob(Blob::new(b"different blob".to_vec()))).unwrap();
    let mut subtree = Tree::new();
    subtree.add_entry("100644".into(), "leaf".into(), leaf.clone(), "blob".into());
    let subtree = store.write(&Object::Tree(subtree)).unwrap();
    let mut tree = Tree::new();
    tree.add_entry("040000".into(), "nested".into(), subtree.clone(), "tree".into());
    tree.add_entry("160000".into(), "submodule".into(), ObjectHash::from_hex("a".repeat(40)), "gitlink".into());
    let tree = store.write(&Object::Tree(tree)).unwrap();
    let commit = store.write(&Object::Commit(Commit::new(tree.clone(), vec![], "tester".into(), "root".into()))).unwrap();
    fs::write(f.repo.join(".lit/refs/heads/main"), commit.as_str()).unwrap();

    let object_path = |hash: &ObjectHash| {
        let id = hash.as_str();
        f.repo.join(".lit/objects").join(&id[..4]).join(&id[4..])
    };
    assert_eq!(f.run(&["verify"], "")["valid"], true);
    for (hash, what) in [(&leaf, "leaf"), (&subtree, "nested"), (&tree, "Commit tree")] {
        let path = object_path(hash);
        let before = fs::read(&path).unwrap();
        fs::remove_file(&path).unwrap();
        assert_verify_error(&f, "link:", what);
        fs::write(path, before).unwrap();
        assert_eq!(f.run(&["verify"], "")["valid"], true);
    }

    let path = object_path(&leaf);
    let original = fs::read(&path).unwrap();
    // A readable, correctly encoded object stored under the wrong hash.
    fs::copy(object_path(&different), &path).unwrap();
    assert_verify_error(&f, "object:", "does not match its object ID");
    fs::write(path, original).unwrap();
    assert_eq!(f.run(&["verify"], "")["valid"], true);

    let mut index = lit::storage::Index::load(&f.repo).unwrap();
    index.add("leaf".into(), subtree.to_string(), "100644".into());
    index.save(&f.repo).unwrap();
    assert_verify_error(&f, "index", "does not reference a valid blob");
    index.clear();
    index.save(&f.repo).unwrap();
    assert_eq!(f.run(&["verify"], "")["valid"], true);
}

#[test]
fn verify_checks_typed_edges_from_detached_head_remote_refs_and_annotated_tags() {
    use lit::core::{Blob, Commit, Object, ObjectHash, Tag, Tree};
    use lit::storage::ObjectStore;

    let f = Fixture::new();
    let store = ObjectStore::new(&f.repo);
    let tree = store.write(&Object::Tree(Tree::new())).unwrap();
    let blob = store.write(&Object::Blob(Blob::new(b"not a commit".to_vec()))).unwrap();
    let make_commit = |tree, parents| store.write(&Object::Commit(
        Commit::new(tree, parents, "tester".into(), "fixture".into()),
    )).unwrap();
    let wrong_parent = make_commit(tree.clone(), vec![blob.clone()]);
    let wrong_tree = make_commit(blob.clone(), vec![]);
    let missing = ObjectHash::from_hex("a".repeat(192));
    let tagged = make_commit(tree.clone(), vec![missing.clone()]);
    let tag_object = |target| store.write(&Object::Tag(
        Tag::new(target, "commit".into(), "notice".into(), "tester".into(), "tag".into()),
    )).unwrap();
    let tag = tag_object(tagged);
    let head = f.repo.join(".lit/HEAD");
    let remote = f.repo.join(".lit/refs/remotes/origin/topic");
    let tag_ref = f.repo.join(".lit/refs/tags/notice");
    fs::create_dir(remote.parent().unwrap()).unwrap();

    fs::write(&head, wrong_parent.as_str()).unwrap();
    assert_verify_error(&f, "link:", "Commit parent");
    assert_verify_error(&f, "link:", "expected commit");
    fs::write(&head, "ref: refs/heads/main\n").unwrap();

    fs::write(&remote, wrong_tree.as_str()).unwrap();
    assert_verify_error(&f, "link:", "expected tree");
    fs::write(&remote, "⚠️ invalid ref ⚠️").unwrap();
    assert_verify_error(&f, "ref:remotes/origin/topic", "Invalid object ID");
    fs::remove_file(&remote).unwrap();

    fs::write(&tag_ref, tag.as_str()).unwrap();
    assert_verify_error(&f, "link:", "Commit parent");
    assert_verify_error(&f, "link:", missing.as_str());
    fs::write(&tag_ref, tag_object(blob).as_str()).unwrap();
    assert_verify_error(&f, "link:", "Annotated tag target");
    fs::remove_file(&tag_ref).unwrap();
    assert_eq!(f.run(&["verify"], "")["valid"], true);
}

fn assert_verify_error(f: &Fixture, category: &str, detail: &str) {
    let report = f.run(&["verify"], "");
    assert_eq!(report["valid"], false, "{report}");
    assert!(report["checks"].as_array().unwrap().iter().any(|check| {
        check["status"] == "error"
            && check["check"].as_str().unwrap().starts_with(category)
            && check["details"].as_str().unwrap().contains(detail)
    }), "{category} {detail}: {report}");
}

#[test]
fn native_gc_keeps_history_checkout_and_clone_working_across_multiple_packs() {
    use lit::core::ObjectHash;
    use lit::storage::ObjectStore;
    use std::sync::mpsc;

    let f = Fixture::new();
    let initial = f.seed();
    let initial = initial.as_str().unwrap();
    f.run(&["tag", "before-pack"], "");
    let store = ObjectStore::new(&f.repo);
    assert!(!store.exists(&ObjectHash::from_hex("0".repeat(192))));
    let initial_id = ObjectHash::from_hex(initial.to_owned());
    let bytes = store.read(&initial_id).unwrap().to_bytes();
    let (reader_ready, ready) = mpsc::channel();
    let (stop_reader, stopped) = mpsc::channel();
    let reader = std::thread::spawn(move || {
        assert_eq!(store.read(&initial_id).unwrap().to_bytes(), bytes);
        reader_ready.send(()).unwrap();
        while matches!(stopped.try_recv(), Err(mpsc::TryRecvError::Empty)) {
            assert_eq!(store.read(&initial_id).unwrap().to_bytes(), bytes);
            std::thread::yield_now();
        }
        assert!(store.exists(&initial_id));
        assert_eq!(store.read(&initial_id).unwrap().to_bytes(), bytes);
    });
    ready.recv().unwrap();
    let packed = f.run(&["gc"], "");
    stop_reader.send(()).unwrap();
    reader.join().unwrap();

    assert_eq!(packed["loose_removed"], packed["objects_packed"]);
    assert_eq!(f.run(&["status"], "")["clean"], true);
    assert_eq!(f.run(&["verify"], "")["valid"], true);
    assert_eq!(f.run(&["verify"], "")["objects_checked"], packed["objects_packed"]);
    assert_eq!(f.run(&["gc"], "")["packs_created"], 0);
    fs::write(f.repo.join("a"), "new content").unwrap();
    f.run(&["add", "a"], "");
    let tip = f.run(&["commit", "-m", "after pack"], "");
    assert_eq!(tip["parent"], initial);
    assert_eq!(f.run(&["log"], "")["commits"].as_array().unwrap().len(), 2);
    assert_eq!(f.run(&["verify"], "")["valid"], true);
    assert_eq!(f.run(&["gc"], "")["packs_created"], 1);
    assert_eq!(
        fs::read_dir(f.repo.join(".lit/packs")).unwrap()
            .filter_map(Result::ok).filter(|entry| entry.path().extension().unwrap() == "idx").count(),
        2
    );
    assert_eq!(f.run(&["gc"], "")["packs_created"], 0);
    assert_eq!(f.run(&["verify"], "")["valid"], true);
    f.run(&["checkout", initial], "");
    assert_eq!(fs::read_to_string(f.repo.join("a")).unwrap(), "base");
    f.run(&["checkout", "main"], "");
    assert_eq!(fs::read_to_string(f.repo.join("a")).unwrap(), "new content");

    let clone = f._root.path().join("clone");
    f.run(&["clone", f.repo.to_str().unwrap(), clone.to_str().unwrap()], "");
    assert_eq!(fs::read_to_string(clone.join("a")).unwrap(), "new content");
    let cloned = ObjectStore::new(&clone);
    assert!(cloned.read(&ObjectHash::from_hex(initial.to_owned())).is_ok());
}

#[test]
fn native_pack_verification_detects_corruption_even_with_a_good_loose_copy() {
    use lit::core::{Blob, Object};
    use lit::storage::ObjectStore;

    let f = Fixture::new();
    let store = ObjectStore::new(&f.repo);
    let object = Object::Blob(Blob::new(b"one packed blob".to_vec()));
    let hash = store.write(&object).unwrap();
    assert_eq!(f.run(&["gc"], "")["objects_packed"], 1);
    let index = fs::read_dir(f.repo.join(".lit/packs")).unwrap().filter_map(Result::ok)
        .map(|entry| entry.path()).find(|path| path.extension().unwrap() == "idx").unwrap();
    let pack = index.with_extension("pack");
    let original_index = fs::read(&index).unwrap();
    let original_pack = fs::read(&pack).unwrap();
    assert_eq!(f.run(&["verify"], "")["valid"], true);
    assert_eq!(store.write(&object).unwrap(), hash);
    assert_eq!(f.run(&["verify"], "")["objects_checked"], 1);

    let mut corrupt = original_index.clone();
    corrupt[7] = 3;
    fs::write(&index, corrupt).unwrap();
    assert_verify_error(&f, "pack_index", "Unsupported pack or index version");
    let mut corrupt = original_index.clone();
    corrupt[208..216].copy_from_slice(&u64::MAX.to_be_bytes());
    fs::write(&index, corrupt).unwrap();
    assert_verify_error(&f, "pack_index", "Pack entries do not start");
    let mut corrupt = original_index.clone();
    corrupt[16] = if corrupt[16] == b'a' { b'b' } else { b'a' };
    fs::write(&index, corrupt).unwrap();
    assert_verify_error(&f, "object:", "does not match its object ID");
    fs::write(&index, &original_index).unwrap();

    let mut corrupt = original_pack.clone();
    *corrupt.last_mut().unwrap() ^= 1;
    fs::write(&pack, corrupt).unwrap();
    assert_verify_error(&f, "object:", "CRC32 mismatch");
    let mut corrupt = original_pack.clone();
    corrupt[12] = 2;
    fs::write(&pack, corrupt).unwrap();
    assert_verify_error(&f, "object:", "type disagrees");
    fs::write(&pack, &original_pack[..original_pack.len() - 1]).unwrap();
    assert_verify_error(&f, "object:", "Compressed size disagrees");
    fs::write(pack, original_pack).unwrap();
    assert_eq!(f.run(&["verify"], "")["valid"], true);
}

#[test]
fn native_gc_keeps_loose_objects_when_pack_preparation_fails() {
    use lit::core::{Blob, Object};
    use lit::storage::ObjectStore;

    let f = Fixture::new();
    f.seed();
    let store = ObjectStore::new(&f.repo);
    let hash = store.write(&Object::Blob(Blob::new(b"will be corrupted".to_vec()))).unwrap();
    let id = hash.as_str();
    let path = f.repo.join(".lit/objects").join(&id[..4]).join(&id[4..]);
    let saved = fs::read(&path).unwrap();
    fs::write(&path, b"corrupt loose copy").unwrap();

    assert!(!f.output(&["gc"], "").status.success());
    let packs = f.repo.join(".lit/packs");
    assert!(!packs.exists() || fs::read_dir(packs).unwrap().next().is_none());
    assert_eq!(f.run(&["status"], "")["clean"], true);
    fs::write(path, saved).unwrap();
    assert_eq!(f.run(&["verify"], "")["valid"], true);
}

#[test]
fn encrypted_gc_verify_and_rotation_work_through_the_cli() {
    use lit::core::{Blob, Object, ObjectHash};
    use lit::crypto::encryption::{EncryptionConfig, EncryptionManager};
    use lit::storage::ObjectStore;
    use std::sync::{Arc, Mutex};

    const OLD: &str = "test-encrypted-packs-old-A1!";
    const NEW: &str = "test-encrypted-packs-new-A1!";
    const FIXTURE_HEAD: &str = "LIT_ENCRYPTED_TEST_FIXTURE_HEAD";
    if let Ok(tip) = std::env::var(FIXTURE_HEAD) {
        let repo = std::env::current_dir().unwrap();
        let store = ObjectStore::new_with_encryption(&repo, Some(OLD)).unwrap();
        let loose = store.write(&Object::Blob(Blob::new(b"loose after gc".to_vec()))).unwrap();
        let mut manager = EncryptionManager::new(EncryptionConfig::load(&repo).unwrap());
        manager.initialize(OLD).unwrap();
        let manager = Arc::new(Mutex::new(manager));
        lit::core::refs::write_ref_encrypted(&repo, "heads/main", &tip, &manager).unwrap();
        lit::core::refs::update_head_encrypted(&repo, "main", &manager).unwrap();
        let home = std::env::var_os("HOME").unwrap();
        fs::write(PathBuf::from(home).join("loose-hash"), loose.as_str()).unwrap();
        return;
    }

    let f = Fixture::new();
    let key_path = f.home.join("shared.key");
    let config = EncryptionConfig {
        enabled: true,
        key_file: key_path.to_str().unwrap().to_owned(),
        ..EncryptionConfig::default()
    };
    config.save(&f.repo).unwrap();
    let old_file = f.home.join("old-passphrase");
    let new_file = f.home.join("new-passphrase");
    fs::write(&old_file, OLD).unwrap();
    fs::write(&new_file, NEW).unwrap();
    let call = |arguments: &[&str], credential: &std::path::Path| -> Value {
        let output = f.command(arguments)
            .env("LIT_PASSPHRASE_FILE", credential)
            .env("LIT_NEW_PASSPHRASE_FILE", &new_file)
            .output().unwrap();
        assert!(output.status.success(), "{arguments:?}: {}", String::from_utf8_lossy(&output.stderr));
        serde_json::from_slice(&output.stdout).unwrap()
    };

    fs::write(f.repo.join("a"), "encrypted fixture payload").unwrap();
    call(&["add", "a"], &old_file);
    let tip = call(&["commit", "-m", "encrypted pack"], &old_file)["hash"].as_str().unwrap().to_owned();
    let original_key = fs::read(&key_path).unwrap();
    let result = call(&["gc"], &old_file);
    assert_eq!(result["loose_removed"], 3);
    let pack_index = fs::read_dir(f.repo.join(".lit/packs")).unwrap().filter_map(Result::ok)
        .map(|entry| entry.path()).find(|path| path.extension().unwrap() == "idx").unwrap();
    let pack = pack_index.with_extension("pack");
    let original_pack = fs::read(&pack).unwrap();
    let original_index = fs::read(&pack_index).unwrap();
    assert_eq!(&original_pack[..8], b"LITP\0\0\0\x02");
    assert_eq!(&original_index[..8], b"LITI\0\0\0\x02");
    assert!(!original_pack.windows(b"encrypted fixture payload".len())
        .any(|window| window == b"encrypted fixture payload"));

    let store = ObjectStore::new_with_encryption(&f.repo, Some(OLD)).unwrap();
    let hash = ObjectHash::from_hex(tip.clone());
    assert!(matches!(store.read(&hash).unwrap(), Object::Commit(_)));
    let offsets = lit::commands::gc::load_pack_index(&pack_index).unwrap();
    let offset = offsets[&tip].1 as usize;
    let len = u64::from_be_bytes(original_pack[offset + 9..offset + 17].try_into().unwrap()) as usize;
    let mut tampered = original_pack.clone();
    tampered[offset + 17 + len - 1] ^= 1;
    let mut tampered_index = original_index.clone();
    let record = tampered_index[12..].chunks_exact_mut(208)
        .find(|record| &record[4..196] == tip.as_bytes()).unwrap();
    record[204..208].copy_from_slice(&crc32fast::hash(&tampered[offset + 17..offset + 17 + len]).to_be_bytes());
    fs::write(&pack, tampered).unwrap();
    fs::write(&pack_index, tampered_index).unwrap();
    store.list().unwrap();
    assert!(store.read(&hash).unwrap_err().contains("Decryption failed"));
    fs::write(&pack, &original_pack).unwrap();
    fs::write(&pack_index, &original_index).unwrap();

    let fixture = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "encrypted_gc_verify_and_rotation_work_through_the_cli"])
        .current_dir(&f.repo)
        .env("HOME", &f.home)
        .env("USERPROFILE", &f.home)
        .env(FIXTURE_HEAD, &tip)
        .output().unwrap();
    assert!(fixture.status.success(), "{}", String::from_utf8_lossy(&fixture.stdout));
    let loose = ObjectHash::from_hex(fs::read_to_string(f.home.join("loose-hash")).unwrap());
    let result = call(&["rotate-key"], &old_file);
    assert_eq!(result["objects_rotated"], 4);
    assert_eq!(result["refs_rotated"], 2);
    assert_eq!(fs::read(&key_path).unwrap(), original_key);
    assert_ne!(fs::read(&pack).unwrap(), original_pack);
    assert!(!f.repo.join(".lit/key-rotation").exists());

    let rotated = ObjectStore::new_with_encryption(&f.repo, Some(NEW)).unwrap();
    assert!(matches!(rotated.read(&hash).unwrap(), Object::Commit(_)));
    assert!(matches!(rotated.read(&loose).unwrap(), Object::Blob(blob) if blob.content == b"loose after gc"));
    assert!(store.write(&Object::Blob(Blob::new(b"stale cipher".to_vec()))).is_err());
    assert_eq!(call(&["verify"], &new_file)["valid"], true);
    let status = call(&["status"], &new_file);
    assert_eq!(status["clean"], true);
    assert_eq!(status["head"], tip);
    assert!(ObjectStore::new_with_encryption(&f.repo, Some(OLD)).is_err());
}
