[CmdletBinding()]
param(
    [string]$RepositoryRoot
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$Root = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $RepositoryRoot).Path)
$Failures = New-Object System.Collections.Generic.List[string]

function Join-RepoPath {
    param([string[]]$Parts)
    $path = $Root
    foreach ($part in $Parts) {
        $path = Join-Path $path $part
    }
    return $path
}

function Fail([string]$message) {
    $Failures.Add($message) | Out-Null
}

$versionPath = Join-RepoPath @('VERSION')
if (-not (Test-Path -LiteralPath $versionPath)) {
    throw "VERSION is missing: $versionPath"
}
$FullVersion = ([System.IO.File]::ReadAllText($versionPath)).Trim()
$match = [regex]::Match($FullVersion, '^(?<a>[0-9]+)\.(?<b>[0-9]+)\.(?<c>[0-9]+)\.(?<d>[0-9]+)$')
if (-not $match.Success) {
    Fail "VERSION is not a full four-part numeric version: '$FullVersion'"
} else {
    foreach ($name in @('a','b','c','d')) {
        $component = 0
        if (-not [int]::TryParse($match.Groups[$name].Value, [ref]$component) -or $component -gt 65535) {
            Fail "VERSION component '$name' is outside 0..65535."
        }
    }
}
if ($FullVersion) {
    $Parts = $FullVersion.Split('.')
    $DisplayVersion = ($Parts[0..2] -join '.')
}

function Require-Text([string]$Path, [string]$Pattern, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Path)) {
        Fail "$Description is missing: $Path"
        return $null
    }
    $text = [System.IO.File]::ReadAllText($Path)
    if ($text -notmatch $Pattern) {
        Fail "$Description does not contain the expected version data."
    }
    return $text
}

$frontendText = Require-Text (Join-RepoPath @('frontend', 'src', 'lib', 'generated-version.ts')) ("export const APP_VERSION = '" + [regex]::Escape($FullVersion) + "';") 'Frontend generated version'
$backendText = Require-Text (Join-RepoPath @('backend-api', 'app', 'version.py')) ('APP_VERSION\s*=\s*"' + [regex]::Escape($FullVersion) + '"') 'Backend generated version'
$configText = Require-Text (Join-RepoPath @('backend-api', 'app', 'config.py')) 'from \.version import APP_VERSION as GENERATED_APP_VERSION' 'Backend generated-version import'
if ($configText -and $configText -notmatch 'APP_VERSION:\s*str\s*=\s*Field\(default=GENERATED_APP_VERSION\)') {
    Fail 'Backend APP_VERSION default is not derived from the generated version module.'
}
$systemText = Require-Text (Join-RepoPath @('backend-api', 'app', 'routers', 'system.py')) 'return\s+\{"version":\s*settings\.APP_VERSION\}' 'Backend version endpoint'
$pageText = Require-Text (Join-RepoPath @('frontend', 'src', 'routes', '+page.svelte')) 'generated-version' 'Frontend UI version import'

$dockerfilePath = Join-RepoPath @('Dockerfile')
$dockerText = Require-Text $dockerfilePath 'ARG\s+BUILD_VERSION' 'Docker build version argument'
if ($dockerText -and $dockerText -notmatch ('(?m)^\s*ARG\s+BUILD_VERSION\s*=\s*' + [regex]::Escape($FullVersion) + '\s*\r?$')) {
    Fail 'Dockerfile BUILD_VERSION default is not synchronized with VERSION.'
}
foreach ($composeName in @('docker-compose.yml', 'docker-compose.cpu.yml', 'docker-compose.vaapi.yml')) {
    $composePath = Join-RepoPath @($composeName)
    if (Test-Path -LiteralPath $composePath) {
        $composeText = [IO.File]::ReadAllText($composePath)
        $expectedComposeVersion = '\$\{APP_VERSION:-' + [regex]::Escape($FullVersion) + '\}'
        if ($composeText -notmatch ('(?m)^\s*BUILD_VERSION:\s*"' + $expectedComposeVersion + '"\s*$')) {
            Fail ($composeName + ' BUILD_VERSION fallback is not synchronized with VERSION.')
        }
    }
}

$workflowRoot = Join-RepoPath @('.github', 'workflows')
if (Test-Path -LiteralPath $workflowRoot) {
    foreach ($file in Get-ChildItem -LiteralPath $workflowRoot -Recurse -File -Include *.yml,*.yaml) {
        $text = [System.IO.File]::ReadAllText($file.FullName)
        if ($text -match '(?m)^\s*APP_VERSION\s*=\s*[0-9]+(?:\.[0-9]+){0,3}\s*$') {
            Fail ("Workflow contains a hardcoded active APP_VERSION: " + $file.FullName)
        }
        if ($text -match 'BUILD_VERSION\s*:\s*[0-9]+') {
            Fail ("Workflow contains a hardcoded active BUILD_VERSION: " + $file.FullName)
        }
    }
}

$packageFiles = @(
    (Join-RepoPath @('frontend', 'package.json')),
    (Join-RepoPath @('package.json'))
)
foreach ($packagePath in $packageFiles) {
    if (Test-Path -LiteralPath $packagePath) {
        $package = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
        if ($null -ne $package.version -and $package.version -ne $FullVersion -and $package.version -ne $DisplayVersion) {
            Fail ("Package metadata version mismatch in " + $packagePath + ": " + $package.version)
        }
    }
}

# The current package lock has no root package version. Do not scan dependency
# versions: they are third-party versions, not the application version.

