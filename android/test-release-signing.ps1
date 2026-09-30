[CmdletBinding()]
param([string]$KeytoolPath)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'The automatic DPAPI signing helper requires Windows.' }
$testRoot = Join-Path $env:LOCALAPPDATA '8mb.local-signing-tests'
$testDirectory = Join-Path $testRoot ([Guid]::NewGuid().ToString())
$testKeystore = Join-Path $testDirectory 'test-app-signing.jks'
$helper = Join-Path $PSScriptRoot 'configure-release-signing.ps1'
$arguments = @{ Automatic = $true; SkipGitHubSecrets = $true; KeystorePath = $testKeystore }
if ($KeytoolPath) { $arguments.KeytoolPath = $KeytoolPath }

try {
    & $helper @arguments
    $originalHash = (Get-FileHash -LiteralPath $testKeystore).Hash
    & $helper @arguments
    if ((Get-FileHash -LiteralPath $testKeystore).Hash -ne $originalHash) {
        throw 'Repeating setup changed the signing identity.'
    }
    $credentials = Import-Clixml -LiteralPath (Join-Path $testDirectory 'passwords.dpapi.xml')
    foreach ($secret in @($credentials.StorePassword, $credentials.KeyPassword)) {
        if ($secret -isnot [Security.SecureString] -or [Net.NetworkCredential]::new('', $secret).Password.Length -lt 40) {
            throw 'Secure password recovery failed.'
        }
    }
    if ((Get-Content -LiteralPath (Join-Path $testDirectory 'passwords.dpapi.xml') -Raw).Contains(
        [Net.NetworkCredential]::new('', $credentials.StorePassword).Password)) {
        throw 'Password recovery file contains a plaintext password.'
    }
    $acl = Get-Acl -LiteralPath $testDirectory
    $allowedSids = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18')
    if (-not $acl.AreAccessRulesProtected -or $acl.Access.Count -ne 2) { throw 'Private-directory ACL is too broad.' }
    foreach ($rule in $acl.Access) {
        if ($rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowedSids) {
            throw 'Unexpected identity can access private signing material.'
        }
    }
    $aliasRefused = $false
    try { & $helper @arguments -Alias 'wrong-identity' } catch {
        if ($_.Exception.Message -notmatch 'another key') { throw }
        $aliasRefused = $true
    }
    if (-not $aliasRefused -or (Get-FileHash -LiteralPath $testKeystore).Hash -ne $originalHash) {
        throw 'An alias mismatch did not preserve the existing signing key.'
    }
    $unknownDirectory = Join-Path $testDirectory 'unknown'
    New-Item -ItemType Directory -Path $unknownDirectory | Out-Null
    $unknownKeystore = Join-Path $unknownDirectory 'unknown.jks'
    [IO.File]::WriteAllBytes($unknownKeystore, [byte[]]@(1, 2, 3, 4))
    $unknownHash = (Get-FileHash -LiteralPath $unknownKeystore).Hash
    $unknownArguments = $arguments.Clone()
    $unknownArguments.KeystorePath = $unknownKeystore
    $unknownRefused = $false
    try { & $helper @unknownArguments } catch {
        if ($_.Exception.Message -notmatch 'existing keystore') { throw }
        $unknownRefused = $true
    }
    if (-not $unknownRefused -or (Get-FileHash -LiteralPath $unknownKeystore).Hash -ne $unknownHash) {
        throw 'An unknown existing keystore was not preserved.'
    }
    Write-Host 'PASS: offline setup, idempotence, encrypted password recovery, restricted ACL, alias guard, unknown-key guard.'
} finally {
    # Only this newly generated disposable test directory may be removed.
    $resolved = [IO.Path]::GetFullPath($testDirectory)
    $expectedPrefix = [IO.Path]::GetFullPath($testRoot).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($resolved) -notmatch '^[a-f0-9-]{36}$') {
        throw 'Refusing to clean up an unexpected signing-test path.'
    }
    if (Test-Path -LiteralPath $resolved) { Remove-Item -LiteralPath $resolved -Recurse -Force }
}
