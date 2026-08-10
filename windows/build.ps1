$ErrorActionPreference = 'Stop'

# Do not let the Python launcher silently select a newer interpreter than the
# pinned native wheels support.  In particular, `py -3` currently selects
# Python 3.14 on some hosts while the pinned orjson/pydantic-core releases only
# publish wheels through Python 3.13.
$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
$PythonExe = $null
$PythonArgs = @()
if ($null -ne $PythonLauncher) {
    foreach ($version in @('3.11', '3.12', '3.13')) {
        & $PythonLauncher.Source "-$version" '-c' 'import sys' 2>$null
        if ($LASTEXITCODE -eq 0) {
            $PythonExe = $PythonLauncher.Source
            $PythonArgs = @("-$version")
            break
        }
    }
}
if ($null -eq $PythonExe) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $PythonCommand) {
        throw 'A supported Python 3.11, 3.12, or 3.13 interpreter is required to build the Windows executable.'
    }
    $PythonExe = $PythonCommand.Source
}
Write-Host "Using Python: $PythonExe $($PythonArgs -join ' ')"

function Invoke-BuildPython {
    param([string[]]$Arguments)
    & $PythonExe @PythonArgs @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BrandAssetsScript = Join-Path $PSScriptRoot 'brand-assets.ps1'
. $BrandAssetsScript
$BrandDir = Join-Path $RepoRoot 'build\brand'
$BrandIcon = Join-Path $BrandDir '8mblocal.ico'
Write-8mbLocalBrandIco -Path $BrandIcon
$BundleDir = Join-Path $PSScriptRoot 'ffmpeg'
$BinDir = Join-Path $BundleDir 'bin'
$Archive = Join-Path $env:TEMP 'ffmpeg-release-full.7z'

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null

$NeedFfmpeg = -not (Test-Path (Join-Path $BinDir 'ffmpeg.exe')) -or
    -not (Test-Path (Join-Path $BinDir 'ffprobe.exe'))
if (-not $NeedFfmpeg) {
    $existingEncoderListing = (& (Join-Path $BinDir 'ffmpeg.exe') -hide_banner -encoders 2>&1 | Out-String)
    $NeedFfmpeg = $existingEncoderListing -notmatch 'libsvtav1'
}

if ($NeedFfmpeg) {
    Write-Host 'Downloading the FFmpeg full build with SVT-AV1…'
    $SevenZip = Get-Command 7z.exe -ErrorAction SilentlyContinue
    if ($null -eq $SevenZip) {
        $ProgramFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
        $KnownSevenZip = @(
            (Join-Path $env:ProgramFiles '7-Zip\7z.exe'),
            (Join-Path $ProgramFilesX86 '7-Zip\7z.exe')
        ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if ($KnownSevenZip) {
            $SevenZip = [pscustomobject]@{ Source = $KnownSevenZip }
        }
    }
    if ($null -eq $SevenZip) {
        throw '7-Zip is required to extract the FFmpeg full build with libsvtav1. Install 7-Zip or provide prebundled FFmpeg binaries.'
    }
    Invoke-WebRequest `
        -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full.7z' `
        -OutFile $Archive
    $ExtractDir = Join-Path $env:TEMP ('8mblocal-ffmpeg-' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Force -Path $ExtractDir | Out-Null
        & $SevenZip.Source x $Archive "-o$ExtractDir" -y | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "7-Zip failed to extract FFmpeg (exit code $LASTEXITCODE)"
        }
        $Ffmpeg = Get-ChildItem -Path $ExtractDir -Filter 'ffmpeg.exe' -Recurse | Select-Object -First 1
        $Ffprobe = Get-ChildItem -Path $ExtractDir -Filter 'ffprobe.exe' -Recurse | Select-Object -First 1
        if ($null -eq $Ffmpeg -or $null -eq $Ffprobe) {
            throw 'The FFmpeg archive did not contain ffmpeg.exe and ffprobe.exe'
        }
        $EncoderListing = (& $Ffmpeg.FullName -hide_banner -encoders 2>&1 | Out-String)
        if ($EncoderListing -notmatch 'libsvtav1') {
            throw 'The FFmpeg archive does not contain libsvtav1; refusing to build a slow CPU-AV1 fallback.'
        }
        Copy-Item $Ffmpeg.FullName (Join-Path $BinDir 'ffmpeg.exe') -Force
        Copy-Item $Ffprobe.FullName (Join-Path $BinDir 'ffprobe.exe') -Force
    } finally {
        Remove-Item $ExtractDir -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $Archive -Force -ErrorAction SilentlyContinue
    }
}

Push-Location $RepoRoot
try {
    Write-Host 'Building the shared Svelte frontend…'
    Push-Location (Join-Path $RepoRoot 'frontend')
    try {
        npm ci
        npm run build
    } finally {
        Pop-Location
    }

    Write-Host 'Installing the backend runtime used by the frozen application…'
    Invoke-BuildPython @('-m', 'pip', 'install', '-r', (Join-Path $RepoRoot 'requirements.txt'))
    Invoke-BuildPython @('-m', 'pip', 'install', 'pywebview==6.2.1')
    Invoke-BuildPython @('-m', 'pip', 'install', '--upgrade', 'pyinstaller')
    Invoke-BuildPython @(
        '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
        '--name', '8mblocal',
        '--windowed',
        '--icon', $BrandIcon,
        '--version-file', 'windows\version_info.txt',
        '--paths', "$RepoRoot\backend-api",
        '--paths', "$RepoRoot",
        '--add-data', "$RepoRoot\frontend\build;frontend-build",
        '--add-binary', "$($BinDir)\ffmpeg.exe;bin",
        '--add-binary', "$($BinDir)\ffprobe.exe;bin",
        '--collect-submodules', 'app',
        '--collect-submodules', 'worker.app',
        '--collect-submodules', 'webview',
        '--hidden-import', 'shared.local_runtime',
        '--hidden-import', 'shared.subprocess_utils',
        '--hidden-import', 'celery.backends.cache',
        '--hidden-import', 'celery.loaders.app',
        '--hidden-import', 'kombu.transport.memory',
        '--hidden-import', 'worker.app.tasks',
        '--hidden-import', 'worker.app.startup_tests',
        'windows\desktop_app.py'
    )
} finally {
    Pop-Location
}

$InstallerPath = $null
$InstallerCommand = Get-Command iscc -ErrorAction SilentlyContinue
if ($null -ne $InstallerCommand) {
    $InstallerPath = $InstallerCommand.Source
}
if ($null -eq $InstallerPath) {
    $KnownIscc = @(
        if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe' }
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    $InstallerPath = $KnownIscc
}
if ($null -ne $InstallerPath) {
    Write-Host 'Building the Inno Setup installer…'
    & $InstallerPath (Join-Path $PSScriptRoot 'installer.iss')
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE"
    }
    $BuiltInstaller = Join-Path $RepoRoot 'dist\8mblocal-Setup.exe'
    if (-not (Test-Path -LiteralPath $BuiltInstaller -PathType Leaf)) {
        throw "Inno Setup reported success but did not create $BuiltInstaller"
    }
    Write-Host "Built $BuiltInstaller"
} else {
    Write-Warning 'Inno Setup (iscc.exe) was not found; portable executable built, installer skipped.'
}

$BuiltExecutable = Join-Path $RepoRoot 'dist\8mblocal.exe'
if (-not (Test-Path -LiteralPath $BuiltExecutable -PathType Leaf)) {
    throw "PyInstaller reported success but did not create $BuiltExecutable"
}
Write-Host "Built $BuiltExecutable"
