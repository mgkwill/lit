# Install OpenLit

OpenLit 0.1.0 is being prepared. No public release or registry package has been
published by this work. The command stays `lit`, and Windows uses `lit.exe`.

## Build this checkout

Install Rust with Cargo, a platform C compiler, and Git. Then run:

```sh
cargo build --locked --release
cargo install --locked --path . --bin lit
lit --version
```

Expected version: `lit 0.1.0`. On Windows, use PowerShell with the Visual Studio
C++ build tools installed. `cargo install --path .` installs the local OpenLit
package. It does not download the upstream Lit package.

The build path above assumes Cargo's default target directory. If you set
`CARGO_TARGET_DIR` or a Cargo `target-dir`, use that directory instead of `target`.

For a source install, `./install.sh --root /your/install/directory` on Unix or
`./install.ps1 -InstallRoot C:\your\install\directory` in PowerShell wraps the
locked Cargo install. Both scripts use their own checkout even when called from
another directory. They leave shell profiles, telemetry, and existing OpenLit
configuration alone. They do not force-replace another package's `lit` binary.

If another `lit` executable is already on your PATH, check which one your shell
runs before replacing it. On Unix use `command -v lit`; in PowerShell use
`Get-Command lit`. Keep your old binary if you need to compare versions.

## Candidate archives

Archives use `openlit-VERSION-TARGET.tar.gz` on Unix and `.zip` on Windows.
Inside is `lit` or `lit.exe`, plus the original `LICENSE` and `THIRD_PARTY_NOTICES.txt`. Qualification builds
include their branch name in place of VERSION so they cannot be mistaken for a
published release. Keep the license with the installed binary.

Once an approved release is available, verify its build provenance and checksum
before unpacking. A checksum alone detects changed bytes; it does not establish
who built them. See [release signing](RELEASING.md).

## Platforms

| Target | Runtime evidence before the 0.1.0 rename | Requirements |
| --- | --- | --- |
| Linux x86-64 | Ubuntu 22.04, glibc 2.35; host-a | glibc 2.35 or newer and libgcc_s |
| Linux arm64 | Ubuntu 22.04 arm64, glibc 2.35; host-e and host-f | glibc 2.35 or newer and libgcc_s |
| macOS Intel | macOS 15 Intel | System libraries |
| macOS Apple Silicon | macOS 15 arm64 and local Mac | System libraries |
| Windows x86-64 | Windows Server 2022 | Universal CRT and Visual C++ v14 runtime |

The earlier Linux binaries required symbols through glibc 2.34, but the tested
runtime floor is 2.35. A macOS deployment-target field is not proof that an older
macOS release works. Windows desktop versions and Windows arm64 are not covered
by that matrix. [Exact earlier evidence](../tests/fixtures/release-qualification/2026-11-20-compatibility.md)

If Windows reports a missing `VCRUNTIME140.dll`, install the
[Microsoft x64 Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist/).
Git remotes need the system Git executable; SSH remotes also need SSH.

Do not disable Gatekeeper or other platform checks to make an unsigned candidate
look like a signed release. Public signing and clean-download testing remain
release requirements.
