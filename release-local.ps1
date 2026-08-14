[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    [string]$OutputDir,
    [switch]$SkipTests,
    [switch]$SkipDocker,
    [switch]$SkipWindows,
    [switch]$SkipMsix,
    [switch]$DryRun,
    [switch]$KeepTemp,
    [switch]$Overwrite
)

$ErrorActionPreference = 'Stop'
$Root = [System.IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$SetVersion = Join-Path $Root 'scripts\set-version.ps1'
$CheckVersion = Join-Path $Root 'scripts\check-version.ps1'
$WindowsBuild = Join-Path $Root 'windows\build.ps1'
$Timestamp = (Get-Date).ToUniversalTime().ToString('o')
$StageResults = New-Object System.Collections.Generic.List[object]
$Events = New-Object System.Collections.Generic.List[string]
$Artifacts = New-Object System.Collections.Generic.List[object]
$OutputPath = $null
$Failure = $null
$DockerContainer = $null
$DockerContainerStarted = $false
$DockerTemp = $null
$ToolVersions = [ordered]@{}
$StorePackageIdentityName = 'jms1717.8mb.local'
$StorePublisher = 'CN=AAE66F20-996E-4A3C-B08E-182952BAD9F7'
$StorePublisherDisplayName = 'jms1717'
$ReleaseMarkerName = '.8mblocal-release-output'
$ReleaseMarkerValue = '8mb.local local release output'

function Write-Event {
    param([string]$Message)
    $line = (Get-Date).ToString('s') + ' ' + $Message
    $Events.Add($line) | Out-Null
    Write-Host $line
}

function Get-CommandPath {
    param([string]$Name)
    if ($Name -in @('npm', 'npx')) {
        $cmdShim = Get-Command ($Name + '.cmd') -ErrorAction SilentlyContinue
        if ($null -ne $cmdShim) {
            return $cmdShim.Source
        }
    }
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return $null
    }
    return $command.Source
}

function Get-NormalizedFourPartVersion {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $match = [regex]::Match($Value, '\d+(?:\.\d+){1,3}')
    if (-not $match.Success) { return $null }
    try {
        $parsed = [version]$match.Value
        return '{0}.{1}.{2}.{3}' -f $parsed.Major, $parsed.Minor,
            $(if ($parsed.Build -lt 0) { 0 } else { $parsed.Build }),
            $(if ($parsed.Revision -lt 0) { 0 } else { $parsed.Revision })
    } catch {
        return $null
    }
}

function Require-Command {
    param([string]$Name, [string]$Purpose)
    $path = Get-CommandPath $Name
    if ($null -eq $path) {
        throw "Required tool '$Name' is missing ($Purpose). Install it separately or use a stage skip option."
    }
    return $path
}

function Get-ToolVersion {
    param([string]$Path, [string[]]$Arguments = @('--version'))
    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $line = (& $Path @Arguments 2>&1 |
            Select-Object -First 1 |
            ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord] -and $_.Exception -and $_.Exception.Message) {
                    $_.Exception.Message
                } else {
                    $_.ToString()
                }
            } |
            Out-String).Trim()
        if ($line) { return $line }
    } catch {}
    finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    return 'unavailable'
}

function Invoke-Stage {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $Root,
        [switch]$AllowFailure
    )
    $safeName = ($Name -replace '[^A-Za-z0-9_.-]', '_')
    $logPath = $null
    if ($null -ne $OutputPath) {
        $logPath = Join-Path $OutputPath ($safeName + '.log')
    }
    Write-Event ("COMMAND " + $Name + ": " + $Executable + " " + ($Arguments -join ' '))
    $started = Get-Date
    $lines = New-Object System.Collections.Generic.List[string]
    Push-Location $WorkingDirectory
    $oldErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell promotes native stderr (including compiler
        # warnings) into ErrorRecord objects. Capture that stream as log text
        # and use the process exit code as the stage result instead of
        # aborting on a warning.
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments 2>&1 | ForEach-Object {
            $text = $_.ToString()
            $lines.Add($text) | Out-Null
            Write-Host $text
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
        Pop-Location
    }
    if ($null -ne $logPath) {
        # Always create a log file, including for successful commands that
        # produce no stdout/stderr. The manifest must never point at a missing
        # stage log.
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllLines($logPath, [string[]]$lines.ToArray(), $utf8NoBom)
    }
    $passed = ($exitCode -eq 0)
    $StageResults.Add([pscustomobject]@{
        name = $Name
        command = $Executable + ' ' + ($Arguments -join ' ')
        working_directory = $WorkingDirectory
        exit_code = $exitCode
        passed = $passed
        duration_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
        log = $logPath
    }) | Out-Null
    if (-not $passed -and -not $AllowFailure) {
        throw "Stage '$Name' failed with exit code $exitCode. See $logPath"
    }
    return $lines
}

function Invoke-Capture {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $Root
    )
    $lines = Invoke-Stage -Name $Name -Executable $Executable -Arguments $Arguments -WorkingDirectory $WorkingDirectory
    return ($lines -join [Environment]::NewLine)
}

function Invoke-StageProcess {
    param(
        [string]$Name,
        [string]$Executable,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = $Root,
        [int]$TimeoutSeconds = 300
    )
    $safeName = ($Name -replace '[^A-Za-z0-9_.-]', '_')
    $logPath = if ($null -ne $OutputPath) { Join-Path $OutputPath ($safeName + '.log') } else { $null }
    Write-Event ("COMMAND " + $Name + ": " + $Executable + " " + ($Arguments -join ' '))
    $started = Get-Date
    $argumentString = ($Arguments | ForEach-Object {
        '"' + ([string]$_).Replace('"', '\"') + '"'
    }) -join ' '
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Executable
    $startInfo.Arguments = $argumentString
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $child = [Diagnostics.Process]::new()
    $child.StartInfo = $startInfo
    if (-not $child.Start()) { throw "Unable to start stage process: $Executable" }
    $stdoutTask = $child.StandardOutput.ReadToEndAsync()
    $stderrTask = $child.StandardError.ReadToEndAsync()
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $timedOut = $false
    while (-not $child.HasExited) {
        if ((Get-Date) -ge $deadline) {
            $timedOut = $true
            & taskkill.exe /PID $child.Id /T /F *> $null
            break
        }
        Start-Sleep -Milliseconds 200
    }
    $child.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    $exitCode = if ($timedOut) { 124 } else { $child.ExitCode }
    $child.Dispose()
    $combined = $stdout
    if ($stderr) {
        $separator = if ($combined) { [Environment]::NewLine } else { '' }
        $combined += ($separator + $stderr)
    }
    if ($null -ne $logPath) {
        [IO.File]::WriteAllText($logPath, $combined, (New-Object System.Text.UTF8Encoding($false)))
    }
    $passed = (-not $timedOut -and $exitCode -eq 0)
    $StageResults.Add([pscustomobject]@{
        name = $Name
        command = $Executable + ' ' + ($Arguments -join ' ')
        working_directory = $WorkingDirectory
        exit_code = $exitCode
        passed = $passed
        duration_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
        log = $logPath
    }) | Out-Null
    foreach ($line in ($combined -split "`r?`n")) {
        if ($line) { Write-Host $line }
    }
    if ($timedOut) {
        throw "Stage '$Name' timed out after ${TimeoutSeconds}s. See $logPath"
    }
    if (-not $passed) {
        throw "Stage '$Name' failed with exit code $exitCode. See $logPath"
    }
    return @($combined -split "`r?`n")
}

