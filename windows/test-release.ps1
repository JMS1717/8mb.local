[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$Install,
    [switch]$SkipTranscode,
    [switch]$KeepData,
    [int]$Port = 8123,
    [string]$DataDir = '',
    [string]$ExePath = '',
    [string]$InstallerPath = '',
    [ValidateSet('all-users', 'current-user')]
    [string]$InstallMode = 'all-users',
    [switch]$UseDefaultInstallDir,
    [switch]$TestNativeWindow
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DistDir = Join-Path $RepoRoot 'dist'
$DefaultExe = Join-Path $DistDir '8mblocal.exe'
$DefaultInstaller = Join-Path $DistDir '8mblocal-Setup.exe'

if ($Build) {
    & (Join-Path $PSScriptRoot 'build.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw "windows/build.ps1 failed with exit code $LASTEXITCODE"
    }
}

if ([string]::IsNullOrWhiteSpace($DataDir)) {
    $DataDir = Join-Path $env:TEMP ('8mblocal-release-smoke-' + [guid]::NewGuid().ToString('N'))
}
$RunRoot = Join-Path $DataDir ('run-' + [guid]::NewGuid().ToString('N'))
$AppData = Join-Path $RunRoot 'app-data'
$MediaDir = Join-Path $RunRoot 'media'
$LogOut = Join-Path $RunRoot 'app.stdout.log'
$LogErr = Join-Path $RunRoot 'app.stderr.log'
$null = New-Item -ItemType Directory -Force -Path $AppData, $MediaDir

$process = $null
$client = $null
$installDir = $null
$uninstaller = $null
$desktopShortcut = $null
$dataSentinel = Join-Path $AppData 'preserve-on-uninstall.txt'
[System.IO.File]::WriteAllText($dataSentinel, '8mb.local release-test user data')

function Invoke-JsonGet {
    param([string]$Uri)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 10
    } catch {
        throw "GET $Uri failed: $($_.Exception.Message)"
    }
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
        throw "GET $Uri returned HTTP $($response.StatusCode)"
    }
    return ($response.Content | ConvertFrom-Json)
}

function Invoke-JsonPost {
    param([string]$Uri, [hashtable]$Payload)
    $body = $Payload | ConvertTo-Json -Depth 8 -Compress
    $content = [System.Net.Http.StringContent]::new(
        $body,
        [System.Text.Encoding]::UTF8,
        'application/json'
    )
    try {
        $response = $client.PostAsync($Uri, $content).GetAwaiter().GetResult()
        $responseBody = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    } finally {
        $content.Dispose()
    }
    if (-not $response.IsSuccessStatusCode) {
        throw "POST $Uri returned HTTP $([int]$response.StatusCode): $responseBody"
    }
    return ($responseBody | ConvertFrom-Json)
}

function Invoke-MultipartUpload {
    param([string]$Uri, [string]$InputPath)
    $multipart = [System.Net.Http.MultipartFormDataContent]::new()
    $stream = [System.IO.File]::OpenRead($InputPath)
    $fileContent = [System.Net.Http.StreamContent]::new($stream)
    $fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('video/mp4')
    $multipart.Add($fileContent, 'file', [System.IO.Path]::GetFileName($InputPath))
    $multipart.Add([System.Net.Http.StringContent]::new('0.5'), 'target_size_mb')
    $multipart.Add([System.Net.Http.StringContent]::new('64'), 'audio_bitrate_kbps')
    try {
        $response = $client.PostAsync($Uri, $multipart).GetAwaiter().GetResult()
        $responseBody = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    } finally {
        $multipart.Dispose()
        $fileContent.Dispose()
        $stream.Dispose()
    }
    if (-not $response.IsSuccessStatusCode) {
        throw "Multipart upload returned HTTP $([int]$response.StatusCode): $responseBody"
    }
    return ($responseBody | ConvertFrom-Json)
}