$windowsRoot = Join-RepoPath @('windows')
if (Test-Path -LiteralPath $windowsRoot) {
    $packagingFiles = @(
        (Join-RepoPath @('windows', 'installer.iss')),
        (Join-RepoPath @('windows', 'msix', 'AppxManifest.xml')),
        (Join-RepoPath @('windows', 'AppxManifest.xml')),
        (Join-RepoPath @('windows', 'version_info.txt')),
        (Join-RepoPath @('windows', 'version-info.txt')),
        (Join-RepoPath @('windows', 'version.rc'))
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
    $packagingFiles += @(Get-ChildItem -LiteralPath $windowsRoot -File -Filter '*.rc' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    foreach ($filePath in $packagingFiles | Select-Object -Unique) {
        $file = Get-Item -LiteralPath $filePath
        $text = [System.IO.File]::ReadAllText($file.FullName)
        if ($file.Extension -in @('.iss')) {
            if ($text -notmatch '(?im)^\s*AppVersion\s*=\s*\{#MyAppVersion\}\s*$') {
                Fail ("Installer AppVersion is not derived from MyAppVersion in " + $file.FullName)
            }
            if ($text -match '(?im)^\s*#define\s+MyAppVersion\s+"([^"]+)"' -and $Matches[1] -ne $FullVersion) {
                Fail ("Installer MyAppVersion mismatch in " + $file.FullName)
            }
            if ($text -notmatch '(?im)^\s*VersionInfoVersion\s*=\s*\{#MyAppVersion\}') {
                Fail ("Installer VersionInfoVersion is not derived from MyAppVersion in " + $file.FullName)
            }
            if ($text -notmatch '(?im)^\s*VersionInfoProductVersion\s*=\s*\{#MyAppVersion\}') {
                Fail ("Installer VersionInfoProductVersion is not derived from MyAppVersion in " + $file.FullName)
            }
        }
        if ($file.Name -match 'AppxManifest|msixmanifest') {
            if ($text -match '(?i)(?<![A-Za-z])Version\s*=\s*"([^"]+)"' -and $Matches[1] -ne $FullVersion) {
                Fail ("MSIX manifest version mismatch in " + $file.FullName)
            }
        }
        if ($file.Name -match '^version_info\.txt$|^version-info\.txt$') {
            if ($text -notmatch '(?im)filevers=\(' + [regex]::Escape($Parts[0]) + ',\s*' + [regex]::Escape($Parts[1]) + ',\s*' + [regex]::Escape($Parts[2]) + ',\s*' + [regex]::Escape($Parts[3]) + '\)') {
                Fail ("PyInstaller version metadata mismatch in " + $file.FullName)
            }
            if ($text -notmatch '(?im)StringStruct\("FileVersion",\s*"' + [regex]::Escape($FullVersion) + '"\)') {
                Fail ("PyInstaller FileVersion mismatch in " + $file.FullName)
            }
            if ($text -notmatch '(?im)StringStruct\("ProductVersion",\s*"' + [regex]::Escape($DisplayVersion) + '"\)') {
                Fail ("PyInstaller ProductVersion mismatch in " + $file.FullName)
            }
        }
    }

    $manifestTemplate = Join-RepoPath @('windows', 'msix', 'AppxManifest.xml.template')
    if (Test-Path -LiteralPath $manifestTemplate) {
        $templateText = [System.IO.File]::ReadAllText($manifestTemplate)
        $templateXml = $null
        try {
            $templateXml = [xml]$templateText
        } catch {
            Fail ("MSIX manifest template is not valid XML: " + $_.Exception.Message)
        }
        if ($templateText -notmatch '__VERSION__') {
            Fail 'MSIX manifest template must retain the __VERSION__ build placeholder.'
        }
        if ($templateText -match '(?i)unvirtualizedResources|FileSystemWriteVirtualization') {
            Fail 'MSIX manifest template contains forbidden virtualization capabilities.'
        }
        if ($templateText -notmatch '(?i)<rescap:Capability\s+Name="runFullTrust"') {
            Fail 'MSIX manifest template must retain the required runFullTrust capability.'
        }
    }

    $desktopText = Require-Text (Join-RepoPath @('windows', 'desktop_app.py')) '.' 'Windows desktop launcher'
    if ($desktopText -and $desktopText -notmatch ('(?m)DESKTOP_VERSION\s*=\s*["'']' + [regex]::Escape($FullVersion) + '["'']')) {
        Fail ('Windows desktop launcher version does not match VERSION: expected ' + $FullVersion)
    }
    $cliText = Require-Text (Join-RepoPath @('windows', '8mblocal.py')) '.' 'Windows portable CLI'
    if ($cliText -and $cliText -match '(?m)^VERSION\s*=\s*["''][0-9]+(?:\.[0-9]+){1,3}["'']') {
        Fail 'Windows portable CLI contains a hardcoded numeric version.'
    }
    $msixBuildText = Require-Text (Join-RepoPath @('windows', 'build-msix.ps1')) '.' 'MSIX build script'
    if ($msixBuildText -and $msixBuildText -match '(?m)^\s*\[string\]\$Version\s*=\s*["''][0-9]+') {
        Fail 'MSIX build script contains a hardcoded numeric version default.'
    }
}

if ($Failures.Count -gt 0) {
    $message = 'VERSION_CHECK_FAILED' + [Environment]::NewLine + ' - ' + ($Failures -join ([Environment]::NewLine + ' - '))
    Write-Error $message
    exit 1
}
Write-Output ("VERSION_CHECK_PASSED=" + $FullVersion)
Write-Output ("VERSION_DISPLAY=" + $DisplayVersion)