function Test-RequestedVersion {
    $match = [regex]::Match($Version.Trim(), '^(?<a>[0-9]+)\.(?<b>[0-9]+)\.(?<c>[0-9]+)\.(?<d>[0-9]+)$')
    if (-not $match.Success) {
        throw "Version '$Version' is invalid. Use four numeric components such as 140.0.0.0."
    }
    foreach ($name in @('a','b','c','d')) {
        $number = 0
        if (-not [int]::TryParse($match.Groups[$name].Value, [ref]$number) -or $number -gt 65535) {
            throw "Version '$Version' contains a component outside the Windows/MSIX range 0..65535."
        }
    }
}

function Test-SourceSupport {
    if (-not (Test-Path -LiteralPath $SetVersion)) {
        throw "Source version script is missing: $SetVersion"
    }
    if (-not (Test-Path -LiteralPath $CheckVersion)) {
        throw "Source version check script is missing: $CheckVersion"
    }
    if (-not $SkipWindows) {
        if (-not (Test-Path -LiteralPath $WindowsBuild)) {
            throw "Windows stage requested, but this checkout has no windows\\build.ps1. Use -SkipWindows only when intentionally building Docker without Windows artifacts."
        }
        if (-not $SkipMsix) {
            $msixScript = Join-Path $Root 'windows\build-msix.ps1'
            if (-not (Test-Path -LiteralPath $msixScript) -and $null -eq (Get-CommandPath 'MakeAppx.exe')) {
                throw "MSIX stage requested, but neither windows\\build-msix.ps1 nor MakeAppx.exe exists."
            }
        }
    }
}

function Resolve-InnoCompiler {
    $path = Get-CommandPath 'ISCC.exe'
    if ($null -ne $path) { return $path }
    $candidates = @(
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe'
    )
    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }
    return $null
}

function Resolve-MakeAppx {
    $path = Get-CommandPath 'MakeAppx.exe'
    if ($null -ne $path) { return $path }
    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    if (Test-Path -LiteralPath $kitsRoot) {
        $path = Get-ChildItem -LiteralPath $kitsRoot -Filter 'MakeAppx.exe' -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\MakeAppx\.exe$' } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($null -ne $path) { return $path.FullName }
    }
    return $null
}

function Resolve-SevenZip {
    $path = Get-CommandPath '7z.exe'
    if ($null -ne $path) { return $path }
    $candidates = @(
        (Join-Path $env:ProgramFiles '7-Zip\7z.exe'),
        (Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe')
    )
    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA 'Programs\7-Zip\7z.exe'
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }
    return $null
}

function Test-Tools {
    $tools = [ordered]@{}
    if (-not $SkipTests -or -not $SkipWindows) {
        $pythonPath = Require-Command 'python' 'Python tests and compile checks'
        $pythonMajorMinor = (& $pythonPath -c 'import sys; print(str(sys.version_info.major) + chr(46) + str(sys.version_info.minor))' | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $pythonMajorMinor -notin @('3.11', '3.12', '3.13')) {
            throw "Python 3.11, 3.12, or 3.13 is required; found $pythonMajorMinor at $pythonPath."
        }
        $nodePath = Require-Command 'node' 'frontend build'
        $npmPath = Require-Command 'npm' 'frontend dependency installation/build'
        $tools.python = [ordered]@{path = $pythonPath; version = (Get-ToolVersion $pythonPath)}
        $tools.node = [ordered]@{path = $nodePath; version = (Get-ToolVersion $nodePath)}
        $tools.npm = [ordered]@{path = $npmPath; version = (Get-ToolVersion $npmPath)}
        if (-not $SkipTests) {
            $projectPython = Join-Path $Root '.venv\Scripts\python.exe'
            $testPython = if (Test-Path -LiteralPath $projectPython -PathType Leaf) { $projectPython } else { $pythonPath }
            & $testPython -c 'import aiofiles, apscheduler, celery, fastapi, httpx, orjson, psutil, pydantic, redis' 2>$null
            if ($LASTEXITCODE -ne 0) {
                throw ("Python test dependencies are missing. Prepare the project environment first: " +
                    "python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt httpx==0.27.2")
            }
            $tools.test_python = [ordered]@{path = $testPython; version = (Get-ToolVersion $testPython)}
        }
    }
    if (-not $SkipDocker) {
        $tools.docker = Require-Command 'docker' 'Docker build/Compose validation'
        $composeVersion = Invoke-Capture -Name 'tool-docker-compose-version' -Executable 'docker' -Arguments @('compose', 'version')
        $tools.docker = [ordered]@{path = $tools.docker; version = (Get-ToolVersion $tools.docker)}
        $tools.compose = $composeVersion.Trim()
    }
    if (-not $SkipDocker) {
        $curlPath = Require-Command 'curl.exe' 'Docker API upload/download smoke test'
        $tarPath = Require-Command 'tar' 'Docker archive structural verification'
        $tools.curl = [ordered]@{path = $curlPath; version = (Get-ToolVersion $curlPath)}
        $tools.tar = [ordered]@{path = $tarPath; version = (Get-ToolVersion $tarPath)}
    }
    if (-not $SkipWindows) {
        $inno = Resolve-InnoCompiler
        if ($null -eq $inno) {
            throw "Required tool 'ISCC.exe' is missing (Windows installer build)."
        }
        $tools.inno = [ordered]@{path = $inno; version = (Get-ToolVersion $inno)}
        $bundledFfmpeg = Join-Path $Root 'windows\ffmpeg\bin\ffmpeg.exe'
        $bundledFfprobe = Join-Path $Root 'windows\ffmpeg\bin\ffprobe.exe'
        $hasUsableBundle = $false
        if ((Test-Path -LiteralPath $bundledFfmpeg -PathType Leaf) -and (Test-Path -LiteralPath $bundledFfprobe -PathType Leaf)) {
            $encoderText = (& $bundledFfmpeg -hide_banner -encoders 2>&1 | Out-String)
            $hasUsableBundle = $encoderText -match '\blibsvtav1\b'
        }
        if (-not $hasUsableBundle) {
            $sevenZip = Resolve-SevenZip
            if ($null -eq $sevenZip) {
                throw "Required tool '7z.exe' is missing (Windows build needs to download the bundled FFmpeg archive)."
            }
            $tools.sevenzip = [ordered]@{path = $sevenZip; version = (Get-ToolVersion $sevenZip)}
        }
        $makeAppx = Resolve-MakeAppx
        if (-not $SkipMsix -and $null -eq $makeAppx) {
            $msixBuilder = Join-Path $Root 'windows\build-msix.ps1'
            if (Test-Path -LiteralPath $msixBuilder) {
                $tools.makeappx = [ordered]@{
                    path = 'deferred'
                    version = 'windows\build-msix.ps1 downloads Microsoft.Windows.SDK.BuildTools when needed'
                }
            } else {
                throw "Required tool 'MakeAppx.exe' is missing (MSIX build), and no build-msix.ps1 fallback exists."
            }
        } elseif (-not $SkipMsix) {
            $tools.makeappx = [ordered]@{path = $makeAppx; version = (Get-ToolVersion $makeAppx)}
        }
    }
    $script:ToolVersions = $tools
    return $tools
}

