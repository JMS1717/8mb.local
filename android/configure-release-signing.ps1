[CmdletBinding()]
param(
    [string]$Repository = 'JMS1717/8mb.local',
    [string]$KeystorePath = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) '8mb.local-signing\8mblocal-upload.jks'),
    [string]$Alias = '8mblocal-upload'
)

$ErrorActionPreference = 'Stop'
$keytool = (Get-Command keytool -ErrorAction Stop).Source
$gh = (Get-Command gh -ErrorAction Stop).Source
$resolvedKeystore = [IO.Path]::GetFullPath($KeystorePath)
$keystoreDirectory = Split-Path -Parent $resolvedKeystore
New-Item -ItemType Directory -Force -Path $keystoreDirectory | Out-Null

function Read-PlaintextSecret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    return [Net.NetworkCredential]::new('', $secure).Password
}

function Set-GitHubSecret([string]$Name, [string]$Value) {
    $Value | & $gh secret set $Name --repo $Repository
    if ($LASTEXITCODE -ne 0) { throw "Could not configure GitHub secret $Name." }
}

$storePassword = Read-PlaintextSecret 'Permanent upload-keystore password (save it in your password manager)'
$keyPassword = Read-PlaintextSecret 'Permanent upload-key password (save it in your password manager)'
if ($storePassword.Length -lt 12 -or $keyPassword.Length -lt 12) {
    throw 'Use passwords of at least 12 characters.'
}

try {
    $env:EIGHTMB_UPLOAD_STORE_PASSWORD = $storePassword
    $env:EIGHTMB_UPLOAD_KEY_PASSWORD = $keyPassword
    if (-not (Test-Path -LiteralPath $resolvedKeystore -PathType Leaf)) {
        & $keytool -genkeypair -v `
            -keystore $resolvedKeystore `
            -storetype JKS `
            -storepass:env EIGHTMB_UPLOAD_STORE_PASSWORD `
            -keypass:env EIGHTMB_UPLOAD_KEY_PASSWORD `
            -alias $Alias `
            -keyalg RSA `
            -keysize 4096 `
            -validity 10000 `
            -dname 'CN=8mb.local, O=JMS1717, C=US'
        if ($LASTEXITCODE -ne 0) { throw 'keytool could not create the upload keystore.' }
    }

    & $keytool -list `
        -keystore $resolvedKeystore `
        -storepass:env EIGHTMB_UPLOAD_STORE_PASSWORD `
        -alias $Alias | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'The upload keystore password or alias is invalid.' }

    $keystoreBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($resolvedKeystore))
    Set-GitHubSecret ANDROID_UPLOAD_KEYSTORE_BASE64 $keystoreBase64
    Set-GitHubSecret ANDROID_UPLOAD_STORE_PASSWORD $storePassword
    Set-GitHubSecret ANDROID_UPLOAD_KEY_ALIAS $Alias
    Set-GitHubSecret ANDROID_UPLOAD_KEY_PASSWORD $keyPassword

    Write-Host "Permanent upload keystore: $resolvedKeystore"
    Write-Host 'GitHub release-signing secrets configured. Back up the keystore and both passwords before publishing.'
} finally {
    Remove-Item Env:EIGHTMB_UPLOAD_STORE_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:EIGHTMB_UPLOAD_KEY_PASSWORD -ErrorAction SilentlyContinue
    $storePassword = $null
    $keyPassword = $null
}
