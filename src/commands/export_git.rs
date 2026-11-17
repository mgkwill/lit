use super::git_interop::{checked_output, git, git_oid, translate, valid_oid, GitObject};
use crate::core::{find_repo_root, list_refs, Object, ObjectHash};
use crate::errors::LitError;
use crate::response::ExportGitResponse;
use crate::storage::ObjectStore;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs;
use std::io::{ErrorKind, Write};
use std::path::Path;

pub fn execute(destination: String) -> Result<ExportGitResponse, LitError> {
    let repo_root = find_repo_root()?;
    let dest = Path::new(&destination);
    let parent = dest
        .parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let dest_exists = match fs::symlink_metadata(dest) {
        Ok(metadata) if metadata.is_dir() => {
            if fs::read_dir(dest)
                .map_err(|e| format!("Read export destination: {e}"))?
                .next()
                .is_some()
            {
                return Err("Export destination must be new or an empty directory".into());
            }
            true
        }
        Ok(_) => return Err("Export destination must be a directory".into()),
        Err(e) if e.kind() == ErrorKind::NotFound => false,
        Err(e) => return Err(format!("Inspect export destination: {e}").into()),
    };
    fs::create_dir_all(parent).map_err(|e| format!("Create export parent: {e}"))?;
    let staging = tempfile::tempdir_in(parent).map_err(|e| format!("Stage Git export: {e}"))?;
    let output = staging.path();
    checked_output(git(output).args(["init", "--bare", "--object-format=sha1", "--template="]))?;

    let mut refs = BTreeMap::new();
    for namespace in ["heads", "tags", "remotes"] {
        for reference in list_refs(&repo_root, namespace)? {
            let name = format!("refs/{namespace}/{}", reference.name.replace('\\', "/"));
            refs.insert(name, reference.hash);
        }
    }
    let head_file = match fs::read_to_string(repo_root.join(".lit/HEAD")) {
        Ok(value) => value,
        Err(e) if e.kind() == ErrorKind::NotFound => "ref: refs/heads/main".to_owned(),
        Err(e) => return Err(format!("Read Lit HEAD: {e}").into()),
    };
    let head = head_file.trim();
    let symbolic = head.strip_prefix("ref: ");
    let mut roots: Vec<_> = refs.values().cloned().collect();
    if symbolic.is_none() {
        roots.push(head.to_owned());
    }

    let (map, count) = write_objects(&repo_root, output, roots)?;

    for (name, lit_id) in &refs {
        let oid = lookup(&map, lit_id)?;
        checked_output(git(output).args(["update-ref", name, oid]))?;
    }
    if let Some(reference) = symbolic {
        if !reference.starts_with("refs/heads/") {
            return Err("Unsupported symbolic Lit HEAD".into());
        }
        checked_output(git(output).args(["symbolic-ref", "HEAD", reference]))?;
    } else {
        checked_output(git(output).args([
            "update-ref",
            "--no-deref",
            "HEAD",
            lookup(&map, head)?,
        ]))?;
    }
    checked_output(git(output).args(["pack-refs", "--all"]))?;
    checked_output(git(output).args(["fsck", "--full", "--strict"]))?;

    if dest_exists {
        fs::remove_dir(dest).map_err(|e| format!("Replace empty export destination: {e}"))?;
    }
    fs::rename(output, dest).map_err(|e| format!("Publish Git export: {e}"))?;

    let objects_exported = count as u64;
    let refs_exported = refs.len() as u64;
    Ok(ExportGitResponse {
        destination,
        objects_exported,
        refs_exported,
        message: format!(
            "Exported {objects_exported} objects and {refs_exported} refs to Git repository"
        ),
    })
}

pub(super) fn write_objects(
    root: &Path,
    output: &Path,
    roots: Vec<String>,
) -> Result<(HashMap<String, String>, usize), LitError> {
    let store = ObjectStore::new(root);
    let mut writer = ObjectWriter {
        destination: output,
        written: HashSet::new(),
    };
    let map = translate(
        roots,
        |id| {
            let object = store.read(&ObjectHash::from_hex(id.to_owned()))?;
            let dependencies = dependencies(&object)
                .into_iter()
                .map(ToString::to_string)
                .collect();
            Ok((object, dependencies))
        },
        |object, map| writer.convert(&object, map),
    )?;
    Ok((map, writer.written.len()))
}

fn dependencies(object: &Object) -> Vec<&ObjectHash> {
    match object {
        Object::Blob(_) => Vec::new(),
        Object::Tree(tree) => tree
            .entries
            .iter()
            .filter(|entry| entry.mode != "160000")
            .map(|entry| &entry.hash)
            .collect(),
        Object::Commit(commit) => std::iter::once(&commit.tree)
            .chain(&commit.parents)
            .collect(),
        Object::Tag(tag) => vec![&tag.target],
    }
}

fn lookup<'a>(map: &'a HashMap<String, String>, id: &str) -> Result<&'a str, LitError> {
    map.get(id)
        .map(String::as_str)
        .ok_or_else(|| format!("Missing exported object for Lit ID {id}").into())
}

struct ObjectWriter<'a> {
    destination: &'a Path,
    written: HashSet<String>,
}

