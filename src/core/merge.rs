/// 3-Way Merge Engine
///
/// Implements recursive 3-way merge with structured conflict output.
/// Supports strategies: recursive (default), ours, theirs.
use crate::core::diff::{myers_diff, DiffOp};
use crate::core::{Blob, Object, ObjectHash, Tree, TreeEntry};
use crate::storage::ObjectStore;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Merge strategy
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MergeStrategy {
    Recursive,
    Ours,
    Theirs,
}

impl std::str::FromStr for MergeStrategy {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, String> {
        match s {
            "recursive" => Ok(MergeStrategy::Recursive),
            "ours" => Ok(MergeStrategy::Ours),
            "theirs" => Ok(MergeStrategy::Theirs),
            _ => Err(format!(
                "Unknown merge strategy: '{}'. Valid: recursive, ours, theirs",
                s
            )),
        }
    }
}

/// Result of a merge operation
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MergeResult {
    /// The merged tree (if merge succeeded or has conflicts with partial results)
    pub tree: Option<ObjectHash>,
    /// Whether the merge was a fast-forward
    pub fast_forward: bool,
    /// Whether conflicts were detected
    pub has_conflicts: bool,
    /// Per-file merge results
    pub file_results: Vec<FileMergeResult>,
    /// Strategy used
    pub strategy: String,
}

/// Per-file merge result
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileMergeResult {
    pub path: String,
    pub status: FileMergeStatus,
    /// Conflict regions (only present if status == Conflict)
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub conflicts: Vec<ConflictRegion>,
}

/// File-level merge status
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum FileMergeStatus {
    Clean,
    Conflict,
    Added,
    Deleted,
    /// Both sides modified but auto-resolved
    AutoResolved,
}

/// A single conflict region in a file
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConflictRegion {
    pub start_line: usize,
    pub ours: Vec<String>,
    pub theirs: Vec<String>,
    pub base: Vec<String>,
}

/// Find the common ancestor (merge base) between two commits
pub fn find_merge_base(
    store: &ObjectStore,
    commit_a: &ObjectHash,
    commit_b: &ObjectHash,
) -> Result<Option<ObjectHash>, String> {
    // BFS from both commits, first intersection is the merge base
    let mut visited_a: HashMap<String, usize> = HashMap::new();
    let mut visited_b: HashMap<String, usize> = HashMap::new();
    let mut queue_a = vec![commit_a.clone()];
    let mut queue_b = vec![commit_b.clone()];
    let mut depth = 0usize;

    // Alternating BFS from both sides
    while !queue_a.is_empty() || !queue_b.is_empty() {
        // Process queue A
        let mut next_a = Vec::new();
        for hash in &queue_a {
            let key = hash.to_string();
            if visited_b.contains_key(&key) {
                return Ok(Some(hash.clone()));
            }
            if visited_a.contains_key(&key) {
                continue;
            }
            visited_a.insert(key, depth);

            if let Ok(Object::Commit(commit)) = store.read(hash) {
                for parent in &commit.parents {
                    if !visited_a.contains_key(&parent.to_string()) {
                        next_a.push(parent.clone());
                    }
                }
            }
        }
        queue_a = next_a;

        // Process queue B
        let mut next_b = Vec::new();
        for hash in &queue_b {
            let key = hash.to_string();
            if visited_a.contains_key(&key) {
                return Ok(Some(hash.clone()));
            }
            if visited_b.contains_key(&key) {
                continue;
            }
            visited_b.insert(key, depth);

            if let Ok(Object::Commit(commit)) = store.read(hash) {
                for parent in &commit.parents {
                    if !visited_b.contains_key(&parent.to_string()) {
                        next_b.push(parent.clone());
                    }
                }
            }
        }
        queue_b = next_b;
        depth += 1;

        // Safety limit
        if depth > 10000 {
            return Err("Merge base search exceeded depth limit".to_string());
        }
    }

    Ok(None) // No common ancestor
}

/// Check if commit_a is an ancestor of commit_b (for fast-forward detection)
pub fn is_ancestor(
    store: &ObjectStore,
    ancestor: &ObjectHash,
    descendant: &ObjectHash,
) -> Result<bool, String> {
    if ancestor.to_string() == descendant.to_string() {
        return Ok(true);
    }

    let mut queue = vec![descendant.clone()];
    let mut visited = std::collections::HashSet::new();

    while let Some(hash) = queue.pop() {
        let key = hash.to_string();
        if key == ancestor.to_string() {
            return Ok(true);
        }
        if !visited.insert(key) {
            continue;
        }
        if let Ok(Object::Commit(commit)) = store.read(&hash) {
            for parent in &commit.parents {
                queue.push(parent.clone());
            }
        }
    }

    Ok(false)
}

