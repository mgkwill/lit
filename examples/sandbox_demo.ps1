# Show strict sandbox refusal on Windows, where OpenLit has no strict backend.
[CmdletBinding()]
param([string]$LitBinary = "$PSScriptRoot\..\target\release\lit.exe")
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$LitBinary = (Resolve-Path -LiteralPath $LitBinary).Path
$demoDir = Join-Path ([System.IO.Path]::GetTempPath()) ('openlit-sandbox-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $demoDir | Out-Null
Push-Location $demoDir
try {
    & $LitBinary init | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Repository initialization failed.' }
    & $LitBinary sandbox init demo | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Sandbox initialization failed.' }
    $errorFile = Join-Path $demoDir 'strict-error.json'
    & $LitBinary sandbox run --strict demo -- cmd /c 'echo launched > marker' 2> $errorFile
    if ($LASTEXITCODE -eq 0) { throw 'Strict mode unexpectedly launched the command.' }
    $response = Get-Content -LiteralPath $errorFile -Raw | ConvertFrom-Json
    if ($response.error.code -ne 'SANDBOX_UNAVAILABLE') { throw 'Unexpected refusal reason.' }
    if (Test-Path -LiteralPath '.lit/sandboxes/demo/marker') { throw 'The payload ran.' }
    Write-Host 'Strict mode refused before launching the payload, as expected on Windows.'
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $demoDir -Recurse -Force
}
