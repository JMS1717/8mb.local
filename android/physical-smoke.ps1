[CmdletBinding()]
param(
    [string]$Serial,
    [switch]$SkipBuild,
    [string]$ReportPath = "$PSScriptRoot\physical-codec-report.json",
    [ValidateSet('debug', 'release')][string]$BuildType = 'debug',
    [string]$AppApk,
    [string]$TestApk,
    [string]$AdbPath
)
$ErrorActionPreference = 'Stop'
$package = 'com.jms1717.eightmblocal'
$runner = "$package.test/androidx.test.runner.AndroidJUnitRunner"
$testClass = "$package.PhysicalHardwareCompressionTest"
$audioTestClass = "$package.PhysicalAudioExtractionTest"
$inventoryTestClass = "$package.PhysicalCodecInventoryTest"
$uiTestClass = "$package.MainActivityTest"
$listingScreenshotTestClass = "$package.StoreListingScreenshotTest"

$adbCommand = Get-Command adb -ErrorAction SilentlyContinue
if ($AdbPath) {
    $adbPath = (Resolve-Path -LiteralPath $AdbPath).Path
} elseif ($null -eq $adbCommand) {
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
$sdk = [int]((& $adbPath -s $Serial shell getprop ro.build.version.sdk | Out-String).Trim())
if ($sdk -lt 29) { throw "The automatic camera-roll proof requires Android 10/API 29+; found API $sdk." }

if (-not $SkipBuild) {
    Push-Location $PSScriptRoot
    try {
        $variant = (Get-Culture).TextInfo.ToTitleCase($BuildType)
        & .\gradlew.bat "-PinstrumentedBuildType=$BuildType" "assemble$variant" "assemble${variant}AndroidTest"
        if ($LASTEXITCODE -ne 0) { throw "Android test build failed with exit code $LASTEXITCODE." }
    } finally {
        Pop-Location
    }
}

if (-not $AppApk) { $AppApk = Join-Path $PSScriptRoot "app\build\outputs\apk\$BuildType\app-$BuildType.apk" }
if (-not $TestApk) { $TestApk = Join-Path $PSScriptRoot "app\build\outputs\apk\androidTest\$BuildType\app-$BuildType-androidTest.apk" }
if (-not (Test-Path -LiteralPath $AppApk)) { throw "APK is missing: $AppApk" }
$testedApkHash = (Get-FileHash -LiteralPath $AppApk -Algorithm SHA256).Hash.ToLowerInvariant()
foreach ($apk in @($appApk, $testApk)) {
    if (-not (Test-Path -LiteralPath $apk -PathType Leaf)) { throw "APK is missing: $apk" }
    & $adbPath -s $Serial install -r $apk | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "APK installation failed: $apk" }
}

# A fresh install can place Android's notification-permission activity over
# MainActivity before Compose attaches. The shell grant keeps this fully
# unattended and also ensures the foreground-service notification is visible.
if ($sdk -ge 33) {
    & $adbPath -s $Serial shell pm grant $package android.permission.POST_NOTIFICATIONS
    if ($LASTEXITCODE -ne 0) { throw 'Could not grant notification permission for the physical smoke test.' }
}
& $adbPath -s $Serial shell input keyevent KEYCODE_WAKEUP | Out-Null
& $adbPath -s $Serial shell wm dismiss-keyguard | Out-Null
& $adbPath -s $Serial shell input keyevent KEYCODE_HOME | Out-Null
& $adbPath -s $Serial shell am force-stop $package | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not reset the app before the physical smoke test.' }

$telemetry = @{}
foreach ($class in @($uiTestClass, $inventoryTestClass, $audioTestClass, $testClass, $listingScreenshotTestClass)) {
    $instrumentation = & $adbPath -s $Serial shell am instrument -w -r -e class $class $runner 2>&1 | Out-String
    $instrumentation | Write-Host
    if ($LASTEXITCODE -ne 0 -or $instrumentation -notmatch 'OK \(1 test\)' -or
        $instrumentation -match 'INSTRUMENTATION_STATUS_CODE: -[234]') {
        throw "Automated physical test failed: $class"
    }
    foreach ($name in @('eightmb_codec_report', 'eightmb_codec_inventory')) {
        $match = [regex]::Match($instrumentation, "(?m)^INSTRUMENTATION_STATUS: ${name}=(\{[^\r\n]+\})")
        if ($match.Success) { $telemetry[$name] = $match.Groups[1].Value }
    }
}

if (-not $telemetry['eightmb_codec_report']) {
    throw 'The automated test completed without a codec telemetry report.'
}
if (-not $telemetry['eightmb_codec_inventory']) {
    throw 'The automated test completed without a working codec inventory.'
}
$json = $telemetry['eightmb_codec_report'] | ConvertFrom-Json
$inventoryJson = $telemetry['eightmb_codec_inventory'] | ConvertFrom-Json
$json | Add-Member -NotePropertyName codec_inventory -NotePropertyValue $inventoryJson
$json | Add-Member -NotePropertyName tested_apk_sha256 -NotePropertyValue $testedApkHash
$json | Add-Member -NotePropertyName build_type -NotePropertyValue $BuildType
if ((Get-FileHash -LiteralPath $AppApk -Algorithm SHA256).Hash.ToLowerInvariant() -ne $testedApkHash) {
    throw 'The APK changed during testing.'
}
[IO.File]::WriteAllText(
    [IO.Path]::GetFullPath($ReportPath),
    ($json | ConvertTo-Json -Depth 12),
    [Text.UTF8Encoding]::new($false)
)
if (-not $json.actual_encoder -or $json.hardware_used -ne $true) {
    throw "Hardware use was not proven. actual_encoder=$($json.actual_encoder), hardware=$($json.hardware_used), fallback=$($json.fallback_occurred)"
}
Write-Host "PASS ARM64 Android foreground-service compression to camera roll: $($json.actual_encoder)"
Write-Host 'PASS Android desktop-default Opus audio extraction'
Write-Host 'PASS Android main workflow UI and Play-listing screenshot capture'
Write-Host "PASS working H.264/HEVC/AV1 inventory and automatic choice: $($inventoryJson.automatic_mime)"
Write-Host "Report: $([IO.Path]::GetFullPath($ReportPath))"
