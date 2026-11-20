# OpenLit examples

These examples use the `lit` command from OpenLit 0.1.0. Paths and repository
URLs are examples. Run writes in a disposable repository while learning.

## Validate a batch before running it

Save this as `operations.jsonl`:

```jsonl
{"command":"add","args":{"files":["hello.txt"]}}
{"command":"commit","args":{"message":"Save the edit"}}
{"command":"status","args":{}}
```

```sh
lit batch --dry-run --fail-on-error < operations.jsonl
lit batch --rollback-on-error < operations.jsonl
```

Dry-run checks operation names and argument types. It does not prove that files
exist or that an operation will succeed. The older flat form, such as
`{"op":"add","files":["hello.txt"]}`, is also accepted.

Read the result of every line and the top-level `failed` count. Ordinary batch
mode can report failed operations while exiting zero; `--fail-on-error` makes
that an unsuccessful process exit. `--atomic` stops at the first error and keeps
earlier writes.

`--rollback-on-error` restores the starting index and branch tip after a failed
line. It accepts add, commit, snapshot, status, log, and branch listing on a
checked-out branch. It holds the repository write lock until it finishes. Readers
can see intermediate states. Files in the working directory are not isolated
from direct edits by other programs. Objects left without a reference can later
be collected by `lit gc`. After a crash, run `lit tx rollback` before writing.

## Use an explicit transaction

```sh
lit tx begin
lit add hello.txt
lit commit -m "Save the edit"
lit tx commit
```

Use `lit tx rollback` instead of `commit` to restore the checkpoint. It covers
the original branch, index, and files tracked when the transaction began.
Untracked files are left alone. Direct edits to tracked files made after the
checkpoint can be replaced. If rollback is interrupted, run it again.

## Connect to Git remotes

```sh
lit remote add origin git+https://github.com/OWNER/REPOSITORY.git
lit fetch origin
lit pull origin main
lit push origin main
```

For SSH, a URL looks like `git+ssh://git@github.com/OWNER/REPOSITORY.git`.
Use the system Git credential helper or SSH agent. Keep host-key checking
on and keep credentials out of URLs, command history, and shared logs.
Read-only credentials can fetch but cannot push.

Local Git remotes can use `file:///absolute/path/repository.git`. Native OpenLit
repositories also support local file transport. Native `lit://` transport is
for trusted local networks; it is not the authenticated Git HTTPS path.

## Import and export Git history

```sh
mkdir imported
cd imported
lit init
lit import-git /absolute/path/to/git-repository
lit verify
lit export-git /absolute/path/to/new-export.git
git -C /absolute/path/to/new-export.git fsck --full
```

Use a new export destination. Check HEAD, branches, tags, and the files you need
with Git before switching tools. Import/export has regression coverage for packed
refs and round trips, but it is not a promise about every Git extension.

## Connect an MCP client

Configure the client to launch `lit mcp-serve --stdio` in the repository directory.
Discover tools with `tools/list`; use their returned argument schemas. Existing
names such as `lit_status` and `lit_commit` remain unchanged.

For a local HTTP client, `lit mcp-serve --port 8385` listens on loopback. Do not
expose it to untrusted clients. Use the authenticated broker for shared publication.

## Pack and check storage

```sh
lit verify
lit gc
lit verify
```

Packing changes storage layout; it does not replace a backup. Encrypted
repositories need their passphrase for these operations. See [encryption](ENCRYPTION.md)
and [migration](DEPLOYMENT.md) before moving or rotating encrypted data.
