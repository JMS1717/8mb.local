[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'windows\brand-assets.ps1')

# Legacy launchers need the complete desktop tile. Adaptive launchers provide
# their own outer mask, so their foreground must contain only the desktop glyph;
# embedding the complete rounded tile there creates a visibly padded double mask.
$targets = @(
    @{ Density = 'mdpi'; Canvas = 48; Artwork = 38; AdaptiveCanvas = 108; AdaptiveArtwork = 66 },
    @{ Density = 'hdpi'; Canvas = 72; Artwork = 56; AdaptiveCanvas = 162; AdaptiveArtwork = 99 },
    @{ Density = 'xhdpi'; Canvas = 96; Artwork = 75; AdaptiveCanvas = 216; AdaptiveArtwork = 132 },
    @{ Density = 'xxhdpi'; Canvas = 144; Artwork = 112; AdaptiveCanvas = 324; AdaptiveArtwork = 198 },
    @{ Density = 'xxxhdpi'; Canvas = 192; Artwork = 150; AdaptiveCanvas = 432; AdaptiveArtwork = 264 }
)

function Write-PaddedBrandPng {
    param(
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][int]$CanvasSize,
        [Parameter(Mandatory = $true)][int]$ArtworkSize
    )

    $outputDirectory = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    $temporaryArtwork = Join-Path ([IO.Path]::GetTempPath()) (
        '8mblocal-android-art-' + [guid]::NewGuid().ToString('N') + '.png'
    )
    try {
        Write-8mbLocalBrandPng -Path $temporaryArtwork -Width $ArtworkSize -Height $ArtworkSize
        $artwork = [Drawing.Bitmap]::FromFile($temporaryArtwork)
        $canvas = New-Object Drawing.Bitmap(
            $CanvasSize,
            $CanvasSize,
            [Drawing.Imaging.PixelFormat]::Format32bppArgb
        )
        try {
            $offset = [int](($CanvasSize - $ArtworkSize) / 2)
            for ($y = 0; $y -lt $ArtworkSize; $y++) {
                for ($x = 0; $x -lt $ArtworkSize; $x++) {
                    $canvas.SetPixel($x + $offset, $y + $offset, $artwork.GetPixel($x, $y))
                }
            }
            $canvas.Save($Destination, [Drawing.Imaging.ImageFormat]::Png)
        } finally {
            $canvas.Dispose()
            $artwork.Dispose()
        }
    } finally {
        Remove-Item -LiteralPath $temporaryArtwork -Force -ErrorAction SilentlyContinue
    }
}

function Write-AdaptiveGlyphPng {
    param(
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][int]$CanvasSize,
        [Parameter(Mandatory = $true)][int]$ArtworkSize
    )

    $outputDirectory = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    $bitmap = New-Object Drawing.Bitmap(
        $CanvasSize,
        $CanvasSize,
        [Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.TextRenderingHint = [Drawing.Text.TextRenderingHint]::AntiAliasGridFit
        $graphics.Clear([Drawing.Color]::Transparent)
        $fontSize = [Math]::Max(10, [Math]::Floor($ArtworkSize * 0.48))
        $font = New-Object Drawing.Font('Arial', $fontSize, [Drawing.FontStyle]::Bold, [Drawing.GraphicsUnit]::Pixel)
        $brush = New-Object Drawing.SolidBrush([Drawing.Color]::White)
        $format = New-Object Drawing.StringFormat
        $rect = New-Object Drawing.RectangleF(0, 0, $CanvasSize, $CanvasSize)
        try {
            $format.Alignment = [Drawing.StringAlignment]::Center
            $format.LineAlignment = [Drawing.StringAlignment]::Center
            $graphics.DrawString('8', $font, $brush, $rect, $format)
        } finally {
            $format.Dispose()
            $brush.Dispose()
            $font.Dispose()
        }
        $bitmap.Save($Destination, [Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

foreach ($target in $targets) {
    $legacy = Join-Path $PSScriptRoot "app\src\main\res\mipmap-$($target.Density)\ic_launcher.png"
    Write-PaddedBrandPng -Destination $legacy -CanvasSize $target.Canvas -ArtworkSize $target.Artwork
    $adaptive = Join-Path $PSScriptRoot "app\src\main\res\drawable-$($target.Density)\ic_launcher_foreground.png"
    Write-AdaptiveGlyphPng -Destination $adaptive -CanvasSize $target.AdaptiveCanvas -ArtworkSize $target.AdaptiveArtwork
}

Write-Host 'Generated Android launcher icons from the exact desktop brand renderer.'
