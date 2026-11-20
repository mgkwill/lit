# Build and install OpenLit from this checkout. The command remains lit.exe.
[CmdletBinding()]
param([string]$InstallRoot)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw 'Cargo is required. Install Rust and the C++ build tools, then try again.'
}
$installArgs = @('install', '--locked', '--path', $PSScriptRoot, '--bin', 'lit')
if ($InstallRoot) {
    $installArgs += @('--root', $InstallRoot)
}
& cargo @installArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
Write-Host 'OpenLit is installed. The executable is lit.exe in your Cargo installation directory.'
Write-Host 'Run lit --version from that directory. See docs/INSTALL.md for setup details.'
