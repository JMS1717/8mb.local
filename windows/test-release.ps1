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
    [string]$ExpectedVersion = '',
    [ValidateSet('all-users', 'current-user')]
    [string]$InstallMode = 'all-users',
    [switch]$UseDefaultInstallDir,
    [switch]$TestNativeWindow,
    [switch]$TestAuth,
    [switch]$TestSettings
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DistDir = Join-Path $RepoRoot 'dist'
$DefaultExe = Join-Path $DistDir '8mblocal.exe'
$DefaultInstaller = Join-Path $DistDir '8mblocal-Setup.exe'
$VersionFile = Join-Path $RepoRoot 'VERSION'
if ([string]::IsNullOrWhiteSpace($ExpectedVersion)) {
    if (-not (Test-Path -LiteralPath $VersionFile -PathType Leaf)) {
        throw "VERSION file is missing: $VersionFile"
    }
    $ExpectedVersion = ([IO.File]::ReadAllText($VersionFile)).Trim()
}
if ($ExpectedVersion -notmatch '^\d+\.\d+\.\d+\.\d+$') {
    throw "ExpectedVersion must be a full four-part version: $ExpectedVersion"
}
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
$ProgressLog = Join-Path $RunRoot 'smoke-progress.log'

function Write-SmokeProgress {
    param([string]$Message)
    try {
        [IO.File]::AppendAllText(
            $ProgressLog,
            ("{0:o} {1}`n" -f (Get-Date), $Message),
            [Text.UTF8Encoding]::new($false)
        )
    } catch {}
}

$process = $null
$client = $null
$installDir = $null
$uninstaller = $null
$desktopShortcut = $null
$authHeaders = @{}
$authUser = $env:RELEASE_SMOKE_AUTH_USER
$authPass = $env:RELEASE_SMOKE_AUTH_PASS
$previousAuthEnabled = $env:AUTH_ENABLED
$previousAuthUser = $env:AUTH_USER
$previousAuthPass = $env:AUTH_PASS
if ($TestAuth) {
    if ([string]::IsNullOrWhiteSpace($authUser)) {
        throw 'TestAuth requires RELEASE_SMOKE_AUTH_USER in the process environment.'
    }
    $authMaterial = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${authUser}:$authPass"))
    $authHeaders = @{ Authorization = "Basic $authMaterial" }
    # The packaged desktop process inherits these isolated smoke-test values.
    # Do not write them to disk or include them in logs.
    $env:AUTH_ENABLED = 'true'
    $env:AUTH_USER = $authUser
    $env:AUTH_PASS = $authPass
}
$dataSentinel = Join-Path $AppData 'preserve-on-uninstall.txt'
[System.IO.File]::WriteAllText($dataSentinel, '8mb.local release-test user data')

function Invoke-JsonGet {
    param([string]$Uri)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -Headers $authHeaders -TimeoutSec 10
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

function Invoke-JsonPut {
    param([string]$Uri, [object]$Payload)
    $body = $Payload | ConvertTo-Json -Depth 12 -Compress
    $content = [System.Net.Http.StringContent]::new(
        $body,
        [System.Text.Encoding]::UTF8,
        'application/json'
    )
    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Put, $Uri)
    $request.Content = $content
    try {
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        $responseBody = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    } finally {
        $request.Dispose()
        $content.Dispose()
    }
    if (-not $response.IsSuccessStatusCode) {
        throw "PUT $Uri returned HTTP $([int]$response.StatusCode): $responseBody"
    }
    if ([string]::IsNullOrWhiteSpace($responseBody)) { return $null }
    return ($responseBody | ConvertFrom-Json)
}

function Invoke-JsonDelete {
    param([string]$Uri)
    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Delete, $Uri)
    try {
        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        $responseBody = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    } finally {
        $request.Dispose()
    }
    if (-not $response.IsSuccessStatusCode) {
        throw "DELETE $Uri returned HTTP $([int]$response.StatusCode): $responseBody"
    }
    if ([string]::IsNullOrWhiteSpace($responseBody)) { return $null }
    return ($responseBody | ConvertFrom-Json)
}

