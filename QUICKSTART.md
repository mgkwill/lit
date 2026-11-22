# Start using OpenLit

The product is OpenLit; the executable is `lit`. These examples use a POSIX
shell on macOS or Linux. On Windows, create and edit the files in your editor
and run the same `lit` commands in PowerShell.

Build and install using [the installation guide](docs/INSTALL.md). Check that
`lit --version` reports `lit 0.1.1`.

## Save your first file

```sh
mkdir example
cd example
lit init
printf 'hello\n' > hello.txt
lit add hello.txt
lit commit -m "Save the first file"
lit status --human
lit log --human
```

`add` stages the current file contents. `commit` saves what you staged. Later
edits need another `add` before they can appear in a commit. JSON is the default
output; `--human` changes the display.

## Make a branch

```sh
lit checkout -b greeting
printf 'hello, team\n' > hello.txt
lit add hello.txt
lit commit -m "Update the greeting"
lit checkout main
lit merge greeting
lit verify
```

If both branches changed the same part of a file, inspect the reported conflict.
Choose the side you intend to keep with `lit resolve hello.txt --strategy ours`
or `--strategy theirs`, then run `lit resolve --continue`.

To cancel the merge, use `lit merge --abort`. It restores paths owned by the
merge and preserves unrelated work. If you edited one of those paths after the
merge stopped, save your edit elsewhere first; abort refuses to overwrite it.

## Work with a Git repository

```sh
lit clone git+https://github.com/OWNER/REPOSITORY.git project
cd project
lit fetch origin
lit pull origin main
```

Replace the repository URL with one you can read. Configure your normal Git
credential helper first. For SSH, use a `git+ssh://` URL and a trusted host key.
OpenLit does not store a password in the URL. See [remote examples](EXAMPLES.md).

## Give an agent a separate checkout

On macOS or Linux, read the full `head` value from `lit status`, then run:

```sh
lit workspace create ../attempt-1 --base FULL_COMMIT_ID
cd ../attempt-1
lit workspace info
```

Use the actual full commit ID in place of `FULL_COMMIT_ID`. The base stays
recorded even after the attempt makes new commits. This separates working files;
it does not sandbox the agent process. See [security](SECURITY.md).

## When something fails

Read the JSON `code` and `message`. Do not assume that every error has a useful
suggestion. After an interrupted transaction, use `lit tx rollback` to restore
its checkpoint, then `lit verify`. Keep a backup if the repository cannot be
verified. See [examples](EXAMPLES.md) for transaction limits.
