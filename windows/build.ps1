[CmdletBinding()]
param(
    [string]$Version,
    [string]$OutputDir,
    [ValidateSet('x64', 'arm64')]
    [string]$Architecture
)

$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$VersionPath = Join-Path $RepoRoot 'VERSION'

$signingPfx = $env:WINDOWS_SIGNING_PFX_FILE
$signingPassword = $env:WINDOWS_SIGNING_PFX_PASSWORD
$signingConfigured = -not [string]::IsNullOrWhiteSpace($signingPfx) -and
    -not [string]::IsNullOrWhiteSpace($signingPassword)
if (-not $signingConfigured -and
    (-not [string]::IsNullOrWhiteSpace($signingPfx) -or -not [string]::IsNullOrWhiteSpace($signingPassword))) {
    throw 'Windows release signing is only partially configured; provide WINDOWS_SIGNING_PFX_FILE and WINDOWS_SIGNING_PFX_PASSWORD.'
}
if ($signingConfigured -and -not (Test-Path -LiteralPath $signingPfx -PathType Leaf)) {
    throw "WINDOWS_SIGNING_PFX_FILE does not point to a readable certificate: $signingPfx"
}

function Resolve-SignTool {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) { return $command.Source }

    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    if (Test-Path -LiteralPath $kitsRoot -PathType Container) {
        $candidate = Get-ChildItem -LiteralPath $kitsRoot -Filter signtool.exe -Recurse -File |
            Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($null -ne $candidate) { return $candidate.FullName }
    }
    throw 'signtool.exe is required when Windows release signing is enabled.'
}

function Invoke-AuthenticodeSigning {
    param([Parameter(Mandatory)][string]$Path)
    if (-not $signingConfigured) { return }

    $signTool = Resolve-SignTool
    $timestampUrl = if ([string]::IsNullOrWhiteSpace($env:WINDOWS_SIGNING_TIMESTAMP_URL)) {
        'http://timestamp.digicert.com'
    } else {
        $env:WINDOWS_SIGNING_TIMESTAMP_URL
    }
    & $signTool sign /fd SHA256 /tr $timestampUrl /td SHA256 /f $signingPfx /p $signingPassword $Path
    if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed for $Path." }
    & $signTool verify /pa /v $Path
    if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed for $Path." }
}

if (-not $PSBoundParameters.ContainsKey('Architecture')) {
    $nativeArchitecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
    $Architecture = if ($nativeArchitecture -eq 'arm64') { 'arm64' } else { 'x64' }
}

if (-not $PSBoundParameters.ContainsKey('Version')) {
    if (-not (Test-Path -LiteralPath $VersionPath -PathType Leaf)) {
        throw "VERSION file is missing: $VersionPath"
    }
    $Version = ([IO.File]::ReadAllText($VersionPath)).Trim()
}
if ($Version -notmatch '^\d{1,5}\.\d{1,5}\.\d{1,5}\.\d{1,5}$') {
    throw "Version must contain four numeric components, for example 140.0.0.0: $Version"
}

$SetVersionScript = Join-Path $RepoRoot 'scripts\set-version.ps1'
if (-not (Test-Path -LiteralPath $SetVersionScript -PathType Leaf)) {
    throw "Version synchronization script is missing: $SetVersionScript"
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $SetVersionScript -Version $Version -RepositoryRoot $RepoRoot
if ($LASTEXITCODE -ne 0) {
    throw "Version synchronization failed with exit code ${LASTEXITCODE}."
}

function Resolve-Python {
    $candidates = @()
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        $candidates += [pscustomobject]@{ Path = $python.Source; Args = @() }
    }
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        foreach ($candidate in @('3.13', '3.12', '3.11')) {
            $candidates += [pscustomobject]@{ Path = $launcher.Source; Args = @("-$candidate") }
        }
    }

    foreach ($candidate in $candidates) {
        $probeArgs = @($candidate.Args + @(
            '-c',
            'import platform,sys; print(f"{sys.version_info.major}.{sys.version_info.minor}|{platform.machine().lower()}")'
        ))
        $details = (& $candidate.Path @probeArgs 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $details -notmatch '^(3\.11|3\.12|3\.13)\|(.+)$') {
            continue
        }
        $machine = $Matches[2]
        $matchesTarget = if ($Architecture -eq 'arm64') {
            $machine -in @('arm64', 'aarch64')
        } else {
            $machine -in @('amd64', 'x86_64')
        }
        if ($matchesTarget) {
            return $candidate
        }
    }
    throw "A native $Architecture Python 3.11, 3.12, or 3.13 interpreter is required."
}

