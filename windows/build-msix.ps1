[CmdletBinding()]
param(
    [string]$ExePath,
    [string]$OutputPath,
    [string]$PackageIdentityName = 'JMS1717.8mblocal.Dev',
    [string]$Publisher = 'CN=JMS1717 Development',
    [string]$PublisherDisplayName = 'JMS1717',
    [string]$Version = '138.0.0.0',
    [string]$MakeAppxPath,
    [switch]$StoreSubmission
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'brand-assets.ps1')

if (-not $ExePath) {
    $ExePath = Join-Path $RepoRoot 'dist\8mblocal.exe'
}
if (-not $OutputPath) {
    $OutputPath = Join-Path $RepoRoot "dist\8mblocal_$($Version)_x64.msix"
}

$ExePath = [IO.Path]::GetFullPath($ExePath)
$OutputPath = [IO.Path]::GetFullPath($OutputPath)
if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "Portable executable not found: $ExePath. Run windows\build.ps1 first."
}
if ($PackageIdentityName -notmatch '^[A-Za-z0-9.-]{3,50}$') {
    throw 'PackageIdentityName must be 3-50 characters using letters, numbers, periods, or hyphens.'
}
if ($Version -notmatch '^\d{1,5}\.\d{1,5}\.\d{1,5}\.\d{1,5}$') {
    throw 'Version must contain four numeric components, for example 138.0.0.0.'
}
if ($StoreSubmission -and (
    $PackageIdentityName -eq 'JMS1717.8mblocal.Dev' -or
    $Publisher -eq 'CN=JMS1717 Development'
)) {
    throw 'StoreSubmission requires the exact Package Identity Name and Publisher values assigned by Partner Center.'
}

function Resolve-MakeAppx {
    if ($MakeAppxPath) {
        $resolved = Resolve-Path -LiteralPath $MakeAppxPath -ErrorAction Stop
        return $resolved.Path
    }

    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    if (Test-Path -LiteralPath $kitsRoot) {
        $installed = Get-ChildItem -LiteralPath $kitsRoot -Filter MakeAppx.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\MakeAppx\.exe$' } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($installed) {
            return $installed.FullName
        }
    }

    # Use only the small command-line SDK package when the full Windows SDK is
    # absent. This avoids installing a system-wide SDK just to create an MSIX.
    $sdkVersion = '10.0.26100.8249'
    $cacheRoot = Join-Path $env:LOCALAPPDATA "8mb.local-build-cache\WindowsSdkBuildTools\$sdkVersion"
    $cached = Get-ChildItem -LiteralPath $cacheRoot -Filter MakeAppx.exe -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\MakeAppx\.exe$' } |
        Select-Object -First 1
    if ($cached) {
        return $cached.FullName
    }

    New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null
    $archive = Join-Path $env:TEMP "microsoft.windows.sdk.buildtools.$sdkVersion.zip"
    try {
        Write-Host "Downloading Microsoft.Windows.SDK.BuildTools $sdkVersion from NuGet..."
        Invoke-WebRequest `
            -Uri "https://api.nuget.org/v3-flatcontainer/microsoft.windows.sdk.buildtools/$sdkVersion/microsoft.windows.sdk.buildtools.$sdkVersion.nupkg" `
            -OutFile $archive
        Expand-Archive -LiteralPath $archive -DestinationPath $cacheRoot -Force
    } finally {
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    }

    $downloaded = Get-ChildItem -LiteralPath $cacheRoot -Filter MakeAppx.exe -Recurse |
        Where-Object { $_.FullName -match '\\x64\\MakeAppx\.exe$' } |
        Select-Object -First 1
    if (-not $downloaded) {
        throw 'Microsoft.Windows.SDK.BuildTools did not contain x64\MakeAppx.exe.'
    }
    return $downloaded.FullName
}

$makeAppx = Resolve-MakeAppx
$stage = Join-Path $env:TEMP ('8mblocal-msix-' + [guid]::NewGuid().ToString('N'))
$stageFull = [IO.Path]::GetFullPath($stage)
$tempFull = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
if (-not $stageFull.StartsWith($tempFull, [StringComparison]::OrdinalIgnoreCase) -or
    -not ([IO.Path]::GetFileName($stageFull)).StartsWith('8mblocal-msix-')) {
    throw "Refusing to use unsafe staging path: $stageFull"
}

try {
    $assets = Join-Path $stageFull 'Assets'
    New-Item -ItemType Directory -Force -Path $assets | Out-Null
    Copy-Item -LiteralPath $ExePath -Destination (Join-Path $stageFull '8mblocal.exe')

    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'msix\AppxManifest.xml.template') -Raw
    $manifest = $manifest.Replace('__IDENTITY_NAME__', [Security.SecurityElement]::Escape($PackageIdentityName))
    $manifest = $manifest.Replace('__PUBLISHER__', [Security.SecurityElement]::Escape($Publisher))
    $manifest = $manifest.Replace('__PUBLISHER_DISPLAY_NAME__', [Security.SecurityElement]::Escape($PublisherDisplayName))
    $manifest = $manifest.Replace('__VERSION__', [Security.SecurityElement]::Escape($Version))
    [IO.File]::WriteAllText((Join-Path $stageFull 'AppxManifest.xml'), $manifest, (New-Object Text.UTF8Encoding($false)))

    Write-8mbLocalBrandPng (Join-Path $assets 'StoreLogo.png') 50 50
    Write-8mbLocalBrandPng (Join-Path $assets 'Square44x44Logo.png') 44 44
    Write-8mbLocalBrandPng (Join-Path $assets 'Square150x150Logo.png') 150 150
    Write-8mbLocalBrandPng (Join-Path $assets 'Wide310x150Logo.png') 310 150

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
    & $makeAppx pack /o /d $stageFull /p $OutputPath
    if ($LASTEXITCODE -ne 0) {
        throw "MakeAppx failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $OutputPath -PathType Leaf)) {
        throw "MakeAppx reported success but did not create $OutputPath"
    }
    if ($StoreSubmission) {
        Write-Host "Built unsigned Store submission package: $OutputPath"
        Write-Host 'Microsoft signs this MSIX after Partner Center certification.'
    } else {
        Write-Host "Built unsigned development MSIX: $OutputPath"
        Write-Warning 'This package uses development identity values and is not Store-submittable.'
    }
} finally {
    if (Test-Path -LiteralPath $stageFull) {
        Remove-Item -LiteralPath $stageFull -Recurse -Force
    }
}
