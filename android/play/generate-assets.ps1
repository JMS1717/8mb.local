[CmdletBinding()]
param(
    [string]$SourceScreenshotDirectory = (Join-Path $PSScriptRoot 'source-screenshots')
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot 'windows\brand-assets.ps1')

$graphicsDirectory = Join-Path $PSScriptRoot 'listing\en-US\graphics'
$phoneDirectory = Join-Path $graphicsDirectory 'phone-screenshots'
New-Item -ItemType Directory -Force -Path $graphicsDirectory, $phoneDirectory | Out-Null

Write-8mbLocalBrandPng -Path (Join-Path $graphicsDirectory 'app-icon-512.png') -Width 512 -Height 512

$featurePath = Join-Path $graphicsDirectory 'feature-graphic-1024x500.png'
$feature = New-Object Drawing.Bitmap(1024, 500, [Drawing.Imaging.PixelFormat]::Format24bppRgb)
$drawing = [Drawing.Graphics]::FromImage($feature)
try {
    $drawing.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $drawing.TextRenderingHint = [Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $rect = New-Object Drawing.RectangleF(0, 0, 1024, 500)
    $gradient = New-Object Drawing.Drawing2D.LinearGradientBrush(
        $rect,
        [Drawing.Color]::FromArgb(25, 47, 116),
        [Drawing.Color]::FromArgb(0, 180, 255),
        [Drawing.Drawing2D.LinearGradientMode]::Horizontal
    )
    try { $drawing.FillRectangle($gradient, $rect) } finally { $gradient.Dispose() }
    $accent = New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(42, 255, 255, 255))
    try {
        $drawing.FillEllipse($accent, -110, 235, 420, 420)
        $drawing.FillEllipse($accent, 795, -155, 390, 390)
    } finally { $accent.Dispose() }
    $titleFont = New-Object Drawing.Font('Arial', 76, [Drawing.FontStyle]::Bold, [Drawing.GraphicsUnit]::Pixel)
    $subtitleFont = New-Object Drawing.Font('Arial', 34, [Drawing.FontStyle]::Regular, [Drawing.GraphicsUnit]::Pixel)
    $white = New-Object Drawing.SolidBrush([Drawing.Color]::White)
    $format = New-Object Drawing.StringFormat
    try {
        $format.Alignment = [Drawing.StringAlignment]::Center
        $format.LineAlignment = [Drawing.StringAlignment]::Center
        $drawing.DrawString('8mb.local', $titleFont, $white, (New-Object Drawing.RectangleF(180, 125, 664, 105)), $format)
        $drawing.DrawString('Compress locally. Share anywhere.', $subtitleFont, $white, (New-Object Drawing.RectangleF(150, 245, 724, 80)), $format)
    } finally {
        $format.Dispose()
        $white.Dispose()
        $subtitleFont.Dispose()
        $titleFont.Dispose()
    }
    $feature.Save($featurePath, [Drawing.Imaging.ImageFormat]::Png)
} finally {
    $drawing.Dispose()
    $feature.Dispose()
}

function Write-PlayScreenshot {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $input = [Drawing.Bitmap]::FromFile($Source)
    try {
        $cropWidth = $input.Width
        $cropHeight = [Math]::Min($input.Height, $cropWidth * 2)
        $cropTop = [Math]::Max(0, [int](($input.Height - $cropHeight) / 2))
        $output = New-Object Drawing.Bitmap(1080, 2160, [Drawing.Imaging.PixelFormat]::Format24bppRgb)
        $graphics = [Drawing.Graphics]::FromImage($output)
        try {
            $graphics.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.DrawImage(
                $input,
                (New-Object Drawing.Rectangle(0, 0, 1080, 2160)),
                (New-Object Drawing.Rectangle(0, $cropTop, $cropWidth, $cropHeight)),
                [Drawing.GraphicsUnit]::Pixel
            )
            $output.Save($Destination, [Drawing.Imaging.ImageFormat]::Png)
        } finally {
            $graphics.Dispose()
            $output.Dispose()
        }
    } finally { $input.Dispose() }
}

if (Test-Path -LiteralPath $SourceScreenshotDirectory -PathType Container) {
    Get-ChildItem -LiteralPath $SourceScreenshotDirectory -Filter '*.png' -File |
        Sort-Object Name |
        ForEach-Object {
            Write-PlayScreenshot -Source $_.FullName -Destination (Join-Path $phoneDirectory $_.Name)
        }
}

$checksumPath = Join-Path $PSScriptRoot 'listing\en-US\SHA256SUMS-play.txt'
$checksumLines = Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'listing\en-US') -Recurse -File |
    Where-Object { $_.FullName -ne $checksumPath } |
    Sort-Object FullName |
    ForEach-Object {
        $relative = [IO.Path]::GetRelativePath((Split-Path -Parent $checksumPath), $_.FullName).Replace('\', '/')
        "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant())  $relative"
    }
[IO.File]::WriteAllLines($checksumPath, $checksumLines, [Text.UTF8Encoding]::new($false))

Write-Host "Generated Play assets in $graphicsDirectory"