/// Perform a 3-way merge between two trees with a common base
pub fn merge_trees(
    store: &ObjectStore,
    base_tree: Option<&Tree>,
    ours_tree: &Tree,
    theirs_tree: &Tree,
    strategy: MergeStrategy,
) -> Result<MergeResult, String> {
    let files = |tree: Option<&Tree>| -> Result<HashMap<_, _>, String> {
        Ok(match tree {
            Some(tree) => {
                let mut entries = HashMap::new();
                collect_merge_entries(store, tree, "", &mut entries)?;
                entries
            }
            None => HashMap::new(),
        })
    };
    let base = files(base_tree)?;
    let ours = files(Some(ours_tree))?;
    let theirs = files(Some(theirs_tree))?;
    let paths: std::collections::BTreeSet<_> = base
        .keys()
        .chain(ours.keys())
        .chain(theirs.keys())
        .collect();
    let mut entries = Vec::new();
    let mut file_results = Vec::new();

    for path in paths {
        let (base, ours, theirs) = (base.get(path), ours.get(path), theirs.get(path));
        let mut conflicts = Vec::new();
        let (status, selected) = if same_entry(ours, theirs) || same_entry(theirs, base) {
            (file_status(base, ours), ours.cloned())
        } else if same_entry(ours, base) {
            (file_status(base, theirs), theirs.cloned())
        } else if strategy != MergeStrategy::Recursive {
            let selected = if strategy == MergeStrategy::Ours {
                ours
            } else {
                theirs
            };
            (FileMergeStatus::AutoResolved, selected.cloned())
        } else {
            let (merged, regions) = merge_changed_file(store, base, ours, theirs)?;
            conflicts = regions;
            match merged {
                Some(entry) => (FileMergeStatus::AutoResolved, Some(entry)),
                None => (FileMergeStatus::Conflict, ours.cloned()),
            }
        };
        if let Some(entry) = selected {
            entries.push((path.clone(), entry));
        }
        file_results.push(FileMergeResult {
            path: path.clone(),
            status,
            conflicts,
        });
    }

    // A file and its descendants cannot coexist in a tree.
    let names: std::collections::HashSet<_> =
        entries.iter().map(|(path, _)| path.as_str()).collect();
    for (path, _) in &entries {
        if path
            .match_indices('/')
            .any(|(i, _)| names.contains(&path[..i]))
        {
            return Err(format!("File/directory conflict at {path}"));
        }
    }

    Ok(MergeResult {
        tree: Some(build_flat_tree(store, &entries)?),
        fast_forward: false,
        has_conflicts: file_results
            .iter()
            .any(|entry| entry.status == FileMergeStatus::Conflict),
        file_results,
        strategy: format!("{strategy:?}").to_lowercase(),
    })
}

fn collect_merge_entries(
    store: &ObjectStore,
    tree: &Tree,
    prefix: &str,
    entries: &mut HashMap<String, TreeEntry>,
) -> Result<(), String> {
    for entry in &tree.entries {
        let path = format!("{prefix}{}", entry.name);
        match entry.object_type.as_str() {
            "blob" => {
                if entries.insert(path.clone(), entry.clone()).is_some() {
                    return Err(format!("Duplicate merge path {path}"));
                }
            }
            "tree" => {
                let Object::Tree(subtree) = store.read(&entry.hash)? else {
                    return Err(format!("Expected tree object at {path}"));
                };
                collect_merge_entries(store, &subtree, &format!("{path}/"), entries)?;
            }
            _ => {
                return Err(format!(
                    "Unsupported merge entry type at {path}: {}",
                    entry.object_type
                ))
            }
        }
    }
    Ok(())
}

fn same_entry(a: Option<&TreeEntry>, b: Option<&TreeEntry>) -> bool {
    match (a, b) {
        (Some(a), Some(b)) => a.hash == b.hash && a.mode == b.mode,
        (None, None) => true,
        _ => false,
    }
}

fn file_status(base: Option<&TreeEntry>, selected: Option<&TreeEntry>) -> FileMergeStatus {
    match (base, selected) {
        (None, Some(_)) => FileMergeStatus::Added,
        (Some(_), None) => FileMergeStatus::Deleted,
        _ => FileMergeStatus::Clean,
    }
}

