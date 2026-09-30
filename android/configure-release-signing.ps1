[CmdletBinding()]
param(
    [string]$Repository = 'JMS1717/8mb.local',
    [string]$KeystorePath = (Join-Path $env:LOCALAPPDATA '8mb.local-signing\8mblocal-app-signing.jks'),
    [string]$Alias = '8mblocal-app-signing',
    [switch]$Automatic,
    [string]$KeytoolPath,
    [switch]$SkipGitHubSecrets
)

$ErrorActionPreference = 'Stop'
if ($KeytoolPath) {
    $keytool = (Resolve-Path -LiteralPath $KeytoolPath).Path
} else {
    $keytool = (Get-Command keytool -ErrorAction Stop).Source
}
if (-not $SkipGitHubSecrets) { $gh = (Get-Command gh -ErrorAction Stop).Source }
$resolvedKeystore = [IO.Path]::GetFullPath($KeystorePath)
$keystoreDirectory = Split-Path -Parent $resolvedKeystore
New-Item -ItemType Directory -Force -Path $keystoreDirectory | Out-Null
$credentialsPath = Join-Path $keystoreDirectory 'passwords.dpapi.xml'
# Keep private material out of source control and cloud-synced Documents.
if ($Automatic) {
    if ($env:OS -ne 'Windows_NT') { throw 'Automatic password recovery requires Windows DPAPI.' }
    $userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    # Modify only the DACL; Set-Acl may try to write the audit descriptor and
    # require SeSecurityPrivilege unnecessarily on repeat runs.
    & icacls $keystoreDirectory /inheritance:r /grant:r `
        "*$($userSid.Value):(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict signing-directory access.' }
    $acl = Get-Acl -LiteralPath $keystoreDirectory
    foreach ($rule in @($acl.Access)) {
        $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($sid -notin @($userSid.Value, 'S-1-5-18')) {
            & icacls $keystoreDirectory /remove "*$sid" | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Could not remove broad signing-directory access.' }
        }
    }
    $acl = Get-Acl -LiteralPath $keystoreDirectory
    if (-not $acl.AreAccessRulesProtected -or $acl.Access.Count -ne 2) {
        throw 'Signing-directory access was not restricted as expected.'
    }
}

function Read-PlaintextSecret([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    return [Net.NetworkCredential]::new('', $secure).Password
}

function Set-GitHubSecret([string]$Name, [string]$Value) {
    $Value | & $gh secret set $Name --repo $Repository
    if ($LASTEXITCODE -ne 0) { throw "Could not configure GitHub secret $Name." }
}

function New-RandomPassword {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes)
}

if ($Automatic) {
    if (Test-Path -LiteralPath $credentialsPath) {
        $saved = Import-Clixml -LiteralPath $credentialsPath
        if ($saved.Alias -ne $Alias -or $saved.KeystorePath -ne $resolvedKeystore) {
            throw 'Existing recovery information belongs to another key. Do not rotate a signing identity.'
        }
        $storePassword = [Net.NetworkCredential]::new('', $saved.StorePassword).Password
        $keyPassword = [Net.NetworkCredential]::new('', $saved.KeyPassword).Password
    } else {
        if (Test-Path -LiteralPath $resolvedKeystore) {
            throw 'An existing keystore has no DPAPI password recovery file. Use interactive mode; never overwrite it.'
        }
        $storePassword = New-RandomPassword
        $keyPassword = New-RandomPassword
        # Save encrypted recovery before generating the key; retries reuse the same identity/passwords.
        [pscustomobject]@{
            Alias = $Alias
            KeystorePath = $resolvedKeystore
            StorePassword = (ConvertTo-SecureString $storePassword -AsPlainText -Force)
            KeyPassword = (ConvertTo-SecureString $keyPassword -AsPlainText -Force)
        } | Export-Clixml -LiteralPath $credentialsPath
    }
} else {
    $storePassword = Read-PlaintextSecret 'Permanent app-keystore password (save it in your password manager)'
    $keyPassword = Read-PlaintextSecret 'Permanent app-key password (save it in your password manager)'
}
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
        if ($LASTEXITCODE -ne 0) { throw 'keytool could not create the app-signing keystore.' }
    }

    & $keytool -list `
        -keystore $resolvedKeystore `
        -storepass:env EIGHTMB_UPLOAD_STORE_PASSWORD `
        -alias $Alias | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'The app-signing keystore password or alias is invalid.' }

    $certificatePath = Join-Path $keystoreDirectory 'app-signing-certificate.der'
    & $keytool -exportcert -keystore $resolvedKeystore -storepass:env EIGHTMB_UPLOAD_STORE_PASSWORD `
        -alias $Alias -file $certificatePath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not export the public signing certificate.' }
    $fingerprint = (Get-FileHash -LiteralPath $certificatePath -Algorithm SHA256).Hash.ToLowerInvariant()

    # This is a local recovery copy, not a substitute for an independent backup.
    $backupPath = Join-Path $keystoreDirectory 'local-recovery.zip'
    if (-not (Test-Path -LiteralPath $backupPath)) {
        $backupFiles = @($resolvedKeystore, $certificatePath)
        if ($Automatic) { $backupFiles += $credentialsPath }
        Compress-Archive -LiteralPath $backupFiles -DestinationPath $backupPath
    }

    if (-not $SkipGitHubSecrets) {
        $keystoreBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($resolvedKeystore))
        Set-GitHubSecret ANDROID_UPLOAD_KEYSTORE_BASE64 $keystoreBase64
        Set-GitHubSecret ANDROID_UPLOAD_STORE_PASSWORD $storePassword
        Set-GitHubSecret ANDROID_UPLOAD_KEY_ALIAS $Alias
        Set-GitHubSecret ANDROID_UPLOAD_KEY_PASSWORD $keyPassword
    }

    Write-Host "Permanent app-signing keystore: $resolvedKeystore"
    Write-Host "Public certificate SHA-256: $fingerprint"
    if ($SkipGitHubSecrets) {
        Write-Host 'Offline setup only: no GitHub secrets changed and no release was published.'
    } else {
        Write-Host 'GitHub release-signing secrets configured. No release was published.'
    }
    if ($Automatic) {
        Write-Host 'Local recovery uses Windows DPAPI, tied to this Windows account/computer.'
        Write-Host 'Before publishing, export the passwords into a password manager and back up the keystore off this computer.'
    } else {
        Write-Host 'Back up the keystore and both passwords independently before publishing.'
    }
} finally {
    Remove-Item Env:EIGHTMB_UPLOAD_STORE_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:EIGHTMB_UPLOAD_KEY_PASSWORD -ErrorAction SilentlyContinue
    $storePassword = $null
    $keyPassword = $null
    $keystoreBase64 = $null
    $saved = $null
}
