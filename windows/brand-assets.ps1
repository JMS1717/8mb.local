Add-Type -AssemblyName System.Drawing

function Write-8mbLocalBrandPng {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int]$Width,
        [Parameter(Mandatory = $true)][int]$Height
    )

    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }

    $bitmap = New-Object Drawing.Bitmap($Width, $Height, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    try {
        $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $graphics.TextRenderingHint = [Drawing.Text.TextRenderingHint]::AntiAliasGridFit
        $graphics.Clear([Drawing.Color]::Transparent)

        $rect = New-Object Drawing.RectangleF(0, 0, $Width, $Height)
        $radius = [Math]::Max(2.0, [Math]::Min($Width, $Height) * 0.16)
        $diameter = $radius * 2
        $shape = New-Object Drawing.Drawing2D.GraphicsPath
        $gradient = New-Object Drawing.Drawing2D.LinearGradientBrush(
            $rect,
            [Drawing.Color]::FromArgb(0, 113, 197),
            [Drawing.Color]::FromArgb(0, 180, 255),
            [Drawing.Drawing2D.LinearGradientMode]::Horizontal
        )
        try {
            $shape.AddArc(0, 0, $diameter, $diameter, 180, 90)
            $shape.AddArc($Width - $diameter, 0, $diameter, $diameter, 270, 90)
            $shape.AddArc($Width - $diameter, $Height - $diameter, $diameter, $diameter, 0, 90)
            $shape.AddArc(0, $Height - $diameter, $diameter, $diameter, 90, 90)
            $shape.CloseFigure()
            $graphics.FillPath($gradient, $shape)
        } finally {
            $gradient.Dispose()
            $shape.Dispose()
        }

        $fontSize = [Math]::Max(10, [Math]::Floor([Math]::Min($Width, $Height) * 0.48))
        $font = New-Object Drawing.Font('Arial', $fontSize, [Drawing.FontStyle]::Bold, [Drawing.GraphicsUnit]::Pixel)
        $brush = New-Object Drawing.SolidBrush([Drawing.Color]::White)
        $format = New-Object Drawing.StringFormat
        try {
            $format.Alignment = [Drawing.StringAlignment]::Center
            $format.LineAlignment = [Drawing.StringAlignment]::Center
            $graphics.DrawString('8', $font, $brush, $rect, $format)
        } finally {
            $format.Dispose()
            $brush.Dispose()
            $font.Dispose()
        }
        $bitmap.Save($Path, [Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

function Write-8mbLocalBrandIco {
    param([Parameter(Mandatory = $true)][string]$Path)

    $temporaryPng = Join-Path ([IO.Path]::GetTempPath()) ('8mblocal-icon-' + [guid]::NewGuid().ToString('N') + '.png')
    try {
        Write-8mbLocalBrandPng -Path $temporaryPng -Width 256 -Height 256
        $png = [IO.File]::ReadAllBytes($temporaryPng)
        $parent = Split-Path -Parent $Path
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $writer = New-Object IO.BinaryWriter($stream)
        try {
            # ICO header and one 256x256 PNG-compressed, 32-bit image entry.
            $writer.Write([uint16]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]1)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]32)
            $writer.Write([uint32]$png.Length)
            $writer.Write([uint32]22)
            $writer.Write($png)
        } finally {
            $writer.Dispose()
            $stream.Dispose()
        }
    } finally {
        Remove-Item -LiteralPath $temporaryPng -Force -ErrorAction SilentlyContinue
    }
}