fn merge_changed_file(
    store: &ObjectStore,
    base: Option<&TreeEntry>,
    ours: Option<&TreeEntry>,
    theirs: Option<&TreeEntry>,
) -> Result<(Option<TreeEntry>, Vec<ConflictRegion>), String> {
    let (Some(base), Some(ours), Some(theirs)) = (base, ours, theirs) else {
        return Ok((None, Vec::new()));
    };
    let mut merged = ours.clone();
    // Combine an independent mode change and content change.
    if ours.mode == base.mode {
        merged.mode.clone_from(&theirs.mode);
    } else if theirs.mode != base.mode && ours.mode != theirs.mode {
        return Ok((None, Vec::new()));
    }
    if ours.hash == theirs.hash || theirs.hash == base.hash {
        return Ok((Some(merged), Vec::new()));
    }
    if ours.hash == base.hash {
        merged.hash.clone_from(&theirs.hash);
        return Ok((Some(merged), Vec::new()));
    }
    if [base, ours, theirs]
        .iter()
        .any(|entry| !matches!(entry.mode.as_str(), "100644" | "100755"))
    {
        return Ok((None, Vec::new()));
    }
    let read_text = |entry: &TreeEntry| -> Result<Option<String>, String> {
        let Object::Blob(blob) = store.read(&entry.hash)? else {
            return Err(format!("Expected blob object for {}", entry.hash));
        };
        Ok(String::from_utf8(blob.content)
            .ok()
            .filter(|text| !text.contains('\0')))
    };
    let (Some(base), Some(ours), Some(theirs)) =
        (read_text(base)?, read_text(ours)?, read_text(theirs)?)
    else {
        return Ok((None, Vec::new()));
    };
    let base: Vec<_> = base.split_inclusive('\n').collect();
    let ours: Vec<_> = ours.split_inclusive('\n').collect();
    let theirs: Vec<_> = theirs.split_inclusive('\n').collect();
    let result = three_way_merge(&base, &ours, &theirs);
    if !result.conflicts.is_empty() {
        return Ok((None, result.conflicts));
    }
    merged.hash = store.write(&Object::Blob(Blob::new(
        result.merged_lines.concat().into_bytes(),
    )))?;
    Ok((Some(merged), Vec::new()))
}

struct ThreeWayResult {
    merged_lines: Vec<String>,
    conflicts: Vec<ConflictRegion>,
}

#[derive(PartialEq, Eq)]
struct Edit {
    start: usize,
    end: usize,
    lines: Vec<String>,
}

fn edits(base: &[&str], side: &[&str]) -> Vec<Edit> {
    let mut edits = Vec::new();
    let mut offset = 0;
    let mut pending: Option<Edit> = None;
    for operation in myers_diff(base, side) {
        match operation {
            DiffOp::Equal(_) => {
                if let Some(edit) = pending.take() {
                    edits.push(edit);
                }
                offset += 1;
            }
            DiffOp::Delete(_) => {
                let edit = pending.get_or_insert_with(|| Edit {
                    start: offset,
                    end: offset,
                    lines: Vec::new(),
                });
                offset += 1;
                edit.end = offset;
            }
            DiffOp::Insert(line) => {
                pending
                    .get_or_insert_with(|| Edit {
                        start: offset,
                        end: offset,
                        lines: Vec::new(),
                    })
                    .lines
                    .push(line.to_string());
            }
        }
    }
    edits.extend(pending);
    edits
}

fn three_way_merge(base: &[&str], ours: &[&str], theirs: &[&str]) -> ThreeWayResult {
    let left = edits(base, ours);
    let right = edits(base, theirs);
    let mut conflicts = Vec::new();
    let mut left = left.into_iter().peekable();
    let mut right = right.into_iter().peekable();
    let mut merged_lines = Vec::new();
    let mut offset = 0;

    while let (Some(ours), Some(theirs)) = (left.peek(), right.peek()) {
        let ours_first = (ours.start, ours.end) <= (theirs.start, theirs.end);
        let (first, other) = if ours_first {
            (&mut left, &mut right)
        } else {
            (&mut right, &mut left)
        };
        let edit = first.next().expect("peeked edit");
        let next = other.peek().expect("peeked edit");
        if &edit == next {
            other.next();
        } else if next.start < edit.end
            || (edit.start == edit.end && next.start == next.end && edit.start == next.start)
        {
            let next = other.next().expect("peeked overlapping edit");
            let start = edit.start;
            let mut end = edit.end.max(next.end);
            let mut first_group = vec![edit];
            let mut other_group = vec![next];
            loop {
                if first.peek().is_some_and(|edit| edit.start < end) {
                    let edit = first.next().expect("peeked overlapping edit");
                    end = end.max(edit.end);
                    first_group.push(edit);
                } else if other.peek().is_some_and(|edit| edit.start < end) {
                    let edit = other.next().expect("peeked overlapping edit");
                    end = end.max(edit.end);
                    other_group.push(edit);
                } else {
                    break;
                }
            }
            let (ours, theirs) = if ours_first {
                (first_group, other_group)
            } else {
                (other_group, first_group)
            };
            conflicts.push(ConflictRegion {
                start_line: start + 1,
                ours: render_region(base, start, end, ours),
                theirs: render_region(base, start, end, theirs),
                base: render_region(base, start, end, Vec::new()),
            });
            offset = end;
            continue;
        }
        merged_lines.extend(base[offset..edit.start].iter().map(|line| line.to_string()));
        merged_lines.extend(edit.lines);
        offset = edit.end;
    }
    for edit in left.chain(right) {
        merged_lines.extend(base[offset..edit.start].iter().map(|line| line.to_string()));
        merged_lines.extend(edit.lines);
        offset = edit.end;
    }
    merged_lines.extend(base[offset..].iter().map(|line| line.to_string()));
    ThreeWayResult {
        merged_lines,
        conflicts,
    }
}