function Get-GitValue {
    param([string[]]$Arguments)
    $value = (& git -C $Root @Arguments 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        return 'unavailable'
    }
    return $value
}

function Read-Version {
    return ([System.IO.File]::ReadAllText((Join-Path $Root 'VERSION'))).Trim()
}

function Run-Tests {
    $packagePath = Join-Path $Root 'frontend\package.json'
    $package = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
    $npm = Get-CommandPath 'npm'
    Invoke-Stage -Name 'frontend-npm-ci' -Executable $npm -Arguments @('ci', '--loglevel=error', '--no-audit', '--no-fund') -WorkingDirectory (Join-Path $Root 'frontend') | Out-Null
    $npx = Get-CommandPath 'npx'
    if ($null -eq $npx) {
        throw 'The npx command is required to run the frontend Svelte check.'
    }
    Invoke-Stage -Name 'frontend-svelte-kit-sync' -Executable $npx -Arguments @('--no-install', 'svelte-kit', 'sync') -WorkingDirectory (Join-Path $Root 'frontend') | Out-Null
    Invoke-Stage -Name 'frontend-svelte-check' -Executable $npx -Arguments @('--no-install', 'svelte-check', '--tsconfig', './tsconfig.json') -WorkingDirectory (Join-Path $Root 'frontend') | Out-Null
    foreach ($scriptName in @('check', 'lint', 'build')) {
        $hasScript = $false
        if ($null -ne $package.scripts) {
            $hasScript = @($package.scripts.PSObject.Properties.Name) -contains $scriptName
        }
        if ($hasScript) {
            Invoke-Stage -Name ('frontend-npm-' + $scriptName) -Executable $npm -Arguments @('run', $scriptName) -WorkingDirectory (Join-Path $Root 'frontend') | Out-Null
        } else {
            Write-Event ('SKIPPED frontend-npm-' + $scriptName + ': package.json has no script')
            $StageResults.Add([pscustomobject]@{name = 'frontend-npm-' + $scriptName; skipped = $true; reason = 'script missing'; passed = $null}) | Out-Null
        }
    }

    $projectPython = Join-Path $Root '.venv\Scripts\python.exe'
    $python = if (Test-Path -LiteralPath $projectPython -PathType Leaf) {
        $projectPython
    } else {
        Get-CommandPath 'python'
    }
    try {
        Invoke-Stage -Name 'python-test-dependencies' -Executable $python -Arguments @(
            '-c',
            'import aiofiles, apscheduler, celery, fastapi, httpx, orjson, psutil, pydantic, redis'
        ) | Out-Null
    } catch {
        throw ("Python test dependencies are missing. Prepare the project environment first: " +
            "python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt httpx==0.27.2")
    }
    $oldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $Root
        $compileDirs = @('backend-api', 'worker', 'shared', 'tests') | ForEach-Object {
            $path = Join-Path $Root $_
            if (Test-Path -LiteralPath $path) { $_ }
        }
        Invoke-Stage -Name 'python-compileall' -Executable $python -Arguments (@('-m', 'compileall', '-q') + $compileDirs) | Out-Null
        $testSuites = @(
            [pscustomobject]@{Directory = 'tests'; PythonPath = $Root},
            [pscustomobject]@{Directory = 'backend-api/tests'; PythonPath = ((Join-Path $Root 'backend-api'), (Join-Path $Root 'worker'), $Root) -join ';'},
            [pscustomobject]@{Directory = 'worker/tests'; PythonPath = $Root},
            [pscustomobject]@{Directory = 'shared/tests'; PythonPath = $Root}
        )
        foreach ($suite in $testSuites) {
            $testDir = $suite.Directory
            $fullTestDir = Join-Path $Root $testDir
            $testFiles = @(Get-ChildItem -LiteralPath $fullTestDir -Recurse -File -Filter 'test*.py' -ErrorAction SilentlyContinue)
            if ($testFiles.Count -gt 0) {
                $env:PYTHONPATH = $suite.PythonPath
                Invoke-Stage -Name ('python-unittest-' + ($testDir -replace '[\\/]', '-')) -Executable $python -Arguments @('-m', 'unittest', 'discover', '-s', $testDir, '-p', 'test*.py') | Out-Null
            } else {
                Write-Event ('NOT APPLICABLE python tests: no test*.py files under ' + $testDir)
                $StageResults.Add([pscustomobject]@{name = 'python-unittest-' + $testDir; skipped = $false; not_applicable = $true; reason = 'no matching tests in repository'; passed = $true}) | Out-Null
            }
        }
    } finally {
        $env:PYTHONPATH = $oldPythonPath
    }

    Invoke-Stage -Name 'version-consistency' -Executable 'powershell.exe' -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $CheckVersion, '-RepositoryRoot', $Root) | Out-Null
    if (-not $SkipDocker) {
        $oldComposeVersion = $env:APP_VERSION
        $env:APP_VERSION = $Version
        try {
            foreach ($composeFile in @('docker-compose.yml', 'docker-compose.cpu.yml', 'docker-compose.vaapi.yml')) {
                Invoke-Stage -Name ('docker-compose-config-' + ($composeFile -replace '[^A-Za-z0-9]', '-')) -Executable 'docker' -Arguments @('compose', '-f', $composeFile, 'config') | Out-Null
            }
        } finally {
            $env:APP_VERSION = $oldComposeVersion
        }
    } else {
        Write-Event 'SKIPPED docker-compose-config by -SkipDocker'
        $StageResults.Add([pscustomobject]@{name = 'docker-compose-config'; skipped = $true; reason = '-SkipDocker'; passed = $null}) | Out-Null
    }
}

