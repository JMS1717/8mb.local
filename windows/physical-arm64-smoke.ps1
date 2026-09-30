[CmdletBinding()]
param(
    [string]$ExePath = "$PSScriptRoot\..\dist\8mblocal.exe",
    [string]$ReportPath = "$PSScriptRoot\..\dist\windows-arm64-codec-report.json"
)
$ErrorActionPreference = 'Stop'
$native = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
if ($native -ne 'arm64') { throw "Run this script on a physical Windows ARM64 computer; found '$native'." }
$version = ([IO.File]::ReadAllText((Join-Path $PSScriptRoot '..\VERSION'))).Trim()
& (Join-Path $PSScriptRoot 'test-release.ps1') `
    -Architecture arm64 `
    -ExePath $ExePath `
    -ExpectedVersion $version `
    -SmokeVideoCodec h264_mf `
    -CodecReportPath $ReportPath
if ($LASTEXITCODE -ne 0) { throw "ARM64 release smoke failed with exit code $LASTEXITCODE." }
$report = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
if ($report.actual_encoder -ne 'h264_mf' -or $report.hardware_used -ne $true) {
    throw "Media Foundation hardware encode was not proven. actual_encoder=$($report.actual_encoder), hardware_used=$($report.hardware_used), fallback=$($report.fallback_occurred)"
}
Write-Host "PASS native Windows ARM64 and hardware Media Foundation encode ($($report.actual_encoder))"
Write-Host "Report: $([IO.Path]::GetFullPath($ReportPath))"