function Assert-SmokeCondition {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Test-SettingsPersistence {
    if (-not $TestAuth) {
        throw 'TestSettings requires TestAuth so protected settings routes can be exercised.'
    }
    if ($null -eq $client) {
        $client = [System.Net.Http.HttpClient]::new()
        $client.Timeout = [TimeSpan]::FromSeconds(60)
    }
    $client.DefaultRequestHeaders.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Basic', $authMaterial)
    Write-SmokeProgress 'settings-start'

    $codecInitial = Invoke-JsonGet "$baseUrl/api/settings/codecs"
    $profilesInitial = Invoke-JsonGet "$baseUrl/api/settings/preset-profiles"
    $historyInitial = Invoke-JsonGet "$baseUrl/api/settings/history"
    $sizeInitial = Invoke-JsonGet "$baseUrl/api/settings/size-buttons"
    $retentionInitial = Invoke-JsonGet "$baseUrl/api/settings/retention-hours"
    $workerInitial = Invoke-JsonGet "$baseUrl/api/settings/worker-concurrency"
    $profileName = 'CodexSmoke-' + [guid]::NewGuid().ToString('N')
    $profileCreated = $false

    try {
        $cpuCandidates = @('libx264', 'libx265', 'libsvtav1', 'libaom_av1') |
            Where-Object { $codecInitial.PSObject.Properties.Name -contains $_ -and [bool]$codecInitial.$_ }
        Assert-SmokeCondition ($cpuCandidates.Count -gt 0) 'No visible CPU fallback codec was available for settings smoke.'
        $codecToHide = [string]$cpuCandidates[0]
        $codecUpdate = [ordered]@{}
        foreach ($property in $codecInitial.PSObject.Properties) {
            $codecUpdate[$property.Name] = [bool]$property.Value
        }
        $codecUpdate[$codecToHide] = $false
        Invoke-JsonPut "$baseUrl/api/settings/codecs" $codecUpdate | Out-Null
        $codecAfterHide = Invoke-JsonGet "$baseUrl/api/settings/codecs"
        Assert-SmokeCondition (-not [bool]$codecAfterHide.$codecToHide) "Codec visibility did not persist for $codecToHide."
        $availableAfterHide = Invoke-JsonGet "$baseUrl/api/codecs/available"
        Assert-SmokeCondition (-not (@($availableAfterHide.enabled_codecs) -contains $codecToHide)) "Hidden codec $codecToHide remained in the available-codec selector."

        $profiles = @($profilesInitial.profiles)
        Assert-SmokeCondition ($profiles.Count -gt 0) 'No preset profile was available for profile persistence smoke.'
        $template = $profiles[0]
        $profilePayload = [ordered]@{
            name = $profileName
            target_mb = [double]$template.target_mb
            video_codec = [string]$template.video_codec
            audio_codec = [string]$template.audio_codec
            preset = [string]$template.preset
            audio_kbps = [int]$template.audio_kbps
            container = [string]$template.container
            tune = [string]$template.tune
            max_output_fps = 17
        }
        Invoke-JsonPost "$baseUrl/api/settings/preset-profiles" $profilePayload | Out-Null
        $profileCreated = $true
        Invoke-JsonPut "$baseUrl/api/settings/preset-profiles/default" @{ name = $profileName } | Out-Null
        $selected = Invoke-JsonGet "$baseUrl/api/settings/preset-profiles"
        Assert-SmokeCondition ([string]$selected.default -eq $profileName) 'New preset profile was not selected as the default.'

        $profilePayload.max_output_fps = 19
        Invoke-JsonPut "$baseUrl/api/settings/preset-profiles/$profileName" $profilePayload | Out-Null
        $presets = Invoke-JsonGet "$baseUrl/api/settings/presets"
        Invoke-JsonPut "$baseUrl/api/settings/presets" $presets | Out-Null
        $fpsCheck = Invoke-JsonGet "$baseUrl/api/settings/preset-profiles"
        $fpsProfile = @($fpsCheck.profiles | Where-Object { $_.name -eq $profileName })[0]
        Assert-SmokeCondition ($null -ne $fpsProfile -and [double]$fpsProfile.max_output_fps -eq 19) 'Editing default presets erased the saved profile FPS cap.'

        $historyChanged = @{ enabled = -not [bool]$historyInitial.enabled }
        Invoke-JsonPut "$baseUrl/api/settings/history" $historyChanged | Out-Null
        Assert-SmokeCondition ([bool](Invoke-JsonGet "$baseUrl/api/settings/history").enabled -eq [bool]$historyChanged.enabled) 'History setting did not round-trip.'

        $sizeChanged = [ordered]@{ buttons = @($sizeInitial.buttons) + @(0.37) }
        Invoke-JsonPut "$baseUrl/api/settings/size-buttons" $sizeChanged | Out-Null
        Assert-SmokeCondition (@((Invoke-JsonGet "$baseUrl/api/settings/size-buttons").buttons) -contains 0.37) 'Size-button setting did not round-trip.'

        $retentionChanged = [int]$retentionInitial.hours + 1
        Invoke-JsonPut "$baseUrl/api/settings/retention-hours" @{ hours = $retentionChanged } | Out-Null
        Assert-SmokeCondition ([int](Invoke-JsonGet "$baseUrl/api/settings/retention-hours").hours -eq $retentionChanged) 'Retention setting did not round-trip.'

        $workerChanged = if ([int]$workerInitial.concurrency -ge 20) { 1 } else { [int]$workerInitial.concurrency + 1 }
        Invoke-JsonPut "$baseUrl/api/settings/worker-concurrency" @{ concurrency = $workerChanged } | Out-Null
        Assert-SmokeCondition ([int](Invoke-JsonGet "$baseUrl/api/settings/worker-concurrency").concurrency -eq $workerChanged) 'Worker-concurrency setting did not round-trip.'

        Invoke-JsonDelete "$baseUrl/api/settings/preset-profiles/$profileName" | Out-Null
        $profileCreated = $false
        $afterDelete = Invoke-JsonGet "$baseUrl/api/settings/preset-profiles"
        Assert-SmokeCondition (@($afterDelete.profiles | Where-Object { $_.name -eq $profileName }).Count -eq 0) 'Deleted preset profile remained in the profile list.'
        Assert-SmokeCondition ([string]$afterDelete.default -ne $profileName -and -not [string]::IsNullOrWhiteSpace([string]$afterDelete.default)) 'Deleting the selected default profile did not select a valid replacement.'
        Write-Host 'PASS settings/codecs/profiles/persistence (visibility, selection, FPS cap, controls, deletion)'
        Write-SmokeProgress 'settings-complete'
    } finally {
        if ($profileCreated) {
            try { Invoke-JsonDelete "$baseUrl/api/settings/preset-profiles/$profileName" | Out-Null } catch {}
        }
        try {
            $currentProfiles = Invoke-JsonGet "$baseUrl/api/settings/preset-profiles"
            if (-not [string]::IsNullOrWhiteSpace([string]$profilesInitial.default) -and @($currentProfiles.profiles | Where-Object { $_.name -eq $profilesInitial.default }).Count -gt 0) {
                Invoke-JsonPut "$baseUrl/api/settings/preset-profiles/default" @{ name = [string]$profilesInitial.default } | Out-Null
            }
        } catch {}
        try { Invoke-JsonPut "$baseUrl/api/settings/codecs" $codecInitial | Out-Null } catch {}
        try { Invoke-JsonPut "$baseUrl/api/settings/history" @{ enabled = [bool]$historyInitial.enabled } | Out-Null } catch {}
        try { Invoke-JsonPut "$baseUrl/api/settings/size-buttons" $sizeInitial | Out-Null } catch {}
        try { Invoke-JsonPut "$baseUrl/api/settings/retention-hours" @{ hours = [int]$retentionInitial.hours } | Out-Null } catch {}
        try { Invoke-JsonPut "$baseUrl/api/settings/worker-concurrency" @{ concurrency = [int]$workerInitial.concurrency } | Out-Null } catch {}
    }
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

function Read-ProcessLogTail {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    try {
        $text = [IO.File]::ReadAllText($Path)
        if ($text.Length -gt 4000) { return $text.Substring($text.Length - 4000) }
        return $text
    } catch {
        return "Unable to read process log ${Path}: $($_.Exception.Message)"
    }
}

function Invoke-BoundedProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$StdoutPath,
        [string]$StderrPath,
        [int]$TimeoutSeconds = 120
    )
    # Windows PowerShell 5.1 does not expose ProcessStartInfo.ArgumentList.
    # Quote each argument explicitly so test paths containing spaces remain
    # valid when Start-Process builds the native command line.
    $argumentString = ($Arguments | ForEach-Object {
        '"' + ([string]$_).Replace('"', '\"') + '"'
    }) -join ' '
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = $argumentString
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $child = [Diagnostics.Process]::new()
    $child.StartInfo = $startInfo
    if (-not $child.Start()) { throw "Unable to start process: $FilePath" }
    $stdoutTask = $child.StandardOutput.ReadToEndAsync()
    $stderrTask = $child.StandardError.ReadToEndAsync()
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while (-not $child.HasExited) {
        if ((Get-Date) -ge $deadline) {
            & taskkill.exe /PID $child.Id /T /F *> $null
            $child.WaitForExit()
            $stdout = $stdoutTask.GetAwaiter().GetResult()
            $stderr = $stderrTask.GetAwaiter().GetResult()
            [IO.File]::WriteAllText($StdoutPath, $stdout)
            [IO.File]::WriteAllText($StderrPath, $stderr)
            throw "Process timed out after ${TimeoutSeconds}s: $FilePath`nstdout:`n$(Read-ProcessLogTail $StdoutPath)`nstderr:`n$(Read-ProcessLogTail $StderrPath)"
        }
        Start-Sleep -Milliseconds 200
    }
    $child.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    [IO.File]::WriteAllText($StdoutPath, $stdout)
    [IO.File]::WriteAllText($StderrPath, $stderr)
    $exitCode = $child.ExitCode
    $child.Dispose()
    if ($exitCode -ne 0) {
        throw "Process exited with code ${exitCode}: $FilePath`nstdout:`n$(Read-ProcessLogTail $StdoutPath)`nstderr:`n$(Read-ProcessLogTail $StderrPath)"
    }
    return $exitCode
}