$Python = Resolve-Python
Write-Host "Using Python: $($Python.Path) $($Python.Args -join ' ')"
$pythonArchitecture = (& $Python.Path @($Python.Args + @('-c', 'import platform; print(platform.machine().lower())')) | Out-String).Trim()
if ($Architecture -eq 'arm64' -and $pythonArchitecture -notin @('arm64', 'aarch64')) {
    throw "A native ARM64 Python is required for an ARM64 PyInstaller build; found '$pythonArchitecture'."
}

function Invoke-Python {
    param([string[]]$Arguments)
    & $Python.Path @($Python.Args + $Arguments)
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

function Get-VerifiedFfmpegArchive {
    param([string[]]$Uris, [string]$Archive, [string]$ExpectedSha256)
    $downloaded = $false
    foreach ($downloadUri in $Uris) {
        try {
            Invoke-WebRequest -Uri $downloadUri -OutFile $Archive
            $downloaded = $true
            break
        } catch {
            Write-Warning "FFmpeg archive unavailable at $downloadUri; trying the next pinned source."
        }
    }
    if (-not $downloaded) { throw 'No pinned FFmpeg source was available.' }
    $actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $ExpectedSha256) {
        throw "Downloaded FFmpeg SHA-256 '$actualSha256' does not match the pinned build hash."
    }
}

