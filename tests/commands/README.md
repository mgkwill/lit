# Command tests

These Rust tests exercise OpenLit's command implementations. The binary and
Rust import name remain `lit` after the package rename.

```sh
cargo test --locked --test command_tests -- --test-threads=1
```

Run serially because some tests change the process working directory or
shared environment. Use the helper fixtures and keep all writes in temporary
repositories. Do not infer coverage from an old test count: list current tests
with Cargo and inspect their assertions.

Test observable behavior, especially failed operations and preserved files.
For binary, hosted-broker, and platform checks, see [the testing guide](../../TESTING.md).
Tests added during recovery must use MIT-derived source or independently observed
black-box contracts, never post-MIT upstream test code.