function Copy-Artifact {
    param([string]$Source, [string]$DestinationName)
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Expected artifact does not exist: $Source"
    }
    $destination = Join-Path $OutputPath $DestinationName
    if ([IO.Path]::GetFullPath($Source) -ne [IO.Path]::GetFullPath($destination)) {
        Copy-Item -LiteralPath $Source -Destination $destination -Force
    }
    $item = Get-Item -LiteralPath $destination
    if ($item.Length -le 0) {
        throw "Artifact is empty: $destination"
    }
    return $destination
}

function Run-WindowsBuild {
    $scriptText = Get-Content -LiteralPath $WindowsBuild -Raw
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $WindowsBuild)
    if ($scriptText -match '(?im)\$Version') {
        $args += @('-Version', $Version)
    }
    if ($scriptText -match '(?im)\$OutputDir') {
        $args += @('-OutputDir', $OutputPath)
    }
    $oldAppVersion = $env:APP_VERSION
    $env:APP_VERSION = $Version
    try {
        Invoke-StageProcess -Name 'windows-build' -TimeoutSeconds 3600 -Executable 'powershell.exe' -Arguments $args | Out-Null
    } finally {
        $env:APP_VERSION = $oldAppVersion
    }

    $portablePath = Copy-Artifact (Join-Path $OutputPath '8mblocal.exe') '8mblocal.exe'
    $installerPath = Copy-Artifact (Join-Path $OutputPath '8mblocal-Setup.exe') '8mblocal-Setup.exe'
    $portableInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($portablePath)
    $installerInfo = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($installerPath)
    foreach ($info in @($portableInfo, $installerInfo)) {
        $fileVersion = Get-NormalizedFourPartVersion $info.FileVersion
        $productVersion = Get-NormalizedFourPartVersion $info.ProductVersion
        if ($fileVersion -ne $Version -or $productVersion -ne $Version) {
            throw "Windows artifact metadata versions FileVersion='$($info.FileVersion)' ProductVersion='$($info.ProductVersion)' do not exactly match $Version."
        }
    }
    $Artifacts.Add([pscustomobject]@{name = 'portable EXE'; path = $portablePath; size = (Get-Item $portablePath).Length; version = $portableInfo.FileVersion}) | Out-Null
    $Artifacts.Add([pscustomobject]@{name = 'installer EXE'; path = $installerPath; size = (Get-Item $installerPath).Length; version = $installerInfo.FileVersion}) | Out-Null

    if (-not $SkipTests) {
        $smokeScript = Join-Path $Root 'windows\test-release.ps1'
        if (-not (Test-Path -LiteralPath $smokeScript -PathType Leaf)) {
            throw "Windows smoke-test helper is missing: $smokeScript"
        }
        $portableSmokeData = Join-Path $OutputPath 'portable-smoke-data'
        $oldSmokeAuthEnabled = $env:AUTH_ENABLED
        $oldConfiguredAuthUser = $env:AUTH_USER
        $oldConfiguredAuthPass = $env:AUTH_PASS
        $oldSmokeAuthUser = $env:RELEASE_SMOKE_AUTH_USER
        $oldSmokeAuthPass = $env:RELEASE_SMOKE_AUTH_PASS
        $smokePassword = [guid]::NewGuid().ToString('N')
        $env:AUTH_ENABLED = 'true'
        $env:AUTH_USER = 'release-smoke'
        $env:AUTH_PASS = $smokePassword
        $env:RELEASE_SMOKE_AUTH_USER = 'release-smoke'
        # Use a per-run synthetic password only in the child-process environment;
        # it is never written to a command, file, or test log.
        $env:RELEASE_SMOKE_AUTH_PASS = $smokePassword
        try {
            Invoke-StageProcess -Name 'windows-portable-smoke' -TimeoutSeconds 300 -Executable 'powershell.exe' -Arguments @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $smokeScript,
                '-ExePath', $portablePath, '-Port', (Get-FreePort), '-DataDir', $portableSmokeData,
                '-ExpectedVersion', $Version, '-TestAuth', '-TestSettings'
            ) | Out-Null
            $installerSmokeData = Join-Path $OutputPath 'installer-smoke-data'
            Invoke-StageProcess -Name 'windows-installer-smoke' -TimeoutSeconds 300 -Executable 'powershell.exe' -Arguments @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $smokeScript,
                '-Install', '-InstallMode', 'current-user', '-InstallerPath', $installerPath,
                '-Port', (Get-FreePort), '-DataDir', $installerSmokeData, '-ExpectedVersion', $Version, '-TestAuth', '-TestSettings'
            ) | Out-Null
        } finally {
            $env:AUTH_ENABLED = $oldSmokeAuthEnabled
            $env:AUTH_USER = $oldConfiguredAuthUser
            $env:AUTH_PASS = $oldConfiguredAuthPass
            $env:RELEASE_SMOKE_AUTH_USER = $oldSmokeAuthUser
            $env:RELEASE_SMOKE_AUTH_PASS = $oldSmokeAuthPass
        }
    } else {
        foreach ($name in @('windows-portable-smoke', 'windows-installer-smoke')) {
            Write-Event ('SKIPPED ' + $name + ' by -SkipTests')
            $StageResults.Add([pscustomobject]@{name = $name; skipped = $true; reason = '-SkipTests'; passed = $null}) | Out-Null
        }
    }

    if (-not $SkipMsix) {
        $msixPath = Join-Path $OutputPath ('8mblocal_' + $Version + '_x64.msix')
        if (-not (Test-Path -LiteralPath $msixPath -PathType Leaf)) {
            $msixScript = Join-Path $Root 'windows\build-msix.ps1'
            if (Test-Path -LiteralPath $msixScript) {
                Invoke-StageProcess -Name 'windows-msix-build' -TimeoutSeconds 1800 -Executable 'powershell.exe' -Arguments @(
                    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $msixScript,
                    '-Version', $Version, '-ExePath', $portablePath, '-OutputPath', $msixPath,
                    '-PackageIdentityName', $StorePackageIdentityName,
                    '-Publisher', $StorePublisher,
                    '-PublisherDisplayName', $StorePublisherDisplayName,
                    '-StoreSubmission'
                ) | Out-Null
            }
        }
        $msixPath = Copy-Artifact $msixPath ('8mblocal_' + $Version + '_x64.msix')
        Test-MsixArtifact -Path $msixPath
        $Artifacts.Add([pscustomobject]@{name = 'MSIX'; path = $msixPath; size = (Get-Item $msixPath).Length}) | Out-Null
    } else {
        Write-Event 'SKIPPED MSIX by -SkipMsix'
        $StageResults.Add([pscustomobject]@{name = 'windows-msix'; skipped = $true; reason = '-SkipMsix'; passed = $null}) | Out-Null
    }
}