try {
    Write-SmokeProgress 'test-start'
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
        Write-SmokeProgress 'installer-complete'
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
    Write-SmokeProgress 'process-started'

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

    $spa = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/" -Headers $authHeaders -TimeoutSec 10
    if ($spa.StatusCode -ne 200 -or $spa.Content.Length -lt 100) {
        throw 'Packaged executable did not serve the frontend shell'
    }
    $version = Invoke-JsonGet "$baseUrl/api/version"
    if ([string]$version.version -ne $ExpectedVersion) {
        throw "Packaged executable API version '$($version.version)' does not match expected '$ExpectedVersion'"
    }
    Write-Host "PASS health/frontend/version (version=$($version.version))"
    Write-SmokeProgress 'health-version-complete'

    if ($TestAuth) {
        if ($null -eq $client) {
            $client = [System.Net.Http.HttpClient]::new()
            $client.Timeout = [TimeSpan]::FromSeconds(60)
        }
        $client.DefaultRequestHeaders.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Basic', $authMaterial)
        $unauthenticatedStatus = 0
        try {
            $unauthenticatedResponse = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/api/codecs/available" -TimeoutSec 10
            $unauthenticatedStatus = [int]$unauthenticatedResponse.StatusCode
        } catch {
            if ($null -ne $_.Exception.Response) {
                $unauthenticatedStatus = [int]$_.Exception.Response.StatusCode
            }
        }
        if ($unauthenticatedStatus -ne 401) {
            throw "Protected endpoint accepted an unauthenticated request (status=$unauthenticatedStatus)"
        }
        # The current stream route authenticates with Basic auth directly;
        # there is no separate SSE-token endpoint in the active API.
        $authTaskId = [guid]::NewGuid().ToString()
        $sseRequest = [System.Net.Http.HttpRequestMessage]::new(
            [System.Net.Http.HttpMethod]::Get,
            "$baseUrl/api/stream/$authTaskId"
        )
        $sseResponse = $null
        $sseTimeout = [Threading.CancellationTokenSource]::new([TimeSpan]::FromSeconds(5))
        try {
            $sseResponse = $client.SendAsync(
                $sseRequest,
                [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead,
                $sseTimeout.Token
            ).GetAwaiter().GetResult()
            if (-not $sseResponse.IsSuccessStatusCode) {
                throw "Authenticated SSE request returned HTTP $([int]$sseResponse.StatusCode)"
            }
        } finally {
            if ($null -ne $sseResponse) { $sseResponse.Dispose() }
            $sseRequest.Dispose()
            $sseTimeout.Dispose()
        }
        Write-Host 'PASS authentication/Basic auth/SSE stream authorization'
        Write-SmokeProgress 'auth-complete'
    }

    if ($TestSettings) {
        Test-SettingsPersistence
    }

    if (-not $SkipTranscode) {
        Write-SmokeProgress 'ffmpeg-start'
        $InputFile = Join-Path $MediaDir 'release-smoke-input.mp4'
        $ffmpegArgs = @(
            '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=24',
            '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
            '-t', '2', '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '64k', '-movflags', '+faststart', $InputFile
        )
        $ffmpegStdout = Join-Path $MediaDir 'ffmpeg-smoke.stdout.log'
        $ffmpegStderr = Join-Path $MediaDir 'ffmpeg-smoke.stderr.log'
        Invoke-BoundedProcess -FilePath $Ffmpeg -Arguments $ffmpegArgs `
            -StdoutPath $ffmpegStdout -StderrPath $ffmpegStderr -TimeoutSeconds 120 | Out-Null
        if (-not (Test-Path -LiteralPath $InputFile -PathType Leaf)) {
            throw "FFmpeg completed without creating Windows smoke-test media. stderr:`n$(Read-ProcessLogTail $ffmpegStderr)"
        }
        Write-SmokeProgress 'ffmpeg-complete'

        if ($null -eq $client) {
            $client = [System.Net.Http.HttpClient]::new()
            $client.Timeout = [TimeSpan]::FromSeconds(60)
        }
        if ($TestAuth) {
            $client.DefaultRequestHeaders.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Basic', $authMaterial)
        }
        $upload = Invoke-MultipartUpload "$baseUrl/api/upload" $InputFile
        Write-SmokeProgress 'upload-complete'
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
        Write-SmokeProgress 'compress-request-complete'
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
                Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/api/jobs/$taskId/download?wait=2" -Headers $authHeaders -OutFile $OutputFile -TimeoutSec 15
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
        Write-SmokeProgress 'transcode-complete'
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
    if ($TestAuth) {
        $env:AUTH_ENABLED = $previousAuthEnabled
        $env:AUTH_USER = $previousAuthUser
        $env:AUTH_PASS = $previousAuthPass
    }
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
