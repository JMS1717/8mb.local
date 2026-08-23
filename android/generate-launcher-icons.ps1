[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'windows\brand-assets.ps1')

# OnePlus and other launchers mask full-bleed legacy icons into circles. Keep the
# exact desktop artwork inside a 78% safe area so its rounded-square silhouette
# remains visible after the OEM mask is applied.
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

foreach ($target in $targets) {
    $legacy = Join-Path $PSScriptRoot "app\src\main\res\mipmap-$($target.Density)\ic_launcher.png"
    Write-PaddedBrandPng -Destination $legacy -CanvasSize $target.Canvas -ArtworkSize $target.Artwork
    $adaptive = Join-Path $PSScriptRoot "app\src\main\res\drawable-$($target.Density)\ic_launcher_foreground.png"
    Write-PaddedBrandPng -Destination $adaptive -CanvasSize $target.AdaptiveCanvas -ArtworkSize $target.AdaptiveArtwork
}

Write-Host 'Generated Android launcher icons from the exact desktop brand renderer.'