function Ensure-FfmpegBundle {
    param([ValidateSet('x64', 'arm64')][string]$TargetArchitecture)

    $binDir = if ($TargetArchitecture -eq 'arm64') {
        Join-Path $PSScriptRoot 'ffmpeg\arm64\bin'
    } else {
        Join-Path $PSScriptRoot 'ffmpeg\bin'
    }
    $ffmpegPath = Join-Path $binDir 'ffmpeg.exe'
    $ffprobePath = Join-Path $binDir 'ffprobe.exe'
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null

    $needsDownload = -not (Test-Path -LiteralPath $ffmpegPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $ffprobePath -PathType Leaf)
    if (-not $needsDownload) {
        $encoders = (& $ffmpegPath -hide_banner -encoders 2>&1 | Out-String)
        $needsDownload = $encoders -notmatch '\blibsvtav1\b' -or
            $encoders -notmatch '\bh264_mf\b'
    }
    if (-not $needsDownload) {
        return [pscustomobject]@{ Ffmpeg = $ffmpegPath; Ffprobe = $ffprobePath }
    }

    if ($TargetArchitecture -eq 'arm64') {
        # Prefer our release mirror: BtbN expires dated autobuild assets.
        # Both sources must match the same pinned archive digest.
        $archive = Join-Path $env:TEMP '8mblocal-ffmpeg-winarm64.zip'
        $extractDir = Join-Path $env:TEMP ('8mblocal-ffmpeg-arm64-' + [guid]::NewGuid().ToString('N'))
        $uris = @(
            'https://github.com/JMS1717/8mb.local/releases/download/v143/8mblocal-ffmpeg-8.1.3-winarm64.zip',
            'https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-30-13-08/ffmpeg-n8.1.3-9-g29e619e767-winarm64-gpl-8.1.zip'
        )
        $expectedSha256 = '061a770557c30bbf8e0b6ca3810288a60f72b85088378b84be03277746f34f17'
        try {
            Write-Host 'Downloading the pinned native Windows ARM64 FFmpeg build...'
            Get-VerifiedFfmpegArchive -Uris $uris -Archive $archive -ExpectedSha256 $expectedSha256
            Expand-Archive -LiteralPath $archive -DestinationPath $extractDir -Force
            $sourceFfmpeg = Get-ChildItem -LiteralPath $extractDir -Filter 'ffmpeg.exe' -Recurse | Select-Object -First 1
            $sourceFfprobe = Get-ChildItem -LiteralPath $extractDir -Filter 'ffprobe.exe' -Recurse | Select-Object -First 1
            if ($null -eq $sourceFfmpeg -or $null -eq $sourceFfprobe) {
                throw 'The ARM64 FFmpeg archive did not contain ffmpeg.exe and ffprobe.exe.'
            }
            $encoders = (& $sourceFfmpeg.FullName -hide_banner -encoders 2>&1 | Out-String)
            if ($encoders -notmatch '\blibsvtav1\b') {
                throw 'The ARM64 FFmpeg archive does not contain libsvtav1.'
            }
            if ($encoders -notmatch '\bh264_mf\b') {
                throw 'The ARM64 FFmpeg archive does not contain Media Foundation encoders.'
            }
            Copy-Item -LiteralPath $sourceFfmpeg.FullName -Destination $ffmpegPath -Force
            Copy-Item -LiteralPath $sourceFfprobe.FullName -Destination $ffprobePath -Force
        }
        finally {
            Remove-Item -LiteralPath $extractDir -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
        }
        return [pscustomobject]@{ Ffmpeg = $ffmpegPath; Ffprobe = $ffprobePath }
    }

    # The release mirror retains the exact input after BtbN expires its copy.
    $archive = Join-Path $env:TEMP '8mblocal-ffmpeg-win64.zip'
    $extractDir = Join-Path $env:TEMP ('8mblocal-ffmpeg-' + [guid]::NewGuid().ToString('N'))
    try {
        Write-Host 'Downloading the pinned native Windows x64 FFmpeg build...'
        $uris = @(
            'https://github.com/JMS1717/8mb.local/releases/download/v143/8mblocal-ffmpeg-8.1.3-win64.zip',
            'https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-30-13-08/ffmpeg-n8.1.3-9-g29e619e767-win64-gpl-8.1.zip'
        )
        $expectedFfmpegSha256 = '7b801cdd3a1a0bb54ae6f572187e68b4ed54f52086cfee133fab2180bfb429fa'
        Get-VerifiedFfmpegArchive -Uris $uris -Archive $archive -ExpectedSha256 $expectedFfmpegSha256
        Expand-Archive -LiteralPath $archive -DestinationPath $extractDir -Force
        $sourceFfmpeg = Get-ChildItem -LiteralPath $extractDir -Filter 'ffmpeg.exe' -Recurse | Select-Object -First 1
        $sourceFfprobe = Get-ChildItem -LiteralPath $extractDir -Filter 'ffprobe.exe' -Recurse | Select-Object -First 1
        if ($null -eq $sourceFfmpeg -or $null -eq $sourceFfprobe) {
            throw 'The FFmpeg archive did not contain ffmpeg.exe and ffprobe.exe.'
        }
        $encoders = (& $sourceFfmpeg.FullName -hide_banner -encoders 2>&1 | Out-String)
        if ($encoders -notmatch '\blibsvtav1\b') {
            throw 'The FFmpeg archive does not contain libsvtav1; refusing to build without a CPU AV1 fallback.'
        }
        if ($encoders -notmatch '\bh264_mf\b') {
            throw 'The FFmpeg archive does not contain Media Foundation encoders.'
        }
        Copy-Item -LiteralPath $sourceFfmpeg.FullName -Destination $ffmpegPath -Force
        Copy-Item -LiteralPath $sourceFfprobe.FullName -Destination $ffprobePath -Force
    }
    finally {
        Remove-Item -LiteralPath $extractDir -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    }
    return [pscustomobject]@{ Ffmpeg = $ffmpegPath; Ffprobe = $ffprobePath }
}

$BrandAssetsScript = Join-Path $PSScriptRoot 'brand-assets.ps1'
. $BrandAssetsScript
$BrandDir = Join-Path $RepoRoot 'build\brand'
$BrandIcon = Join-Path $BrandDir '8mblocal.ico'
Write-8mbLocalBrandIco -Path $BrandIcon
$Ffmpeg = Ensure-FfmpegBundle -TargetArchitecture $Architecture

$DistDir = Join-Path $RepoRoot 'dist'
$BuildRoot = Join-Path $env:TEMP ('8mblocal-windows-build-' + [guid]::NewGuid().ToString('N'))
$Venv = Join-Path $BuildRoot 'venv'
$PyInstallerWork = Join-Path $BuildRoot 'pyinstaller-work'
$ExistingVenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$UseExistingVenv = $false
if (Test-Path -LiteralPath $ExistingVenvPython -PathType Leaf) {
    & $ExistingVenvPython -c 'import fastapi, uvicorn, multipart, aiofiles, orjson, pydantic, pydantic_settings, redis, celery, apscheduler, dotenv, psutil, webview, PyInstaller' 2>$null
    $UseExistingVenv = ($LASTEXITCODE -eq 0)
}
New-Item -ItemType Directory -Force -Path $DistDir,$BuildRoot | Out-Null