function Test-MsixArtifact {
    param([Parameter(Mandatory = $true)][string]$Path)

    $inspectDir = Join-Path $OutputPath ('.msix-inspect-' + [guid]::NewGuid().ToString('N'))
    $archivePath = Join-Path $OutputPath ('.msix-inspect-' + [guid]::NewGuid().ToString('N') + '.zip')
    New-Item -ItemType Directory -Force -Path $inspectDir | Out-Null
    try {
        # Windows PowerShell's Expand-Archive only accepts the .zip extension,
        # while an MSIX package is a ZIP container. Copy to a unique temporary
        # .zip name for inspection without changing the release artifact.
        Copy-Item -LiteralPath $Path -Destination $archivePath -Force
        Expand-Archive -LiteralPath $archivePath -DestinationPath $inspectDir -Force
        $manifestFile = Get-ChildItem -LiteralPath $inspectDir -Recurse -File -Filter 'AppxManifest.xml' | Select-Object -First 1
        if ($null -eq $manifestFile) {
            throw 'MSIX does not contain AppxManifest.xml.'
        }
        $manifestText = [System.IO.File]::ReadAllText($manifestFile.FullName)
        try {
            [xml]$manifestXml = $manifestText
        } catch {
            throw "MSIX manifest is not valid XML: $($_.Exception.Message)"
        }
        $namespaceManager = New-Object System.Xml.XmlNamespaceManager($manifestXml.NameTable)
        $namespaceManager.AddNamespace('f', 'http://schemas.microsoft.com/appx/manifest/foundation/windows10')
        $namespaceManager.AddNamespace('uap', 'http://schemas.microsoft.com/appx/manifest/uap/windows10')
        $namespaceManager.AddNamespace('rescap', 'http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities')
        $identity = $manifestXml.SelectSingleNode('/f:Package/f:Identity', $namespaceManager)
        if ($null -eq $identity) { throw 'MSIX manifest is missing its package Identity.' }
        if ($identity.Name -ne $StorePackageIdentityName) { throw "MSIX package identity '$($identity.Name)' does not match '$StorePackageIdentityName'." }
        if ($identity.Publisher -ne $StorePublisher) { throw "MSIX publisher '$($identity.Publisher)' does not match the configured Store publisher." }
        if ($identity.ProcessorArchitecture -ne 'x64') { throw "MSIX architecture '$($identity.ProcessorArchitecture)' is not x64." }
        if ($identity.Version -ne $Version) { throw "MSIX manifest version '$($identity.Version)' does not match $Version." }
        $publisherDisplay = $manifestXml.SelectSingleNode('/f:Package/f:Properties/f:PublisherDisplayName', $namespaceManager)
        if ($null -eq $publisherDisplay -or $publisherDisplay.InnerText -ne $StorePublisherDisplayName) {
            throw 'MSIX publisher display name does not match the configured Store publisher display name.'
        }
        $runFullTrust = $manifestXml.SelectNodes('/f:Package/f:Capabilities/rescap:Capability[@Name="runFullTrust"]', $namespaceManager)
        if ($null -eq $runFullTrust -or $runFullTrust.Count -ne 1) { throw 'MSIX must contain exactly one runFullTrust capability.' }
        if ($manifestText -match '(?i)unvirtualizedResources|FileSystemWriteVirtualization') {
            throw 'MSIX manifest contains forbidden unvirtualizedResources or FileSystemWriteVirtualization capability.'
        }
        Write-Event ('MSIX manifest validated: identity=' + $identity.Name + '; publisher=' + $identity.Publisher + '; architecture=x64; version=' + $Version + '; runFullTrust=1; forbidden-capabilities=absent')
    } finally {
        if (Test-Path -LiteralPath $inspectDir) {
            Remove-Item -LiteralPath $inspectDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $archivePath) {
            Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-FreePort {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()
    return $port
}

function Run-DockerBuild {
    $docker = Require-Command 'docker' 'Docker build'
    $tar = Require-Command 'tar' 'Docker archive structural verification'
    $commit = Get-GitValue @('rev-parse', 'HEAD')
    $imageTag = 'jms1717/8mblocal:local-' + $Version
    $buildArgs = @(
        'build',
        '--build-arg', ('BUILD_VERSION=' + $Version),
        '--build-arg', ('BUILD_COMMIT=' + $commit),
        '--build-arg', ('BUILD_TIMESTAMP=' + $Timestamp),
        '--build-arg', 'BUILD_REPOSITORY=https://github.com/JMS1717/8mb.local',
        '--label', ('org.opencontainers.image.version=' + $Version),
        '--label', ('org.opencontainers.image.revision=' + $commit),
        '--label', ('org.opencontainers.image.created=' + $Timestamp),
        '--label', 'org.opencontainers.image.source=https://github.com/JMS1717/8mb.local',
        '-t', $imageTag,
        '.'
    )
    Invoke-StageProcess -Name 'docker-build' -TimeoutSeconds 3600 -Executable $docker -Arguments $buildArgs | Out-Null

    $labelText = Invoke-Capture -Name 'docker-metadata-labels' -Executable $docker -Arguments @('image', 'inspect', $imageTag, '--format', '{{json .Config.Labels}}')
    $labels = $labelText.Trim() | ConvertFrom-Json
    if ($labels.'org.opencontainers.image.version' -ne $Version) {
        throw "Docker image version label '$($labels.'org.opencontainers.image.version')' does not match $Version."
    }
    if ($labels.'org.opencontainers.image.revision' -ne $commit) {
        throw "Docker image revision label does not match commit $commit."
    }
    if ($labels.'org.opencontainers.image.source' -ne 'https://github.com/JMS1717/8mb.local') {
        throw 'Docker image source label does not match the repository URL.'
    }
    $envText = Invoke-Capture -Name 'docker-metadata-env' -Executable $docker -Arguments @('image', 'inspect', $imageTag, '--format', '{{json .Config.Env}}')
    $imageEnv = $envText.Trim() | ConvertFrom-Json
    $expectedVersionEnv = 'APP_VERSION=' + $Version
    if (-not @($imageEnv | Where-Object { $_ -eq $expectedVersionEnv })) {
        throw "Docker image APP_VERSION environment value does not match $Version."
    }

    $dockerArchive = Join-Path $OutputPath '8mblocal-docker.tar'
    Invoke-StageProcess -Name 'docker-save' -TimeoutSeconds 1800 -Executable $docker -Arguments @('save', '-o', $dockerArchive, $imageTag) | Out-Null
    if ((Get-Item $dockerArchive).Length -le 0) { throw 'docker save produced an empty archive.' }
    # The image was already inspected and will be run below. Validate the saved
    # archive structurally instead of loading it back into the same daemon,
    # which is redundant and has caused Docker Desktop API failures.
    $archiveListText = Invoke-Capture -Name 'docker-archive-list' -Executable $tar -Arguments @('-tf', $dockerArchive)
    $archiveEntries = @($archiveListText -split '\r?\n' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    foreach ($requiredEntry in @('manifest.json', 'index.json', 'oci-layout')) {
        if ($archiveEntries -notcontains $requiredEntry) {
            throw "Docker archive is missing required entry '$requiredEntry'."
        }
    }
    $manifestText = Invoke-Capture -Name 'docker-archive-manifest' -Executable $tar -Arguments @('-xOf', $dockerArchive, 'manifest.json')
    try {
        $manifestEntries = @($manifestText.Trim() | ConvertFrom-Json)
    } catch {
        throw "Docker archive manifest.json is invalid JSON: $($_.Exception.Message)"
    }
    $matchingManifest = $manifestEntries | Where-Object { @($_.RepoTags) -contains $imageTag } | Select-Object -First 1
    if ($null -eq $matchingManifest) {
        throw "Docker archive manifest does not contain image tag $imageTag."
    }
    $referencedEntries = @($matchingManifest.Config) + @($matchingManifest.Layers)
    if ([string]::IsNullOrWhiteSpace([string]$matchingManifest.Config) -or @($matchingManifest.Layers).Count -eq 0) {
        throw 'Docker archive manifest is missing its config or layers.'
    }
    foreach ($entry in $referencedEntries) {
        $normalizedEntry = ([string]$entry).Replace('\', '/')
        if ($archiveEntries -notcontains $normalizedEntry) {
            throw "Docker archive manifest references missing entry '$normalizedEntry'."
        }
    }

    $script:DockerTemp = Join-Path $OutputPath 'docker-test'
    $configDir = Join-Path $DockerTemp 'config'
    New-Item -ItemType Directory -Force -Path (Join-Path $DockerTemp 'uploads'),(Join-Path $DockerTemp 'outputs'),$configDir | Out-Null
    # Match the supported Compose layout: mount one test-only configuration
    # directory and let the application recreate missing files inside it.
    $settingsFile = Join-Path $configDir 'settings.json'
    $historyFile = Join-Path $configDir 'history.json'
    $envFile = Join-Path $configDir '.env'
    [System.IO.File]::WriteAllText($settingsFile, '{}', (New-Object System.Text.UTF8Encoding($false)))
    [System.IO.File]::WriteAllText($historyFile, '[]', (New-Object System.Text.UTF8Encoding($false)))
    [System.IO.File]::WriteAllText($envFile, "AUTH_ENABLED=false`n", (New-Object System.Text.UTF8Encoding($false)))
    $port = Get-FreePort
    $script:DockerContainer = '8mblocal-local-' + ([guid]::NewGuid().ToString('N').Substring(0, 10))
    $runArguments = @(
        'run', '-d', '--rm', '--name', $DockerContainer,
        '--publish', ('127.0.0.1:' + $port + ':8001'),
        '--mount', ('type=bind,source=' + (Join-Path $DockerTemp 'uploads') + ',target=/app/uploads'),
        '--mount', ('type=bind,source=' + (Join-Path $DockerTemp 'outputs') + ',target=/app/outputs'),
        '--mount', ('type=bind,source=' + $configDir + ',target=/app/config'),
        '--env', 'AUTH_ENABLED=false',
        '--env', 'ENV_FILE=/app/config/.env',
        '--env', 'SETTINGS_FILE=/app/config/settings.json',
        '--env', 'HISTORY_FILE=/app/config/history.json',
        '--env', ('APP_VERSION=' + $Version),
        '--env', 'TMPDIR=/app/uploads/.tmp',
        $imageTag
    )
    Invoke-Stage -Name 'docker-run' -Executable $docker -Arguments $runArguments | Out-Null
    $script:DockerContainerStarted = $true
    try {
        $healthy = $false
        for ($i = 0; $i -lt 60; $i++) {
            try {
                $health = Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $port + '/healthz') -TimeoutSec 3
                if ($health.StatusCode -eq 200) { $healthy = $true; break }
            } catch {}
            Start-Sleep -Seconds 1
        }
        if (-not $healthy) {
            Invoke-Stage -Name 'docker-logs-on-health-failure' -Executable $docker -Arguments @('logs', $DockerContainer) -AllowFailure | Out-Null
            throw 'Docker container did not become healthy within 60 seconds.'
        }
        $versionJson = Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $port + '/api/version') -TimeoutSec 10
        $reported = ($versionJson.Content | ConvertFrom-Json).version
        if ($reported -ne $Version) { throw "Docker API reported $reported instead of $Version." }
        $frontend = Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $port + '/') -TimeoutSec 10
        if ($frontend.StatusCode -ne 200) { throw 'Docker frontend request failed.' }
        $inputFile = Join-Path $DockerTemp 'input.mp4'
        Invoke-StageProcess -Name 'docker-synthetic-input' -TimeoutSeconds 120 -Executable $docker -Arguments @(
            'run', '--rm', '--entrypoint', 'ffmpeg',
            '--mount', ('type=bind,source=' + $DockerTemp + ',target=/work'),
            $imageTag, '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=10',
            '-f', 'lavfi', '-i', 'sine=frequency=1000:sample_rate=44100',
            '-t', '1', '-c:v', 'libx264', '-c:a', 'aac', '/work/input.mp4'
        ) | Out-Null
        $uploadText = Invoke-Capture -Name 'docker-upload' -Executable 'curl.exe' -Arguments @('-sS', '-f', '-F', ('file=@' + $inputFile), ('http://127.0.0.1:' + $port + '/api/upload?target_size_mb=1&audio_bitrate_kbps=64'))
        $upload = $uploadText | ConvertFrom-Json
        $body = @{
            job_id = $upload.job_id
            filename = $upload.filename
            target_size_mb = 1
            video_codec = 'libx264'
            audio_codec = 'aac'
            audio_bitrate_kbps = 64
            preset = 'p3'
            container = 'mp4'
            tune = 'hq'
        } | ConvertTo-Json -Compress
        $bodyPath = Join-Path $DockerTemp 'compress.json'
        [System.IO.File]::WriteAllText($bodyPath, $body, (New-Object System.Text.UTF8Encoding($false)))
        # Passing JSON inline through Windows PowerShell can strip the JSON
        # quotes during native argument marshalling. Send a file instead.
        $compressText = Invoke-Capture -Name 'docker-compress' -Executable 'curl.exe' -Arguments @('-sS', '-f', '-H', 'Content-Type: application/json', '--data-binary', ('@' + $bodyPath), ('http://127.0.0.1:' + $port + '/api/compress'))
        $task = $compressText | ConvertFrom-Json
        $complete = $false
        for ($i = 0; $i -lt 120; $i++) {
            Start-Sleep -Milliseconds 500
            try {
                $status = (Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $port + '/api/jobs/' + $task.task_id + '/status') -TimeoutSec 5).Content | ConvertFrom-Json
                if ($status.state -eq 'SUCCESS' -or $status.state -eq 'completed') { $complete = $true; break }
                if ($status.state -eq 'FAILURE' -or $status.state -eq 'failed') { throw ('Docker compression failed: ' + ($status | ConvertTo-Json -Compress)) }
            } catch {
                if ($_.Exception.Message -like 'Docker compression failed:*') { throw }
            }
        }
        if (-not $complete) { throw 'Docker compression did not complete within 60 seconds.' }
        $downloadFile = Join-Path $DockerTemp 'output.mp4'
        Invoke-Stage -Name 'docker-download' -Executable 'curl.exe' -Arguments @('-sS', '-f', ('http://127.0.0.1:' + $port + '/api/jobs/' + $task.task_id + '/download'), '-o', $downloadFile) | Out-Null
        $probeLines = Invoke-StageProcess -Name 'docker-ffprobe' -TimeoutSeconds 120 -Executable $docker -Arguments @(
            'run', '--rm', '--entrypoint', 'ffprobe',
            '--mount', ('type=bind,source=' + $DockerTemp + ',target=/work'),
            $imageTag, '-v', 'error', '-show_entries', 'format=duration:stream=codec_type',
            '-of', 'json', '/work/output.mp4'
        )
        try {
            $probe = (($probeLines -join [Environment]::NewLine) | ConvertFrom-Json)
        } catch {
            throw "Docker FFprobe returned invalid JSON: $($_.Exception.Message)"
        }
        $duration = 0.0
        [double]::TryParse([string]$probe.format.duration, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$duration) | Out-Null
        $hasVideo = @($probe.streams | Where-Object { $_.codec_type -eq 'video' }).Count -gt 0
        if ($duration -le 0 -or -not $hasVideo) {
            throw "Docker output failed FFprobe integrity validation (duration=$duration, video_stream=$hasVideo)."
        }
    } finally {
        Invoke-Stage -Name 'docker-stop' -Executable $docker -Arguments @('rm', '-f', $DockerContainer) -AllowFailure | Out-Null
        $script:DockerContainerStarted = $false
    }
    $Artifacts.Add([pscustomobject]@{name = 'Docker image tar'; path = $dockerArchive; size = (Get-Item $dockerArchive).Length; image_tag = $imageTag}) | Out-Null
}

function Write-Outputs {
    $skippedCount = @($StageResults | Where-Object { $_.skipped }).Count
    if ($null -ne $Failure) {
        $result = 'FAILED'
        $releaseReady = $false
    } elseif ($skippedCount -gt 0) {
        $result = 'INCOMPLETE'
        $releaseReady = $false
    } else {
        $result = 'PASS'
        $releaseReady = $true
    }

    $artifactRecords = @(
        foreach ($artifact in $Artifacts.ToArray()) {
            $artifactItem = Get-Item -LiteralPath $artifact.path
            $record = [ordered]@{
                name = $artifact.name
                path = $artifact.path
                size = $artifactItem.Length
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $artifactItem.FullName).Hash
            }
            if ($artifact.PSObject.Properties['image_tag']) {
                $record.image_tag = $artifact.image_tag
            }
            if ($artifact.PSObject.Properties['version']) {
                $record.version = $artifact.version
            }
            [pscustomobject]$record
        }
    )

    $manifest = [ordered]@{
        source_path = $Root
        branch = Get-GitValue @('branch', '--show-current')
        commit = Get-GitValue @('rev-parse', 'HEAD')
        dirty = ((& git -C $Root status --porcelain 2>$null | Out-String).Trim().Length -gt 0)
        requested_version = $Version
        version_file = Read-Version
        result = $result
        release_ready = $releaseReady
        started_utc = $Timestamp
        completed_utc = (Get-Date).ToUniversalTime().ToString('o')
        tools = $script:ToolVersions
        options = [ordered]@{
            skip_tests = [bool]$SkipTests
            skip_docker = [bool]$SkipDocker
            skip_windows = [bool]$SkipWindows
            skip_msix = [bool]$SkipMsix
            keep_temp = [bool]$KeepTemp
        }
        # Windows PowerShell 5.1 cannot JSON-serialize a generic List nested
        # inside an ordered dictionary. Convert the lists to real arrays.
        stages = @($StageResults.ToArray())
        artifacts = $artifactRecords
        failure = $Failure
    }
    $manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $OutputPath 'BUILD-MANIFEST.json') -Encoding UTF8
    $hashLines = New-Object System.Collections.Generic.List[string]
    foreach ($artifact in ($artifactRecords | Sort-Object path)) {
        $hashLines.Add(($artifact.sha256 + '  ' + [IO.Path]::GetFileName($artifact.path))) | Out-Null
    }
    $hashLines | Set-Content -LiteralPath (Join-Path $OutputPath 'SHA256SUMS.txt') -Encoding ASCII
    $report = New-Object System.Collections.Generic.List[string]
    $report.Add('# Local release test results') | Out-Null
    $report.Add('') | Out-Null
    $report.Add(('Version: ' + $Version)) | Out-Null
    $report.Add(('Source: ' + $Root)) | Out-Null
    $report.Add(('Commit: ' + (Get-GitValue @('rev-parse', 'HEAD')))) | Out-Null
    $report.Add(('Dirty at report time: ' + $manifest.dirty)) | Out-Null
    $report.Add('') | Out-Null
    if ($null -ne $Failure) {
        $report.Add(('Result: FAILED - ' + $Failure)) | Out-Null
    } else {
        $report.Add(('Result: ' + $result)) | Out-Null
    }
    $report.Add(('Release-ready: ' + $releaseReady)) | Out-Null
    $report.Add('') | Out-Null
    $report.Add('## Stages') | Out-Null
    foreach ($stage in $StageResults) {
        if ($stage.not_applicable) { $report.Add(('- ' + $stage.name + ': NOT APPLICABLE (' + $stage.reason + ')')) | Out-Null }
        elseif ($stage.skipped) { $report.Add(('- ' + $stage.name + ': SKIPPED (' + $stage.reason + ')')) | Out-Null }
        else { $report.Add(('- ' + $stage.name + ': ' + ($(if ($stage.passed) { 'PASS' } else { 'FAIL' })))) | Out-Null }
    }
    $report.Add('') | Out-Null
    $report.Add('## Artifacts') | Out-Null
    foreach ($artifact in $artifactRecords) {
        $report.Add(('- ' + $artifact.name + ': ' + $artifact.path + ' (' + $artifact.size + ' bytes; SHA-256 ' + $artifact.sha256 + ')')) | Out-Null
    }
    $report.Add('') | Out-Null
    if ($null -ne $Failure) {
        $report.Add('The release build failed before all required stages completed; inspect the failure and stage logs above.') | Out-Null
    } elseif ($skippedCount -gt 0) {
        $report.Add('Required stages were skipped; this output is not release-ready.') | Out-Null
    } else {
        $report.Add('All required stages completed.') | Out-Null
    }
    $report | Set-Content -LiteralPath (Join-Path $OutputPath 'TEST-RESULTS.md') -Encoding UTF8
}

