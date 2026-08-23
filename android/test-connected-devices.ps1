[CmdletBinding()]
param(
    [int]$MinimumDevices = 1,
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$adbCommand = Get-Command adb -ErrorAction SilentlyContinue
if ($null -eq $adbCommand) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
        (Join-Path $env:TEMP 'android-sdk-8mblocal-20260823\platform-tools\adb.exe')
    )
    $adb = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
} else { $adb = $adbCommand.Source }
if (-not $adb) { throw 'ADB was not found.' }

$candidateSerials = @(& $adb devices | Select-Object -Skip 1 | ForEach-Object {
    if ($_ -match '^([^\s]+)\s+device$') { $matches[1] }
})
$seenDeviceIds = @{}
$serials = @($candidateSerials | Where-Object {
    $serial = $_
    $deviceId = ((& $adb -s $serial shell getprop ro.serialno) | Out-String).Trim()
    if (-not $deviceId) { $deviceId = $serial }
    if ($seenDeviceIds.ContainsKey($deviceId)) { return $false }
    $seenDeviceIds[$deviceId] = $true
    return $true
})
if ($serials.Count -lt $MinimumDevices) {
    throw "Expected at least $MinimumDevices authorized ADB devices; found $($serials.Count): $($serials -join ', ')"
}

if (-not $SkipBuild) {
    Push-Location $PSScriptRoot
    try {
        & .\gradlew.bat assembleDebug assembleDebugAndroidTest
        if ($LASTEXITCODE -ne 0) { throw 'Android build failed.' }
    } finally { Pop-Location }
}

$reportDirectory = Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\device-tests'
New-Item -ItemType Directory -Force -Path $reportDirectory | Out-Null
foreach ($serial in $serials) {
    $model = ((& $adb -s $serial shell getprop ro.product.model) | Out-String).Trim()
    $soc = ((& $adb -s $serial shell getprop ro.soc.model) | Out-String).Trim()
    $safeName = (($model + '-' + $soc) -replace '[^A-Za-z0-9._-]', '-').Trim('-')
    Write-Host "Testing $serial ($model, $soc)"
    & (Join-Path $PSScriptRoot 'physical-smoke.ps1') `
        -Serial $serial `
        -SkipBuild `
        -ReportPath (Join-Path $reportDirectory "$safeName.json")
    if ($LASTEXITCODE -ne 0) { throw "Physical smoke failed on $serial." }
}

Write-Host "PASS all $($serials.Count) connected Android devices"