try {
    Push-Location (Join-Path $RepoRoot 'frontend')
    try {
        Write-Host 'Building the shared Svelte frontend...'
        $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue)
        if ($null -eq $npm) { $npm = Get-Command npm -ErrorAction Stop }
        & $npm.Source ci --loglevel=error --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE." }
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed with exit code $LASTEXITCODE." }
    }
    finally {
        Pop-Location
    }

    if ($UseExistingVenv) {
        $VenvPython = $ExistingVenvPython
        Write-Host "Using complete project Python environment: $VenvPython"
    } else {
        Write-Host 'Creating an isolated Windows build environment...'
        Invoke-Python @('-m', 'venv', $Venv)
        $VenvPython = Join-Path $Venv 'Scripts\python.exe'
        & $VenvPython -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw 'Failed to bootstrap the isolated Python build environment.' }
        & $VenvPython -m pip install -r (Join-Path $RepoRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install backend requirements for the Windows build.' }
        & $VenvPython -m pip install 'pywebview==6.2.1' 'pyinstaller>=6.11,<7'
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install pywebview/PyInstaller for the Windows build.' }
    }

    $pyinstallerArgs = @(
        '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
        '--name', '8mblocal',
        '--windowed',
        '--distpath', $DistDir,
        '--workpath', $PyInstallerWork,
        '--specpath', $BuildRoot,
        '--icon', $BrandIcon,
        '--version-file', (Join-Path $PSScriptRoot 'version_info.txt'),
        '--paths', (Join-Path $RepoRoot 'backend-api'),
        '--paths', $RepoRoot,
        '--add-data', ((Join-Path $RepoRoot 'frontend\build') + ';frontend-build'),
        '--add-binary', ($Ffmpeg.Ffmpeg + ';bin'),
        '--add-binary', ($Ffmpeg.Ffprobe + ';bin'),
        '--collect-submodules', 'app',
        '--collect-submodules', 'worker.app',
        '--collect-submodules', 'webview',
        '--hidden-import', 'shared.local_runtime',
        '--hidden-import', 'shared.subprocess_utils',
        '--hidden-import', 'celery.backends.cache',
        '--hidden-import', 'celery.loaders.app',
        '--hidden-import', 'kombu.transport.memory',
        '--hidden-import', 'worker.app.worker',
        '--hidden-import', 'worker.app.tasks',
        '--hidden-import', 'worker.app.startup_tests',
        (Join-Path $PSScriptRoot 'desktop_app.py')
    )
    Write-Host 'Building the portable Windows executable...'
    & $VenvPython @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }

    $builtExecutable = Join-Path $DistDir '8mblocal.exe'
    if (-not (Test-Path -LiteralPath $builtExecutable -PathType Leaf)) {
        throw "PyInstaller did not create $builtExecutable."
    }
    Invoke-AuthenticodeSigning -Path $builtExecutable

    $installerPath = $null
    $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($null -ne $iscc) {
        $installerPath = $iscc.Source
    }
    if (-not $installerPath) {
        $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
        $installerPath = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
            (Join-Path $programFilesX86 'Inno Setup 6\ISCC.exe')
            (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
    }
    if (-not $installerPath) {
        throw 'Inno Setup (ISCC.exe) is required to build the installer EXE.'
    }

    Write-Host 'Building the Inno Setup installer...'
    $installerBaseName = if ($Architecture -eq 'arm64') { '8mblocal-Setup-arm64' } else { '8mblocal-Setup' }
    & $installerPath "/DMyAppArchitecture=$Architecture" "/DMyAppOutputBaseFilename=$installerBaseName" (Join-Path $PSScriptRoot 'installer.iss')
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }
    $builtInstaller = Join-Path $DistDir ($installerBaseName + '.exe')
    if (-not (Test-Path -LiteralPath $builtInstaller -PathType Leaf)) {
        throw "Inno Setup did not create $builtInstaller."
    }
    Invoke-AuthenticodeSigning -Path $builtInstaller

    if ($OutputDir) {
        $resolvedOutput = [IO.Path]::GetFullPath($OutputDir)
        New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null
        Copy-Item -LiteralPath $builtExecutable -Destination (Join-Path $resolvedOutput '8mblocal.exe') -Force
        Copy-Item -LiteralPath $builtInstaller -Destination (Join-Path $resolvedOutput ($installerBaseName + '.exe')) -Force
    }

    Write-Host "Built $builtExecutable"
    Write-Host "Built $builtInstaller"
}
finally {
    if (Test-Path -LiteralPath $BuildRoot) {
        Remove-Item -LiteralPath $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
