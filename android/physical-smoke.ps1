[CmdletBinding()]
param(
    [string]$Serial,
    [switch]$SkipBuild,
    [string]$ReportPath = "$PSScriptRoot\physical-codec-report.json"
)
$ErrorActionPreference = 'Stop'
$package = 'com.jms1717.eightmblocal'
$runner = "$package.test/androidx.test.runner.AndroidJUnitRunner"
$testClass = "$package.PhysicalHardwareCompressionTest"
$audioTestClass = "$package.PhysicalAudioExtractionTest"

$adbCommand = Get-Command adb -ErrorAction SilentlyContinue
if ($null -eq $adbCommand) {
    $adbCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'),
        (Join-Path $env:TEMP 'android-sdk-8mblocal-20260823\platform-tools\adb.exe')
    )
    $adbPath = $adbCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $adbPath) { throw 'ADB was not found. Install Android SDK platform-tools or add adb to PATH.' }
} else {
    $adbPath = $adbCommand.Source
}

if (-not $Serial) {
    $deviceLines = @(& $adbPath devices | Select-Object -Skip 1 | Where-Object { $_ -match '^([^\s]+)\s+device$' })
    if ($deviceLines.Count -ne 1) {
        throw "Expected exactly one connected ADB transport; found $($deviceLines.Count). Pass -Serial explicitly."
    }
    $Serial = ([regex]::Match($deviceLines[0], '^([^\s]+)')).Groups[1].Value
}

& $adbPath -s $Serial get-state | Out-Null
if ($LASTEXITCODE -ne 0) { throw "ADB device '$Serial' is unavailable." }
$abi = (& $adbPath -s $Serial shell getprop ro.product.cpu.abi | Out-String).Trim()
if ($abi -notmatch 'arm64|aarch64') { throw "Run this proof on a physical ARM64 Android device; found '$abi'." }

if (-not $SkipBuild) {
    Push-Location $PSScriptRoot
    try {
        & .\gradlew.bat assembleDebug assembleDebugAndroidTest
        if ($LASTEXITCODE -ne 0) { throw "Android test build failed with exit code $LASTEXITCODE." }
    } finally {
        Pop-Location
    }
}

$appApk = Join-Path $PSScriptRoot 'app\build\outputs\apk\debug\app-debug.apk'
$testApk = Join-Path $PSScriptRoot 'app\build\outputs\apk\androidTest\debug\app-debug-androidTest.apk'
foreach ($apk in @($appApk, $testApk)) {
    if (-not (Test-Path -LiteralPath $apk -PathType Leaf)) { throw "APK is missing: $apk" }
    & $adbPath -s $Serial install -r $apk | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "APK installation failed: $apk" }
}

foreach ($class in @($audioTestClass, $testClass)) {
    $instrumentation = & $adbPath -s $Serial shell am instrument -w -r -e class $class $runner 2>&1 | Out-String
    $instrumentation | Write-Host
    if ($LASTEXITCODE -ne 0 -or $instrumentation -notmatch 'OK \(1 test\)') {
        throw "Automated physical test failed: $class"
    }
}

$report = & $adbPath -s $Serial exec-out run-as $package cat files/last-codec-report.json
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($report | Out-String))) {
    throw 'The automated test completed without a codec telemetry report.'
}
[IO.File]::WriteAllText(
    [IO.Path]::GetFullPath($ReportPath),
    ($report | Out-String),
    [Text.UTF8Encoding]::new($false)
)
$json = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
if (-not $json.actual_encoder -or $json.hardware_used -ne $true) {
    throw "Hardware use was not proven. actual_encoder=$($json.actual_encoder), hardware=$($json.hardware_used), fallback=$($json.fallback_occurred)"
}
Write-Host "PASS ARM64 Android hardware compression: $($json.actual_encoder)"
Write-Host 'PASS Android desktop-default Opus audio extraction'
Write-Host "Report: $([IO.Path]::GetFullPath($ReportPath))"
