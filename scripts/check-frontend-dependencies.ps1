[CmdletBinding()]
param(
    [string]$RepositoryRoot
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$Root = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepositoryRoot).Path)
$Frontend = Join-Path $Root 'frontend'
$PackagePath = Join-Path $Frontend 'package.json'
$LockPath = Join-Path $Frontend 'package-lock.json'
$InstalledCookiePath = Join-Path $Frontend 'node_modules\cookie\package.json'

foreach ($path in @($PackagePath, $LockPath, $InstalledCookiePath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required frontend dependency file is missing: $path"
    }
}

$package = Get-Content -LiteralPath $PackagePath -Raw | ConvertFrom-Json
$override = $null
if ($null -ne $package.overrides) {
    $override = [string]$package.overrides.cookie
}
if ([string]::IsNullOrWhiteSpace($override)) {
    throw 'frontend/package.json must define an npm override for cookie.'
}

function Get-VersionFromText {
    param([string]$Value, [string]$Label)
    $match = [regex]::Match($Value, '(?<version>\d+(?:\.\d+){1,3})')
    if (-not $match.Success) {
        throw "Could not read a version from $Label ('$Value')."
    }
    return [version]$match.Groups['version'].Value
}

$minimumPatchedVersion = [version]'0.7.0'
$overrideVersion = Get-VersionFromText $override 'cookie override'
if ($overrideVersion -lt $minimumPatchedVersion) {
    throw "cookie override '$override' is older than the patched minimum 0.7.0."
}

$lockText = [System.IO.File]::ReadAllText($LockPath)
$lockMatch = [regex]::Match(
    $lockText,
    '(?ms)"node_modules/cookie"\s*:\s*\{\s*"version"\s*:\s*"(?<version>[^"]+)"'
)
if (-not $lockMatch.Success) {
    throw 'frontend/package-lock.json has no node_modules/cookie entry.'
}
$lockVersion = Get-VersionFromText $lockMatch.Groups['version'].Value 'package-lock cookie version'
if ($lockVersion -lt $minimumPatchedVersion) {
    throw "package-lock.json resolves cookie $lockVersion; patched minimum is 0.7.0."
}

$installed = Get-Content -LiteralPath $InstalledCookiePath -Raw | ConvertFrom-Json
$installedVersion = Get-VersionFromText ([string]$installed.version) 'installed cookie version'
if ($installedVersion -lt $minimumPatchedVersion) {
    throw "node_modules resolves cookie $installedVersion; patched minimum is 0.7.0."
}

Write-Output "FRONTEND_COOKIE_OVERRIDE=$override"
Write-Output "FRONTEND_COOKIE_LOCK_VERSION=$lockVersion"
Write-Output "FRONTEND_COOKIE_INSTALLED_VERSION=$installedVersion"
Write-Output 'FRONTEND_DEPENDENCY_SECURITY_CHECK=PASSED'
