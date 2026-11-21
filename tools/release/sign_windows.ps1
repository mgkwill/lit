# The certificate's private key must already be accessible through Windows' provider.
# This script does not import keys or select a publisher automatically.
param(
    [Parameter(Mandatory=$true)][string]$Binary,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Fa-f0-9]{40}$')][string]$Thumbprint,
    [ValidateSet('Sign','Verify')][string]$Mode = 'Verify',
    [string]$TimestampUrl
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Binary = (Resolve-Path -LiteralPath $Binary).Path
$SignTool = (Get-Command signtool.exe -ErrorAction Stop).Source
if ($Mode -eq 'Sign') {
    if ($TimestampUrl -notmatch '^https://[^\s]+$') { throw 'An HTTPS RFC 3161 timestamp URL is required' }
    $Cert = Get-Item -LiteralPath "Cert:\CurrentUser\My\$Thumbprint"
    if (-not $Cert.HasPrivateKey) { throw 'The selected publisher has no accessible private key' }
    & $SignTool sign /sha1 $Thumbprint /s My /fd SHA256 /tr $TimestampUrl /td SHA256 $Binary
    if ($LASTEXITCODE -ne 0) { throw 'Authenticode signing failed' }
}
& $SignTool verify /pa /all /tw $Binary
if ($LASTEXITCODE -ne 0) { throw 'Authenticode policy or timestamp verification failed' }
$Signature = Get-AuthenticodeSignature -LiteralPath $Binary
if ($Signature.Status -ne 'Valid') { throw "Invalid Authenticode status: $($Signature.Status)" }
if ($Signature.SignerCertificate.Thumbprint -ne $Thumbprint) { throw 'Unexpected Authenticode publisher certificate' }
if ($null -eq $Signature.TimeStamperCertificate) { throw 'No trusted timestamp was found' }
