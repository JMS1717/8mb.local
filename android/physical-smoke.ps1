[CmdletBinding()]
param(
    [string]$ApkPath = "$PSScriptRoot\app\build\outputs\apk\debug\app-debug.apk",
    [string]$ReportPath = "$PSScriptRoot\physical-codec-report.json"
)
$ErrorActionPreference = 'Stop'
$package = 'com.jms1717.eightmblocal'
adb get-state | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Connect one Android device with USB debugging enabled.' }
adb install -r $ApkPath | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'APK installation failed.' }
adb shell am force-stop $package | Out-Null
adb shell monkey -p $package -c android.intent.category.LAUNCHER 1 | Out-Null
Read-Host 'On the phone: choose a short video, compress it, confirm the result plays, then press Enter here'
$report = adb exec-out run-as $package cat files/last-codec-report.json
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($report | Out-String))) {
    throw 'No codec report found. Complete one compression in the debug app first.'
}
[IO.File]::WriteAllText([IO.Path]::GetFullPath($ReportPath), ($report | Out-String), [Text.UTF8Encoding]::new($false))
$json = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
if (-not $json.actual_encoder) { throw 'Report is missing actual_encoder.' }
if ($json.hardware_used -ne $true) {
    throw "The completed Android export did not use a hardware MediaCodec. actual_encoder=$($json.actual_encoder), fallback=$($json.fallback_occurred)"
}
Write-Host "Encoder: $($json.actual_encoder)"
Write-Host "Hardware used: $($json.hardware_used)"
Write-Host "Fallback occurred: $($json.fallback_occurred)"
Write-Host "Saved report: $([IO.Path]::GetFullPath($ReportPath))"