impl ObjectWriter<'_> {
    fn convert(
        &mut self,
        object: &Object,
        map: &HashMap<String, String>,
    ) -> Result<String, LitError> {
        let original = match object {
            Object::Commit(commit) => commit.git_source.as_ref(),
            Object::Tag(tag) => tag.git_source.as_ref(),
            _ => None,
        };
        if let Some(body) = original {
            let git_object = GitObject {
                kind: object.type_name().into(),
                body: body.clone(),
            };
            let mut reverse = HashMap::new();
            for dependency in dependencies(object) {
                reverse.insert(
                    lookup(map, dependency.as_str())?.to_owned(),
                    dependency.clone(),
                );
            }
            // Only reuse the bytes if they describe this exact Lit object and
            // the exported dependencies still have their original identities.
            if git_object
                .to_lit(&reverse)
                .is_ok_and(|imported| imported.hash() == object.hash())
            {
                return self.write(object.type_name(), body);
            }
            return Err(
                "Original Git object does not match its Lit data or exported dependencies".into(),
            );
        }

        match object {
            Object::Blob(blob) => self.write("blob", &blob.content),
            Object::Tree(tree) => {
                let mut builder = TreeBuilder::default();
                for entry in &tree.entries {
                    let mode = if entry.mode == "040000" {
                        "40000"
                    } else {
                        &entry.mode
                    };
                    let oid = if mode == "160000" {
                        let oid = entry.hash.as_str();
                        if !valid_oid(oid) {
                            return Err("Invalid Git submodule commit ID".into());
                        }
                        oid
                    } else {
                        lookup(map, entry.hash.as_str())?
                    };
                    builder.insert(&entry.name, mode.to_owned(), oid.to_owned())?;
                }
                builder.write(self)
            }
            Object::Commit(commit) => {
                let mut body = format!("tree {}\n", lookup(map, commit.tree.as_str())?);
                for parent in &commit.parents {
                    body.push_str(&format!("parent {}\n", lookup(map, parent.as_str())?));
                }
                body.push_str(&format!(
                    "author {} {} +0000\n",
                    git_identity(&commit.author),
                    commit.timestamp
                ));
                body.push_str(&format!(
                    "committer {} {} +0000\n\n",
                    git_identity(&commit.committer),
                    commit.timestamp
                ));
                body.push_str(&commit.message);
                if let Some(metadata) = &commit.metadata {
                    body.push_str(&format!("\n\nLit-Metadata: {metadata}"));
                }
                self.write("commit", body.as_bytes())
            }
            Object::Tag(tag) => {
                let target = lookup(map, tag.target.as_str())?;
                let body = format!(
                    "object {target}\ntype {}\ntag {}\ntagger {} {} +0000\n\n{}",
                    tag.target_type,
                    tag.tag_name,
                    git_identity(&tag.tagger),
                    tag.timestamp,
                    tag.message
                );
                self.write("tag", body.as_bytes())
            }
        }
    }

    fn write(&mut self, kind: &str, content: &[u8]) -> Result<String, LitError> {
        let oid = git_oid(kind, content);
        if self.written.insert(oid.clone()) {
            let dir = self.destination.join("objects").join(&oid[..2]);
            fs::create_dir_all(&dir).map_err(|e| format!("Create Git object directory: {e}"))?;
            let file = fs::File::create(dir.join(&oid[2..]))
                .map_err(|e| format!("Create Git object {oid}: {e}"))?;
            let mut encoder = flate2::write::ZlibEncoder::new(file, flate2::Compression::default());
            write!(encoder, "{kind} {}\0", content.len())
                .and_then(|_| encoder.write_all(content))
                .and_then(|_| encoder.finish())
                .map_err(|e| format!("Write Git object {oid}: {e}"))?;
        }
        Ok(oid)
    }
}

#[derive(Default)]
struct TreeBuilder {
    entries: BTreeMap<String, (String, String)>,
    directories: BTreeMap<String, TreeBuilder>,
}

impl TreeBuilder {
    fn insert(&mut self, path: &str, mode: String, oid: String) -> Result<(), LitError> {
        let mut tree = self;
        let mut segments = path.split('/').peekable();
        while let Some(part) = segments.next() {
            validate_segment(part)?;
            if tree.entries.contains_key(part) {
                return Err(format!("Conflicting Git export tree path: {path}").into());
            }
            if segments.peek().is_none() {
                if tree.directories.contains_key(part) {
                    return Err(format!("Conflicting Git export tree path: {path}").into());
                }
                tree.entries.insert(part.into(), (mode, oid));
                break;
            }
            tree = tree.directories.entry(part.into()).or_default();
        }
        Ok(())
    }

    fn write(mut self, writer: &mut ObjectWriter<'_>) -> Result<String, LitError> {
        for (name, directory) in self.directories {
            self.entries
                .insert(name, ("40000".into(), directory.write(writer)?));
        }
        let mut ordered: Vec<_> = self.entries.into_iter().collect();
        ordered.sort_by_cached_key(|(name, (mode, _))| {
            if mode == "40000" {
                format!("{name}/")
            } else {
                name.clone()
            }
        });
        let mut contents = Vec::new();
        for (name, (mode, oid)) in ordered {
            write!(contents, "{mode} {name}\0").map_err(|e| e.to_string())?;
            contents.extend_from_slice(&hex::decode(oid).map_err(|e| e.to_string())?);
        }
        writer.write("tree", &contents)
    }
}

fn validate_segment(name: &str) -> Result<(), LitError> {
    if name.is_empty()
        || name.contains(['\0', '\\'])
        || matches!(name, "." | "..")
        || name.eq_ignore_ascii_case(".git")
        || name.eq_ignore_ascii_case(".lit")
    {
        return Err(format!("Unsupported Git tree path: {name}").into());
    }
    Ok(())
}

fn git_identity(value: &str) -> String {
    let identity = value.replace(['\r', '\n', '\0'], " ");
    if identity.ends_with('>') && identity.contains(" <") {
        identity
    } else {
        let name = identity.replace(['<', '>'], " ");
        let name = if name.trim().is_empty() {
            "Lit"
        } else {
            name.trim()
        };
        format!("{name} <lit@localhost>")
    }
}
