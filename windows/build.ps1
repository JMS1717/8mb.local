[CmdletBinding()]
param(
    [string]$Version,
    [string]$OutputDir
)

$ErrorActionPreference = 'Stop'
$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$VersionPath = Join-Path $RepoRoot 'VERSION'

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
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        foreach ($candidate in @('3.13', '3.12', '3.11')) {
            & $launcher.Source "-$candidate" '-c' 'import sys' 2>$null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject]@{ Path = $launcher.Source; Args = @("-$candidate") }
            }
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $python) {
        throw 'A supported Python 3.11, 3.12, or 3.13 interpreter is required.'
    }
    $majorMinor = (& $python.Source -c 'import sys; print(str(sys.version_info.major) + chr(46) + str(sys.version_info.minor))' | Out-String).Trim()
    if ($majorMinor -notin @('3.11', '3.12', '3.13')) {
        throw "Unsupported Python interpreter $majorMinor. Use Python 3.11, 3.12, or 3.13."
    }
    return [pscustomobject]@{ Path = $python.Source; Args = @() }
}

$Python = Resolve-Python
Write-Host "Using Python: $($Python.Path) $($Python.Args -join ' ')"

function Invoke-Python {
    param([string[]]$Arguments)
    & $Python.Path @($Python.Args + $Arguments)
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

function Resolve-SevenZip {
    $command = Get-Command 7z.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) { return $command.Source }
    $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    @(
        (Join-Path $env:ProgramFiles '7-Zip\7z.exe')
        (Join-Path $programFilesX86 '7-Zip\7z.exe')
        (Join-Path $env:LOCALAPPDATA 'Programs\7-Zip\7z.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
}

function Ensure-FfmpegBundle {
    $binDir = Join-Path $PSScriptRoot 'ffmpeg\bin'
    $ffmpegPath = Join-Path $binDir 'ffmpeg.exe'
    $ffprobePath = Join-Path $binDir 'ffprobe.exe'
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null

    $needsDownload = -not (Test-Path -LiteralPath $ffmpegPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $ffprobePath -PathType Leaf)
    if (-not $needsDownload) {
        $encoders = (& $ffmpegPath -hide_banner -encoders 2>&1 | Out-String)
        $needsDownload = $encoders -notmatch '\blibsvtav1\b'
    }
    if (-not $needsDownload) {
        return [pscustomobject]@{ Ffmpeg = $ffmpegPath; Ffprobe = $ffprobePath }
    }

    $sevenZip = Resolve-SevenZip
    if (-not $sevenZip) {
        throw '7-Zip is required to obtain the bundled FFmpeg build with libsvtav1. Install 7-Zip or place tested ffmpeg.exe and ffprobe.exe in windows\ffmpeg\bin.'
    }

    $archive = Join-Path $env:TEMP '8mblocal-ffmpeg-release-full.7z'
    $extractDir = Join-Path $env:TEMP ('8mblocal-ffmpeg-' + [guid]::NewGuid().ToString('N'))
    try {
        Write-Host 'Downloading the FFmpeg full build with SVT-AV1...'
        Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full.7z' -OutFile $archive
        $expectedFfmpegSha256 = '4b9c814cb07a1f90d05b768ef4eb2abbf89af94bbb924df5b7dbd6e64e1e2b96'
        $actualFfmpegSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
        if ($actualFfmpegSha256 -ne $expectedFfmpegSha256) {
            throw "Downloaded FFmpeg archive SHA-256 '$actualFfmpegSha256' does not match the pinned build hash."
        }
        New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
        & $sevenZip x $archive "-o$extractDir" -y | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "7-Zip failed to extract FFmpeg (exit code $LASTEXITCODE)."
        }
        $sourceFfmpeg = Get-ChildItem -LiteralPath $extractDir -Filter 'ffmpeg.exe' -Recurse | Select-Object -First 1
        $sourceFfprobe = Get-ChildItem -LiteralPath $extractDir -Filter 'ffprobe.exe' -Recurse | Select-Object -First 1
        if ($null -eq $sourceFfmpeg -or $null -eq $sourceFfprobe) {
            throw 'The FFmpeg archive did not contain ffmpeg.exe and ffprobe.exe.'
        }
        $encoders = (& $sourceFfmpeg.FullName -hide_banner -encoders 2>&1 | Out-String)
        if ($encoders -notmatch '\blibsvtav1\b') {
            throw 'The FFmpeg archive does not contain libsvtav1; refusing to build without a CPU AV1 fallback.'
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
$Ffmpeg = Ensure-FfmpegBundle

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
    & $installerPath (Join-Path $PSScriptRoot 'installer.iss')
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }
    $builtInstaller = Join-Path $DistDir '8mblocal-Setup.exe'
    if (-not (Test-Path -LiteralPath $builtInstaller -PathType Leaf)) {
        throw "Inno Setup did not create $builtInstaller."
    }

    if ($OutputDir) {
        $resolvedOutput = [IO.Path]::GetFullPath($OutputDir)
        New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null
        Copy-Item -LiteralPath $builtExecutable -Destination (Join-Path $resolvedOutput '8mblocal.exe') -Force
        Copy-Item -LiteralPath $builtInstaller -Destination (Join-Path $resolvedOutput '8mblocal-Setup.exe') -Force
    }

    Write-Host "Built $builtExecutable"
    Write-Host "Built $builtInstaller"
}
finally {
    if (Test-Path -LiteralPath $BuildRoot) {
        Remove-Item -LiteralPath $BuildRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