try {
    Test-RequestedVersion
    Test-SourceSupport
    if ($DryRun) {
        $toolVersions = Test-Tools
        Write-Event ('DRY RUN version=' + $Version)
        Write-Event ('PLAN: synchronize VERSION and generated app data')
        if ($SkipTests) { Write-Event 'PLAN: skip automated tests' } else { Write-Event 'PLAN: run automated tests' }
        if ($SkipWindows) { Write-Event 'PLAN: skip Windows artifacts' } else { Write-Event 'PLAN: build portable EXE, installer, and MSIX' }
        if ($SkipDocker) { Write-Event 'PLAN: skip Docker build/test/tar' } else { Write-Event 'PLAN: build/test/save Docker image' }
        Write-Event 'DRY RUN PASSED'
        exit 0
    }

    if ([string]::IsNullOrWhiteSpace($OutputDir)) {
        $OutputDir = Join-Path $Root ('dist\release\' + $Version)
    }
    $OutputPath = [System.IO.Path]::GetFullPath($OutputDir)
    # Apply containment checks before checking whether the destination exists;
    # a new path inside the checkout must be just as protected as an existing
    # one.
    $rootWithSeparator = $Root.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $generatedRoot = (Join-Path $Root 'dist').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $outputRoot = [IO.Path]::GetPathRoot($OutputPath)
    if ($OutputPath -eq $Root -or
        $Root.StartsWith($OutputPath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $OutputPath -eq $outputRoot) {
        throw "Refusing to overwrite or create a source/ancestor directory: $OutputPath"
    }
    if ($OutputPath.StartsWith($rootWithSeparator, [StringComparison]::OrdinalIgnoreCase) -and
        -not $OutputPath.StartsWith($generatedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to write release artifacts into a source subdirectory: $OutputPath. Use a path outside the checkout or under dist."
    }
    if (Test-Path -LiteralPath $OutputPath) {
        if (-not $Overwrite) {
            throw "Output directory already exists: $OutputPath. Use -Overwrite explicitly to reuse it."
        }
        if (Test-Path -LiteralPath (Join-Path $OutputPath '.git')) {
            throw "Refusing to overwrite a Git checkout: $OutputPath"
        }
        $markerPath = Join-Path $OutputPath $ReleaseMarkerName
        if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf) -or
            ([IO.File]::ReadAllText($markerPath)).Trim() -ne $ReleaseMarkerValue) {
            throw "Refusing to overwrite an unmarked directory: $OutputPath. Choose a new output path; only directories created by this script can be reused with -Overwrite."
        }
        Remove-Item -LiteralPath $OutputPath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $OutputPath | Out-Null
    [IO.File]::WriteAllText((Join-Path $OutputPath $ReleaseMarkerName), $ReleaseMarkerValue, (New-Object System.Text.UTF8Encoding($false)))
    Write-Event ('OUTPUT=' + $OutputPath)

    $toolVersions = Test-Tools

    Invoke-Stage -Name 'set-version' -Executable 'powershell.exe' -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $SetVersion, '-Version', $Version, '-RepositoryRoot', $Root) | Out-Null
    Invoke-Stage -Name 'check-version' -Executable 'powershell.exe' -Arguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $CheckVersion, '-RepositoryRoot', $Root) | Out-Null
    if (-not $SkipTests) {
        Run-Tests
    } else {
        Write-Event 'SKIPPED all automated tests by -SkipTests'
        $StageResults.Add([pscustomobject]@{name = 'automated-tests'; skipped = $true; reason = '-SkipTests'; passed = $null}) | Out-Null
    }
    if (-not $SkipWindows) {
        Run-WindowsBuild
    } else {
        Write-Event 'SKIPPED Windows artifacts by -SkipWindows'
        $StageResults.Add([pscustomobject]@{name = 'windows-artifacts'; skipped = $true; reason = '-SkipWindows'; passed = $null}) | Out-Null
    }
    if (-not $SkipDocker) {
        Run-DockerBuild
    } else {
        Write-Event 'SKIPPED Docker by -SkipDocker'
        $StageResults.Add([pscustomobject]@{name = 'docker-artifacts'; skipped = $true; reason = '-SkipDocker'; passed = $null}) | Out-Null
    }
    if (@($StageResults | Where-Object { $_.skipped }).Count -gt 0) {
        Write-Event 'LOCAL RELEASE BUILD COMPLETED WITH SKIPPED STAGES (NOT RELEASE-READY)'
    } else {
        Write-Event 'LOCAL RELEASE BUILD PASSED'
    }
} catch {
    $Failure = $_.Exception.Message
    Write-Event ('LOCAL RELEASE BUILD FAILED: ' + $Failure)
    if ($null -ne $DockerContainer -and $DockerContainerStarted) {
        & docker rm -f $DockerContainer 2>$null | Out-Null
        $script:DockerContainerStarted = $false
    }
    if ($null -ne $OutputPath) {
        Write-Outputs
    }
    exit 1
} finally {
    if ($null -ne $OutputPath -and $null -eq $Failure) {
        Write-Outputs
    }
    if ($null -ne $DockerTemp -and -not $KeepTemp -and (Test-Path -LiteralPath $DockerTemp)) {
        Remove-Item -LiteralPath $DockerTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
