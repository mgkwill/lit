# Install OpenLit

[OpenLit 0.1.1 downloads](https://github.com/mgkwill/lit/releases/tag/v0.1.1) are
available for Linux, macOS, and Windows. Run OpenLit with `lit` (`lit.exe` on
Windows). There is no crates.io package; use a verified release archive
or build this checkout.

## Build this checkout

Install Rust with Cargo, a platform C compiler, and Git. Then run:

```sh
cargo build --locked --release
cargo install --locked --path . --bin lit
lit --version
```

Expected version: `lit 0.1.1`, for both the published archives and this checkout.
On Windows, use PowerShell with the Visual Studio C++ build tools installed.
`cargo install --path .` installs the local OpenLit package from this checkout.

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

## Release archives

Archives use `openlit-VERSION-TARGET.tar.gz` on Unix and `.zip` on Windows.
Inside is `lit` or `lit.exe`, plus `LICENSE`, `NOTICE.md`, and
`THIRD_PARTY_NOTICES.txt`. Keep the license and notices with the installed binary.
Use the published release assets; separate CI qualification artifacts use
disposable keys and are not publisher-signed releases.

Download the archive for your platform and its matching `.minisig` file from
the release page. Obtain the public key from the
[reviewed source](https://github.com/mgkwill/lit/blob/777acec2002a69b2429c91a58f9f8371f12724bd/keys/openlit.pub).
Verify the signature before unpacking:

```sh
minisign -V -H -p /path/to/trusted/openlit.pub \
  -m openlit-0.1.1-aarch64-apple-darwin.tar.gz
```

Use the archive for your platform and keep its matching `.minisig` beside it.
A checksum alone does not authenticate a download. See
[full release verification](RELEASE_CREDENTIALS.md).

## Platforms

| Target | OpenLit 0.1.1 release qualification | Requirements |
| --- | --- | --- |
| Linux x86-64 | Ubuntu 22.04, glibc 2.35 | glibc 2.35 or newer and libgcc_s |
| Linux arm64 | Ubuntu 22.04 arm64, glibc 2.35 | glibc 2.35 or newer and libgcc_s |
| macOS Intel | macOS 15 Intel | System libraries |
| macOS Apple Silicon | macOS 15 arm64 and local Mac | System libraries |
| Windows x86-64 | Windows Server 2022 | Universal CRT and Visual C++ v14 runtime |

The Linux binaries require symbols through glibc 2.34, but the tested
runtime floor is 2.35. A macOS deployment-target field is not proof that an older
macOS release works. Windows desktop versions and Windows arm64 are not covered
by that matrix. See the [exact release qualification evidence](../audits/release-0.1.1-2026-11-22.md).

If Windows reports a missing `VCRUNTIME140.dll`, install the
[Microsoft x64 Visual C++ Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist/).
Git remotes need the system Git executable; SSH remotes also need SSH.

Minisign verifies the download but does not supply Apple notarization or
Windows publisher trust; OS warnings may remain.
