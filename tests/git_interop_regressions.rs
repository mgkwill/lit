use serde_json::Value;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use tempfile::TempDir;

#[cfg(unix)]
#[test]
fn checkout_preserves_imported_symlinks_without_overwriting_their_targets() {
    let f = Fixture::new();
    let outside = f._root.path().join("outside");
    fs::write(&outside, "canary").unwrap();
    std::os::unix::fs::symlink(&outside, f.git.join("link")).unwrap();
    f.git(&f.git, &["add", "link"]);
    f.git(&f.git, &["commit", "-qm", "symlink"]);
    f.import(&f.git);

    for _ in 0..2 {
        f.lit(&["checkout", "release/trunk"]);
        assert_eq!(fs::read_link(f.lit.join("link")).unwrap(), outside);
        assert_eq!(fs::read_to_string(&outside).unwrap(), "canary");
    }
}

struct Fixture {
    _root: TempDir,
    home: PathBuf,
    git: PathBuf,
    lit: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let root = TempDir::new().unwrap();
        let home = root.path().join("home");
        let git = root.path().join("source");
        let lit = root.path().join("lit");
        for dir in [&home, &git, &lit] {
            fs::create_dir(dir).unwrap();
        }
        let fixture = Self {
            _root: root,
            home,
            git,
            lit,
        };
        fixture.git(
            &fixture.git,
            &["init", "-q", "--object-format=sha1", "-b", "release/trunk"],
        );
        fixture
    }

    fn output(&self, binary: &str, cwd: &Path, args: &[&str], input: &[u8]) -> Output {
        let mut child = Command::new(binary)
            .current_dir(cwd)
            .args(args)
            .env("HOME", &self.home)
            .env("USERPROFILE", &self.home)
            .env("GIT_CONFIG_NOSYSTEM", "1")
            .env("GIT_AUTHOR_NAME", "Original Author")
            .env("GIT_AUTHOR_EMAIL", "author@example.test")
            .env("GIT_COMMITTER_NAME", "Original Committer")
            .env("GIT_COMMITTER_EMAIL", "committer@example.test")
            .env("GIT_AUTHOR_DATE", "2025-01-01T12:00:00+05:30")
            .env("GIT_COMMITTER_DATE", "2025-01-02T12:00:00-03:30")
            .env("LIT_OUTPUT", "json")
            .env_remove("LIT_PASSPHRASE")
            .env_remove("LIT_PASSPHRASE_FILE")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        child.stdin.take().unwrap().write_all(input).unwrap();
        child.wait_with_output().unwrap()
    }

    fn git(&self, cwd: &Path, args: &[&str]) -> String {
        let output = self.output("git", cwd, args, b"");
        Self::expect(output, args).trim_end().to_owned()
    }

    fn lit(&self, args: &[&str]) -> Value {
        let output = self.output(env!("CARGO_BIN_EXE_lit"), &self.lit, args, b"");
        serde_json::from_str(&Self::expect(output, args)).unwrap()
    }

    fn expect(output: Output, args: &[&str]) -> String {
        assert!(
            output.status.success(),
            "{args:?}: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        String::from_utf8(output.stdout).unwrap()
    }

    fn commit(&self, file: &str, content: &str, message: &str) {
        fs::write(self.git.join(file), content).unwrap();
        self.git(&self.git, &["add", file]);
        self.git(&self.git, &["commit", "-qm", message]);
    }

    fn export(&self, label: &str) -> PathBuf {
        let out = self._root.path().join(format!("{label}.git"));
        self.lit(&["export-git", out.to_str().unwrap()]);
        self.git(&out, &["fsck", "--full", "--strict"]);
        out
    }

    fn import(&self, source: &Path) -> Value {
        self.lit(&["import-git", source.to_str().unwrap()])
    }

    fn assert_ref_fidelity(&self, source: &Path, result: &Path) {
        for args in [
            &["rev-parse", "HEAD"][..],
            &["show-ref", "--head", "--dereference"],
        ] {
            assert_eq!(self.git(source, args), self.git(result, args), "{args:?}");
        }
        let parse_symbolic =
            |path: &Path| self.output("git", path, &["symbolic-ref", "-q", "HEAD"], b"");
        let original = parse_symbolic(source);
        let exported = parse_symbolic(result);
        assert_eq!(original.status.code(), exported.status.code());
        assert_eq!(original.stdout, exported.stdout);
        let reachable = |path: &Path| {
            let output = self.git(
                path,
                &["rev-list", "--objects", "--no-object-names", "--all"],
            );
            let mut objects: Vec<_> = output.lines().map(str::to_owned).collect();
            objects.sort();
            objects
        };
        assert_eq!(reachable(source), reachable(result));
    }
}

