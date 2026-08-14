[CmdletBinding()]
param(
    [switch]$TestInstaller,
    [switch]$TestNativeWindow
)

$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath($PSScriptRoot)
Set-Location -LiteralPath $RepoRoot

function Require-Command {
    param([string]$Name, [string]$InstallHint)
    if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found. $InstallHint"
    }
}

Require-Command 'npm' 'Install Node.js LTS, then reopen PowerShell.'
$pythonFound = $false
$py = Get-Command 'py' -ErrorAction SilentlyContinue
if ($null -ne $py) {
    foreach ($version in @('3.11', '3.12', '3.13')) {
        & $py.Source "-$version" '-c' 'import sys' 2>$null
        if ($LASTEXITCODE -eq 0) { $pythonFound = $true; break }
    }
}
if (-not $pythonFound -and $null -ne (Get-Command 'python' -ErrorAction SilentlyContinue)) {
    $pythonFound = $true
}
if (-not $pythonFound) {
    throw 'Python 3.11, 3.12, or 3.13 was not found. Install Python 3.11 and reopen PowerShell.'
}

Write-Host 'Building the portable Windows executable...'
& (Join-Path $RepoRoot 'windows\build.ps1')
if ($LASTEXITCODE -ne 0) { throw "windows\build.ps1 failed with exit code $LASTEXITCODE" }

$exe = Join-Path $RepoRoot 'dist\8mblocal.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Portable executable was not created: $exe" }

$smokeScript = Join-Path $RepoRoot 'windows\test-release.ps1'
if ($TestInstaller) {
    $installer = Join-Path $RepoRoot 'dist\8mblocal-Setup.exe'
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw 'Installer test requested, but Inno Setup did not create dist\8mblocal-Setup.exe.'
    }
    if ($TestNativeWindow) {
        & $smokeScript -ExePath $exe -Install -TestNativeWindow
    } else {
        & $smokeScript -ExePath $exe -Install
    }
} elseif ($TestNativeWindow) {
    & $smokeScript -ExePath $exe -TestNativeWindow
} else {
    & $smokeScript -ExePath $exe
}
if ($LASTEXITCODE -ne 0) { throw "windows\test-release.ps1 failed with exit code $LASTEXITCODE" }

Write-Host 'Windows release smoke test passed.'

Write-Host "Windows test build passed: $exe"