fn render_region(base: &[&str], start: usize, end: usize, edits: Vec<Edit>) -> Vec<String> {
    let mut region = Vec::new();
    let mut offset = start;
    for edit in edits {
        region.extend(base[offset..edit.start].iter().map(|line| line.to_string()));
        region.extend(edit.lines);
        offset = edit.end;
    }
    region.extend(base[offset..end].iter().map(|line| line.to_string()));
    region
        .into_iter()
        .map(|line| line.trim_end_matches(['\r', '\n']).to_owned())
        .collect()
}

pub(crate) fn build_flat_tree(
    store: &ObjectStore,
    entries: &[(String, TreeEntry)],
) -> Result<ObjectHash, String> {
    let mut root_entries = Vec::new();
    let mut subdirs: HashMap<String, Vec<(String, TreeEntry)>> = HashMap::new();
    for (path, entry) in entries {
        if let Some((directory, rest)) = path.split_once('/') {
            subdirs
                .entry(directory.to_owned())
                .or_default()
                .push((rest.to_owned(), entry.clone()));
        } else {
            root_entries.push(TreeEntry {
                name: path.clone(),
                ..entry.clone()
            });
        }
    }
    for (directory, entries) in subdirs {
        root_entries.push(TreeEntry {
            mode: "040000".to_owned(),
            name: directory,
            hash: build_flat_tree(store, &entries)?,
            object_type: "tree".to_owned(),
        });
    }
    root_entries.sort_by(|a, b| a.name.cmp(&b.name));
    store.write(&Object::Tree(Tree {
        entries: root_entries,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    #[test]
    fn test_merge_strategy_from_str() {
        assert_eq!(
            MergeStrategy::from_str("recursive").unwrap(),
            MergeStrategy::Recursive
        );
        assert_eq!(
            MergeStrategy::from_str("ours").unwrap(),
            MergeStrategy::Ours
        );
        assert_eq!(
            MergeStrategy::from_str("theirs").unwrap(),
            MergeStrategy::Theirs
        );
        assert!(MergeStrategy::from_str("invalid").is_err());
    }

    #[test]
    fn test_three_way_merge_no_conflict() {
        let base = vec!["line1", "line2", "line3"];
        let ours = vec!["line1", "MODIFIED", "line3"];
        let theirs = vec!["line1", "line2", "line3"];
        let result = three_way_merge(&base, &ours, &theirs);
        assert!(result.conflicts.is_empty());
    }

    #[test]
    fn test_three_way_merge_both_sides_different_regions() {
        let base = vec!["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"];
        let ours = vec!["A", "b", "c", "d", "e", "f", "g", "h", "i", "j"];
        let theirs = vec!["a", "b", "c", "d", "e", "f", "g", "h", "i", "J"];
        let result = three_way_merge(&base, &ours, &theirs);
        assert!(result.conflicts.is_empty());
        assert_eq!(
            result.merged_lines,
            vec!["A", "b", "c", "d", "e", "f", "g", "h", "i", "J"]
        );
    }

    #[test]
    fn test_three_way_merge_conflict() {
        let base = ["first", "line2", "middle", "line4", "last"];
        let ours = ["first", "OURS", "middle", "ours again", "last"];
        let theirs = ["first", "THEIRS", "middle", "theirs again", "last"];
        let result = three_way_merge(&base, &ours, &theirs);
        assert_eq!(result.conflicts.len(), 2);
        assert_eq!(result.conflicts[0].ours, ["OURS"]);
        assert_eq!(result.conflicts[1].theirs, ["theirs again"]);
    }
}