#[test]
fn loose_packed_and_mixed_refs_preserve_merge_and_tag_objects() {
    let f = Fixture::new();
    fs::create_dir_all(f.git.join("nested/bin")).unwrap();
    let initial = "repeated data\n".repeat(300);
    f.commit("nested/bin/a", &initial, "base");
    f.git(&f.git, &["branch", "topic/side"]);
    f.commit("nested/bin/a", &(initial + "trunk content"), "trunk");
    f.git(&f.git, &["checkout", "-q", "topic/side"]);
    f.commit("b", "side content", "side");
    f.git(&f.git, &["checkout", "-q", "release/trunk"]);
    f.git(
        &f.git,
        &["merge", "--no-ff", "-m", "merge side", "topic/side"],
    );
    f.git(&f.git, &["tag", "light", "HEAD^"]);
    f.git(
        &f.git,
        &["tag", "-a", "releases/annotated", "-m", "tag body", "HEAD"],
    );
    assert_eq!(f.import(&f.git)["refs_imported"], 4);
    f.assert_ref_fidelity(&f.git, &f.export("loose"));

    f.git(&f.git, &["gc", "--aggressive", "--prune=now"]);
    assert!(f.git.join(".git/packed-refs").is_file());
    assert!(fs::read_dir(f.git.join(".git/objects/pack"))
        .unwrap()
        .flatten()
        .any(|entry| entry.path().extension().is_some_and(|ext| ext == "pack")));
    assert!(!f.git.join(".git/refs/heads/release/trunk").exists());
    assert_eq!(f.import(&f.git)["refs_imported"], 4);
    f.assert_ref_fidelity(&f.git, &f.export("packed"));

    f.git(&f.git, &["branch", "-f", "topic/side", "HEAD"]);
    assert!(f.git.join(".git/refs/heads/topic/side").is_file());
    f.import(&f.git);
    f.assert_ref_fidelity(&f.git, &f.export("mixed"));
}

#[test]
fn detached_head_preserves_opaque_commit_bytes() {
    let f = Fixture::new();
    f.commit("a", "data", "original");
    let parent = f.git(&f.git, &["rev-parse", "HEAD"]);
    let tree = f.git(&f.git, &["rev-parse", "HEAD^{tree}"]);
    let mut raw = format!(
        "tree {tree}\nparent {parent}\nauthor Name <name@example.test> 1700000000 +0545\ncommitter Name <name@example.test> 1700000100 -0330\nencoding ISO-8859-1\n\n"
    ).into_bytes();
    raw.extend_from_slice(b"caf\xe9\n");
    let hash = Fixture::expect(
        f.output(
            "git",
            &f.git,
            &["hash-object", "-w", "-t", "commit", "--stdin"],
            &raw,
        ),
        &["hash-object"],
    );
    f.git(&f.git, &["checkout", "-q", "--detach", hash.trim()]);
    f.import(&f.git);
    let exported = f.export("detached");
    f.assert_ref_fidelity(&f.git, &exported);
    let object = f.output("git", &exported, &["cat-file", "commit", "HEAD"], b"");
    assert!(object.status.success());
    assert_eq!(object.stdout, raw);
}

#[test]
fn lit_commit_after_import_uses_original_git_parent() {
    let f = Fixture::new();
    f.commit("a", "seed", "root");
    let parent = f.git(&f.git, &["rev-parse", "HEAD"]);
    f.import(&f.git);
    f.lit(&["checkout", "release/trunk"]);
    fs::create_dir_all(f.lit.join("deep/path")).unwrap();
    fs::write(f.lit.join("deep/path/new"), "created in Lit").unwrap();
    f.lit(&["add", "deep"]);
    f.lit(&["commit", "-m", "native Lit commit"]);
    let exported = f.export("native-child");
    assert_eq!(f.git(&exported, &["rev-parse", "HEAD^"]), parent);
    assert_eq!(
        f.git(&exported, &["show", "HEAD:deep/path/new"]),
        "created in Lit"
    );
}

#[test]
fn unborn_head_keeps_its_branch_name() {
    let f = Fixture::new();
    assert_eq!(f.import(&f.git)["refs_imported"], 0);
    let exported = f.export("unborn");
    assert_eq!(
        f.git(&exported, &["symbolic-ref", "HEAD"]),
        "refs/heads/release/trunk"
    );
}

#[test]
fn failed_export_leaves_destination_untouched() {
    let f = Fixture::new();
    f.commit("a", "data", "root");
    f.import(&f.git);
    fs::write(
        f.lit.join(".lit/refs/heads/release/trunk"),
        format!("{}\n", "a".repeat(192)),
    )
    .unwrap();
    let dest = f._root.path().join("empty.git");
    fs::create_dir(&dest).unwrap();
    let response = f.output(
        env!("CARGO_BIN_EXE_lit"),
        &f.lit,
        &["export-git", dest.to_str().unwrap()],
        b"",
    );
    assert!(!response.status.success());
    assert!(fs::read_dir(dest).unwrap().next().is_none());
}

#[test]
fn bare_git_with_submodule_gitlink_round_trips_without_submodule_objects() {
    let f = Fixture::new();
    f.commit("a", "root", "base");
    let external = "ab".repeat(20);
    let gitlink = format!("160000,{external},submodule");
    f.git(&f.git, &["update-index", "--add", "--cacheinfo", &gitlink]);
    f.git(&f.git, &["commit", "-qm", "add submodule gitlink"]);
    let bare = f._root.path().join("bare.git");
    f.git(&f.git, &["clone", "--bare", ".", bare.to_str().unwrap()]);
    f.git(&bare, &["pack-refs", "--all"]);
    f.import(&bare);
    let exported = f.export("from-bare");
    f.assert_ref_fidelity(&bare, &exported);
    assert!(f
        .git(&exported, &["ls-tree", "HEAD", "submodule"])
        .contains(&external));
}

#[test]
fn rejects_sha256_source_before_initializing_lit() {
    let f = Fixture::new();
    let src = f._root.path().join("sha256");
    fs::create_dir(&src).unwrap();
    f.git(&src, &["init", "-q", "--object-format=sha256"]);
    let response = f.output(
        env!("CARGO_BIN_EXE_lit"),
        &f.lit,
        &["import-git", src.to_str().unwrap()],
        b"",
    );
    assert!(!response.status.success());
    assert!(String::from_utf8_lossy(&response.stderr).contains("Only SHA-1"));
    assert!(!f.lit.join(".lit").exists());
}
