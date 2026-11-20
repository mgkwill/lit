"""Constrained Rust regression edits to existing Lit modules."""

import json
import re


MODULES = (
    "src/config.rs", "src/core/diff.rs", "src/core/merge.rs", "src/core/objects.rs",
    "src/core/refs.rs", "src/storage/index.rs", "src/errors.rs", "src/response.rs",
)


def body(path, case):
    literal = json.dumps(case)
    if path == "src/config.rs":
        return [
            "let mut config = crate::config::LitConfig::default();",
            f'config.set("core.default_branch", {literal}).unwrap();',
            f'assert_eq!(config.get("core.default_branch").as_deref(), Some({literal}));',
        ]
    if path == "src/core/diff.rs":
        return [
            f"let text = {literal};",
            "assert!(crate::core::diff::diff_text(text, text).is_empty());",
            "assert!(!crate::core::diff::diff_text(text, \"\").is_empty());",
        ]
    if path == "src/core/merge.rs":
        return [
            f"assert!({literal}.parse::<crate::core::merge::MergeStrategy>().is_err());",
            'assert_eq!("ours".parse::<crate::core::merge::MergeStrategy>().unwrap(),',
            "    crate::core::merge::MergeStrategy::Ours);",
        ]
    if path == "src/core/objects.rs":
        return [
            f"let bytes = {literal}.as_bytes();",
            "let blob = crate::core::objects::Object::Blob(crate::core::objects::Blob::new(bytes.to_vec()));",
            "let recovered = crate::core::objects::Object::from_bytes(&blob.to_bytes()).unwrap();",
            "assert_eq!(blob.hash(), recovered.hash());",
        ]
    if path == "src/core/refs.rs":
        return [
            f"let parent = std::path::Path::new({literal});",
            'assert_eq!(crate::core::refs::get_lit_dir(parent), parent.join(".lit"));',
        ]
    if path == "src/storage/index.rs":
        return [
            "let mut index = crate::storage::index::Index::new();",
            f'index.add({literal}.into(), "hash".into(), "100644".into());',
            'index.add("first".into(), "other".into(), "100644".into());',
            f'assert_eq!(index.sorted_entries()[0].path, "first");',
            f'assert_eq!(index.remove({literal}).unwrap().mode, "100644");',
        ]
    if path == "src/errors.rs":
        return [
            f"let error = crate::errors::LitError::config({literal});",
            f"assert_eq!(error.internal_message(), {literal});",
            'assert_eq!(error.error_code(), "CONFIG_ERROR");',
        ]
    if path == "src/response.rs":
        value = int(case[-3:])
        flags = ("true, true", "true, false", "false, true")[value % 3]
        variant = "Json" if value % 3 == 1 else "Human"
        return [f"assert_eq!(crate::response::OutputFormat::resolve({flags}),",
                f"    crate::response::OutputFormat::{variant});"]
    raise ValueError("Rust workload path is outside the approved module set")


def snippet(case, target):
    if not re.fullmatch(r"pi_lit_[a-z0-9]{8}_[0-9]{3}", case):
        raise ValueError("invalid Rust workload test name")
    indent = target["indent"]
    if indent not in ("", "    "):
        raise ValueError("invalid Rust workload indentation")
    header = ["#[test]"] if indent else ["#[cfg(test)]", "#[test]"]
    lines = [*header, f"fn {case}() {{", *("    " + line for line in body(target["file"], case)), "}"]
    return ("\n".join(indent + line for line in lines) + "\n\n").encode()


def expected(before, target, cases):
    anchor = target["anchor"].encode()
    if not anchor or before.count(anchor) != 1:
        raise ValueError("Rust insertion anchor must occur exactly once")
    added = []
    for case in cases:
        text = snippet(case, target)
        if text not in before:
            if case.encode() in before:
                raise ValueError("an existing test differs from the authorized source")
            added.append(text)
    return anchor, b"".join(added)


def projected(before, target, cases):
    anchor, insert = expected(before, target, cases)
    return before.replace(anchor, insert + anchor, 1)


def verify(before, after, target, cases):
    anchor, insert = expected(before, target, cases)
    prefix, suffix = before.split(anchor, 1)
    suffix = anchor + suffix
    if not insert or not after.startswith(prefix) or not after.endswith(suffix):
        raise ValueError("source edit changed material outside the assigned Rust insertion")
    actual = after[len(prefix):len(after) - len(suffix)]
    normalize = lambda block: re.sub(rb"[ \t\r\n]+", b"", block)
    if normalize(actual) != normalize(insert):
        raise ValueError("source edit differs from the authorized Rust regression test")
    return prefix + insert + suffix


def prompt(before, target, cases):
    _, addition = expected(before, target, cases)
    name = target["file"].rsplit("/", 1)[-1]
    return (f"Use your file tools to add the supplied Rust regression source to {name}. "
            "Preserve all existing bytes. Insert only the supplied block, immediately before the complete "
            "anchor shown below. Preserve each existing test. Use the exact Rust code. "
            "Do not edit any other file. Finish after saving.\n\n"
            f"Block to insert:\n```rust\n{addition.decode()}```\n\n"
            f"Anchor (keep this unchanged):\n```rust\n{target['anchor']}```")


def anchors(source):
    """Find isolated module and existing test boundaries in a trusted Rust pin."""
    lines = source.decode().splitlines(keepends=True)
    selected = []
    for index, line in enumerate(lines):
        indent = ""
        if re.match(r"^(?:pub(?:\([^\n]+\))? )?(?:struct |enum |fn |type |trait |impl[ <])", line):
            while index > 0 and lines[index - 1].strip():
                index -= 1
        elif line.strip() == "#[test]" and line.startswith("    ") and index + 1 < len(lines) and re.match(
                r"^    fn [a-zA-Z0-9_]+", lines[index + 1]):
            indent = "    "
        else:
            continue
        if (not selected or index - selected[-1][0] >= 8) and index + 2 < len(lines):
            value = "".join(lines[index:index + 3])
            if source.count(value.encode()) == 1:
                selected.append((index, value, indent))
    return [(anchor, indent) for _, anchor, indent in selected]
