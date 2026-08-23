[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot 'windows\brand-assets.ps1')

# OnePlus and other launchers mask full-bleed legacy icons into circles. Keep the
# exact desktop artwork inside a 78% safe area so its rounded-square silhouette
# remains visible after the OEM mask is applied.
$targets = @(
    @{ Density = 'mdpi'; Canvas = 48; Artwork = 38 },
    @{ Density = 'hdpi'; Canvas = 72; Artwork = 56 },
    @{ Density = 'xhdpi'; Canvas = 96; Artwork = 75 },
    @{ Density = 'xxhdpi'; Canvas = 144; Artwork = 112 },
    @{ Density = 'xxxhdpi'; Canvas = 192; Artwork = 150 }
)

foreach ($target in $targets) {
    $outputDirectory = Join-Path $PSScriptRoot "app\src\main\res\mipmap-$($target.Density)"
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    $temporaryArtwork = Join-Path ([IO.Path]::GetTempPath()) (
        '8mblocal-android-art-' + [guid]::NewGuid().ToString('N') + '.png'
    )
    try {
        Write-8mbLocalBrandPng -Path $temporaryArtwork -Width $target.Artwork -Height $target.Artwork
        $artwork = [Drawing.Bitmap]::FromFile($temporaryArtwork)
        $canvas = New-Object Drawing.Bitmap(
            $target.Canvas,
            $target.Canvas,
            [Drawing.Imaging.PixelFormat]::Format32bppArgb
        )
        try {
            $offset = [int](($target.Canvas - $target.Artwork) / 2)
            for ($y = 0; $y -lt $target.Artwork; $y++) {
                for ($x = 0; $x -lt $target.Artwork; $x++) {
                    $canvas.SetPixel($x + $offset, $y + $offset, $artwork.GetPixel($x, $y))
                }
            }
            $destination = Join-Path $outputDirectory 'ic_launcher.png'
            $canvas.Save($destination, [Drawing.Imaging.ImageFormat]::Png)
        } finally {
            $canvas.Dispose()
            $artwork.Dispose()
        }
    } finally {
        Remove-Item -LiteralPath $temporaryArtwork -Force -ErrorAction SilentlyContinue
    }
}

Write-Host 'Generated Android launcher icons from the exact desktop brand renderer.'