try {
    if ($Install) {
        if ([string]::IsNullOrWhiteSpace($InstallerPath)) { $InstallerPath = $DefaultInstaller }
        if (-not (Test-Path -LiteralPath $InstallerPath -PathType Leaf)) {
            throw "Installer not found: $InstallerPath (run with -Build or provide -InstallerPath)"
        }
        if ($UseDefaultInstallDir) {
            $defaultRoot = if ($InstallMode -eq 'current-user') {
                Join-Path $env:LOCALAPPDATA 'Programs'
            } else {
                ${env:ProgramFiles}
            }
            $installDir = Join-Path $defaultRoot '8mb.local'
            if (Test-Path -LiteralPath $installDir) {
                throw "Refusing to overwrite an existing default installation: $installDir"
            }
        } else {
            $installDir = Join-Path $RunRoot 'installed'
        }
        $installerArguments = @(
            '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'
        )
        if (-not $UseDefaultInstallDir) {
            $installerArguments += "/DIR=`"$installDir`""
        }
        if ($InstallMode -eq 'current-user') {
            $installerArguments += '/CURRENTUSER'
        } else {
            $installerArguments += '/ALLUSERS'
        }
        $installResult = Start-Process -FilePath $InstallerPath -ArgumentList $installerArguments -Wait -PassThru
        if ($installResult.ExitCode -ne 0) {
            throw "Installer exited with code $($installResult.ExitCode)"
        }
        $Executable = Join-Path $installDir '8mblocal.exe'
        $uninstaller = Join-Path $installDir 'unins000.exe'

        $desktopFolder = if ($InstallMode -eq 'current-user') {
            [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
        } else {
            [Environment]::GetFolderPath([Environment+SpecialFolder]::CommonDesktopDirectory)
        }
        $desktopShortcut = Join-Path $desktopFolder '8mb.local.lnk'
        if (-not (Test-Path -LiteralPath $desktopShortcut -PathType Leaf)) {
            throw "${InstallMode} Desktop shortcut was not created: $desktopShortcut"
        }
        $shell = $null
        $shortcut = $null
        try {
            $shell = New-Object -ComObject WScript.Shell
            $shortcut = $shell.CreateShortcut($desktopShortcut)
            $shortcutTarget = [System.IO.Path]::GetFullPath([string]$shortcut.TargetPath)
        } finally {
            if ($null -ne $shortcut) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shortcut) }
            if ($null -ne $shell) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell) }
        }
        if ($shortcutTarget -ne [System.IO.Path]::GetFullPath($Executable)) {
            throw "${InstallMode} Desktop shortcut targets '$shortcutTarget' instead of '$Executable'"
        }
        Write-Host "PASS $InstallMode installer/Desktop shortcut (target=$shortcutTarget)"
    } else {
        if ([string]::IsNullOrWhiteSpace($ExePath)) { $ExePath = $DefaultExe }
        $Executable = $ExePath
    }

    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
        throw "Executable not found: $Executable (run with -Build or provide -ExePath)"
    }

    $Ffmpeg = Join-Path $RepoRoot 'windows\ffmpeg\bin\ffmpeg.exe'
    $Ffprobe = Join-Path $RepoRoot 'windows\ffmpeg\bin\ffprobe.exe'
    if (-not $SkipTranscode -and (-not (Test-Path -LiteralPath $Ffmpeg) -or -not (Test-Path -LiteralPath $Ffprobe))) {
        throw 'Bundled FFmpeg is missing; run windows\build.ps1 first or use -SkipTranscode'
    }

    $process = Start-Process `
        -FilePath $Executable `
        -ArgumentList @('--data-dir', "`"$AppData`"", '--port', "$Port", '--no-browser') `
        -RedirectStandardOutput $LogOut `
        -RedirectStandardError $LogErr `
        -WindowStyle Hidden `
        -PassThru

    $baseUrl = "http://127.0.0.1:$Port"
    $healthy = $false
    $lastHealthError = ''
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) {
            throw "8mblocal.exe exited during startup with code $($process.ExitCode)"
        }
        try {
            $health = Invoke-JsonGet "$baseUrl/healthz"
            if ($health.ok -eq $true) {
                $healthy = $true
                break
            }
            $lastHealthError = "healthz returned ok=$($health.ok)"
        } catch {
            $lastHealthError = $_.Exception.Message
        }
    }
    if (-not $healthy) {
        throw "Timed out waiting for packaged executable healthz: $lastHealthError"
    }

    $spa = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/" -TimeoutSec 10
    if ($spa.StatusCode -ne 200 -or $spa.Content.Length -lt 100) {
        throw 'Packaged executable did not serve the frontend shell'
    }
    $version = Invoke-JsonGet "$baseUrl/api/version"
    if ([string]::IsNullOrWhiteSpace([string]$version.version)) {
        throw 'Packaged executable returned an empty API version'
    }
    Write-Host "PASS health/frontend/version (version=$($version.version))"

    if (-not $SkipTranscode) {
        $InputFile = Join-Path $MediaDir 'release-smoke-input.mp4'
        $ffmpegArgs = @(
            '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=24',
            '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
            '-t', '2', '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '64k', '-movflags', '+faststart', $InputFile
        )
        & $Ffmpeg @ffmpegArgs
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $InputFile -PathType Leaf)) {
            throw "Failed to generate Windows smoke-test media (ffmpeg exit $LASTEXITCODE)"
        }

        $client = [System.Net.Http.HttpClient]::new()
        $client.Timeout = [TimeSpan]::FromSeconds(60)
        $upload = Invoke-MultipartUpload "$baseUrl/api/upload" $InputFile
        $compress = Invoke-JsonPost "$baseUrl/api/compress" @{
            job_id = [string]$upload.job_id
            filename = [string]$upload.filename
            target_size_mb = 0.5
            target_video_bitrate_kbps = 300
            video_codec = 'libx264'
            audio_codec = 'aac'
            audio_bitrate_kbps = 64
            preset = 'p1'
            container = 'mp4'
            tune = 'hq'
            fast_mp4_finalize = $true
        }
        $taskId = [string]$compress.task_id
        $jobDone = $false
        $lastStatus = $null
        for ($attempt = 0; $attempt -lt 360; $attempt++) {
            $lastStatus = Invoke-JsonGet "$baseUrl/api/jobs/$taskId/status"
            $state = ([string]$lastStatus.state).ToUpperInvariant()
            if ($state -in @('SUCCESS', 'COMPLETED')) {
                $jobDone = $true
                break
            }
            if ($state -in @('FAILURE', 'FAILED', 'REVOKED', 'CANCELED', 'CANCELLED')) {
                throw "Smoke-test job ended in ${state}: $($lastStatus.detail)"
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $jobDone) {
            throw "Timed out waiting for smoke-test job ${taskId}: $($lastStatus | ConvertTo-Json -Compress)"
        }

        $OutputFile = Join-Path $MediaDir 'release-smoke-output.mp4'
        $downloaded = $false
        for ($attempt = 0; $attempt -lt 12; $attempt++) {
            try {
                Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/api/jobs/$taskId/download?wait=2" -OutFile $OutputFile -TimeoutSec 15
                $downloaded = $true
                break
            } catch {
                Start-Sleep -Milliseconds 500
            }
        }
        if (-not $downloaded -or -not (Test-Path -LiteralPath $OutputFile -PathType Leaf)) {
            throw "Completed smoke-test job $taskId could not be downloaded"
        }
        $probeText = (& $Ffprobe '-hide_banner' '-loglevel' 'error' '-show_entries' 'format=duration,size' '-of' 'default=noprint_wrappers=1:nokey=1' $OutputFile 2>&1) -join "`n"
        if ($LASTEXITCODE -ne 0) {
            throw "Downloaded output failed ffprobe: $probeText"
        }
        $probeValues = $probeText -split "`r?`n" | Where-Object { $_ -and $_.Trim() }
        if ($probeValues.Count -lt 2) {
            throw "Downloaded output has incomplete FFprobe metadata: $probeText"
        }
        $duration = [double]::Parse($probeValues[0], [Globalization.CultureInfo]::InvariantCulture)
        $size = [int64]::Parse($probeValues[1], [Globalization.CultureInfo]::InvariantCulture)
        if ($duration -le 0 -or $size -le 0) {
            throw "Downloaded output has invalid FFprobe metadata: $probeText"
        }
        Write-Host "PASS upload/transcode/status/download/ffprobe (bytes=$((Get-Item $OutputFile).Length))"
    }

    if ($TestNativeWindow) {
        # Stop the headless smoke-test instance before exercising the real
        # WebView shell on a separate port.
        # Kill the owned launcher tree while the one-file child is still
        # attached. Stopping only the outer PyInstaller process orphans the
        # extracted server child and leaves its port open.
        & taskkill.exe /PID $process.Id /T /F *> $null
        $process.WaitForExit(10000) | Out-Null
        $process = $null

        $nativePort = $Port + 1
        $resolvedNativeExecutable = [System.IO.Path]::GetFullPath($Executable)
        $preexistingNativePids = @(
            Get-CimInstance Win32_Process -Filter "Name='8mblocal.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.ExecutablePath -eq $resolvedNativeExecutable } |
                ForEach-Object { [int]$_.ProcessId }
        )
        $native = Start-Process -FilePath $Executable -ArgumentList @(
            '--data-dir', "`"$AppData`"", '--port', "$nativePort"
        ) -PassThru
        $process = $native
        $windowHandle = [IntPtr]::Zero
        $windowProcess = $null
        for ($attempt = 0; $attempt -lt 120; $attempt++) {
            Start-Sleep -Milliseconds 250
            if ($native.HasExited) {
                throw "Native desktop process exited during startup with code $($native.ExitCode)"
            }
            # A PyInstaller one-file executable starts an extracted child
            # process that owns the WebView window. Locate the window by the
            # verified executable path instead of assuming the outer launcher
            # owns it.
            $candidates = Get-CimInstance Win32_Process -Filter "Name='8mblocal.exe'" -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.ExecutablePath -eq $resolvedNativeExecutable -and
                    [int]$_.ProcessId -notin $preexistingNativePids
                }
            foreach ($candidate in $candidates) {
                $candidateProcess = Get-Process -Id $candidate.ProcessId -ErrorAction SilentlyContinue
                if ($null -eq $candidateProcess) { continue }
                $candidateProcess.Refresh()
                if ($candidateProcess.MainWindowHandle -ne [IntPtr]::Zero) {
                    $windowProcess = $candidateProcess
                    $windowHandle = $candidateProcess.MainWindowHandle
                    break
                }
            }
            if ($windowHandle -ne [IntPtr]::Zero) { break }
        }
        if ($windowHandle -eq [IntPtr]::Zero) {
            throw 'Native WebView window did not appear'
        }
        $nativeHealth = Invoke-JsonGet "http://127.0.0.1:$nativePort/healthz"
        if ($nativeHealth.ok -ne $true) {
            throw 'Native WebView runtime did not expose a healthy local API'
        }
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class NativeWindowClose {
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
}
'@
        [void][NativeWindowClose]::SendMessage($windowHandle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
        if (-not $native.WaitForExit(30000)) {
            throw 'Closing the native window did not stop the desktop process within 30 seconds'
        }
        $process = $null
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$nativePort/healthz" -TimeoutSec 2 | Out-Null
            throw 'Local API remained reachable after closing the native window'
        } catch {
            if ($_.Exception.Message -eq 'Local API remained reachable after closing the native window') { throw }
        }
        Write-Host 'PASS native WebView launch/window close/process shutdown'
    }

    Write-Host 'Windows release smoke test passed.'
} catch {
    Write-Error $_
    if (Test-Path -LiteralPath $LogOut) { Write-Host (Get-Content -LiteralPath $LogOut -Tail 80 -ErrorAction SilentlyContinue) }
    if (Test-Path -LiteralPath $LogErr) { Write-Host (Get-Content -LiteralPath $LogErr -Tail 80 -ErrorAction SilentlyContinue) }
    exit 1
} finally {
    if ($null -ne $client) { $client.Dispose() }
    if ($null -ne $process) {
        # A windowless PyInstaller process may detach from the PowerShell
        # process object before the request finishes.  Verify both PID and
        # executable path, then terminate only the process tree started by
        # this smoke test so an unrelated 8mblocal instance is untouched.
        try {
            $candidate = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)" -ErrorAction SilentlyContinue
            $resolvedExecutable = if ($Executable) { [System.IO.Path]::GetFullPath($Executable) } else { '' }
            if ($null -ne $candidate -and $candidate.ExecutablePath -eq $resolvedExecutable) {
                & taskkill.exe /PID $process.Id /T /F *> $null
            }
        } catch {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    if ($null -ne $uninstaller -and (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        $uninstallResult = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait -PassThru
        if ($uninstallResult.ExitCode -ne 0) {
            throw "Uninstaller exited with code $($uninstallResult.ExitCode)"
        }
        if ($Executable -and (Test-Path -LiteralPath $Executable)) {
            throw "Uninstaller left the application executable behind: $Executable"
        }
        if ($desktopShortcut -and (Test-Path -LiteralPath $desktopShortcut)) {
            throw "Uninstaller left the Desktop shortcut behind: $desktopShortcut"
        }
        if (-not (Test-Path -LiteralPath $dataSentinel -PathType Leaf)) {
            throw "Uninstaller removed user data: $dataSentinel"
        }
        Write-Host 'PASS uninstall/application removal/user-data preservation'
    }
    if (-not $KeepData -and (Test-Path -LiteralPath $RunRoot)) {
        Remove-Item -LiteralPath $RunRoot -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "Smoke-test artifacts kept at $RunRoot"
    }
}
