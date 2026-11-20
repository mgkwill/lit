# Work without network remotes

OpenLit has an airgap setting that rejects network transports and allows local
file operations. It controls OpenLit's own transport selection. It does not
turn off networking for the operating system or every process it starts.

## One command

```sh
lit --airgapped clone file:///absolute/path/to/repository copy
```

Use a real local path. Check the resulting repository with `lit verify` before
using it as your only copy.

## Persistent transport settings

```sh
lit config get airgap.enabled
lit config set airgap.enabled true
lit config get airgap.strict_mode
```

`airgap.strict_mode` further restricts transport choices, including network
shares. Read `lit config show` to inspect effective network and airgap settings.
This setting is separate from strict process sandboxing.

A filesystem path can point at a mounted network volume. A file URL alone does
not prove that the storage is physically disconnected. Use trusted media and
host policy when physical isolation matters. Do not automatically run files
copied from another repository.

## Confine a process

Use `lit sandbox --help` for sandbox commands and select strict mode for enforced
filesystem and network restrictions. A strict run must refuse before executing
its payload if isolation cannot be prepared. Default mode does not provide those
guarantees. Windows has no strict backend in 0.1.0.

See [security](SECURITY.md) and [deployment](DEPLOYMENT.md) for the boundaries
between transport policy, process isolation, and repository migration.
