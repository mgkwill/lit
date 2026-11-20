# Lit recovery release compatibility

GitHub Actions built and tested five release archives from the MIT recovery
source at `fabaf3f`. The checkout on the private qualification repository
matched that revision apart from three unrelated workflows. The release
publication job was skipped on the qualification branch.

| Target | Runner | Result |
| --- | --- | --- |
| Linux x86-64 | Ubuntu 22.04 | Passed on glibc 2.35 |
| Linux arm64 | Ubuntu 22.04 arm64 | Passed on glibc 2.35 |
| macOS Intel | macOS 15 Intel | Passed |
| macOS Apple Silicon | macOS 15 arm64 | Passed |
| Windows x86-64 | Windows Server 2022 | Passed |

Each runner extracted the archive and used the packaged binary for batch
rollback, MCP over stdio and HTTP, and native, file, and smart HTTP remote
regressions. Each archive included the original MIT license. Both Linux
executables had glibc 2.34 as their highest required symbol version. The
same Linux archives passed batch, MCP, and authenticated Git HTTPS and SSH
checks on host-a, host-e, and host-f. Windows strict sandbox mode refused
before launching a payload when a confinement backend was unavailable.

The first Windows build passed batch and MCP and failed a native remote
push at a drive-letter file URL with Windows error 123. The follow-up
decodes local URLs into platform paths and honors an explicit absolute
home directory for LAN configuration. The regression now also covers an
encoded space in a native repository path. [Initial Windows job](https://github.com/mgkwill/lit-release-qualification/actions/runs/44784953384/job/136457212725)

The protected merge-queue sandbox rejected the qualification branch creation.
The dedicated private repository ran the release workflow. The sandbox
main ref and its required GitHub Actions check, empty bypass roster, and
merge queue matched before and after. No post-MIT upstream code was used
as input. [Passing Actions run](https://github.com/mgkwill/lit-release-qualification/actions/runs/44786290284)

## Installation

Verify the selected archive against `SHA256SUMS`, unpack it, and run
`lit --version` or `lit.exe --version`. This candidate identifies its
source commit in `SOURCE-COMMIT` and reports the inherited version
`lit 1.0.0`. Keep the bundled `LICENSE` with the binary. Ordinary Git
remotes use the system Git executable, and SSH Git remotes also use SSH.

- **Linux** needs glibc and the system GCC support library
  (`libgcc_s.so.1`). Both Ubuntu 22.04 architectures ran the packaged
  binary on glibc 2.35.
- **macOS** uses system libraries on both architectures. The binaries'
  load commands record deployment targets 10.12 for Intel and 11.0 for
  Apple Silicon. Both hosted runtime tests used macOS 15. An additional
  Apple Silicon smoke run passed with the downloaded binary copied into
  an empty user-writable install directory whose path contained spaces.
- **Windows** imports `VCRUNTIME140.dll` and Universal CRT APIs. On a
  machine without the v14 runtime, install Microsoft's x64 Visual C++
  Redistributable. The runtime test used the Windows Server 2022 hosted
  runner. [Microsoft runtime downloads](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist/)

[Archive digests and host test results](2026-11-20-compatibility.json)
