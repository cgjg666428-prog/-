$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
function Send-Event($Data) {
    [Console]::Out.WriteLine(($Data | ConvertTo-Json -Compress -Depth 5))
    [Console]::Out.Flush()
}
try {
    $config = $env:SAFETY_STUDY_REGION | ConvertFrom-Json
    $x = [int]$config.x; $y = [int]$config.y
    $width = [int]$config.width; $height = [int]$config.height
    $interval = [Math]::Max(250, [Math]::Min(2000, [int]$config.interval_ms))
    if ($width -lt 120 -or $height -lt 80 -or $width -gt 10000 -or $height -gt 10000) {
        throw 'Invalid practice region. Select the question and all its options.'
    }
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    Add-Type -AssemblyName System.Drawing
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class PracticeDpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }'
    $null = [PracticeDpi]::SetProcessDPIAware()
    $null = [Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
    $null = [Windows.Storage.Streams.DataWriter,Windows.Storage.Streams,ContentType=WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
    $null = [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
    $script:AwaitMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    function Await-WinRT($Operation, [Type]$ResultType) {
        $task = $script:AwaitMethod.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
        $task.Wait()
        return $task.Result
    }
    $language = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages |
        Where-Object { $_.LanguageTag -like 'zh*' } | Select-Object -First 1
    if (-not $language) { throw 'Windows Chinese OCR is unavailable. Install the Simplified Chinese basic typing/OCR language component.' }
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
    $hash = [System.Security.Cryptography.SHA256]::Create()
    $previous = ''; $recognized = ''; $heartbeat = [DateTime]::UtcNow
    Send-Event @{ event='ready'; language=$language.LanguageTag }
    while ($true) {
        $started = [Diagnostics.Stopwatch]::StartNew()
        $picture = New-Object System.Drawing.Bitmap($width, $height)
        $graphics = [System.Drawing.Graphics]::FromImage($picture)
        $memory = New-Object System.IO.MemoryStream
        try {
            $graphics.CopyFromScreen($x, $y, 0, 0, $picture.Size, [System.Drawing.CopyPixelOperation]::SourceCopy)
            $picture.Save($memory, [System.Drawing.Imaging.ImageFormat]::Png)
            $bytes = $memory.ToArray()
        } finally {
            $graphics.Dispose(); $picture.Dispose(); $memory.Dispose()
        }
        $fingerprint = [Convert]::ToBase64String($hash.ComputeHash($bytes))
        if ($fingerprint -ne $previous) {
            $previous = $fingerprint
            Send-Event @{ event='changing' }
        } elseif ($fingerprint -ne $recognized) {
            $random = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
            try {
                $writer = [Windows.Storage.Streams.DataWriter]::new($random)
                try {
                    $writer.WriteBytes($bytes)
                    $null = Await-WinRT ($writer.StoreAsync()) ([uint32])
                    $null = $writer.DetachStream()
                } finally { $writer.Dispose() }
                $random.Seek(0)
                $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($random)) ([Windows.Graphics.Imaging.BitmapDecoder])
                $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
                $scale = [Math]::Min(1.0, [Windows.Media.Ocr.OcrEngine]::MaxImageDimension / [Math]::Max($decoder.PixelWidth, $decoder.PixelHeight))
                $transform.ScaledWidth = [uint32][Math]::Max(1, [Math]::Floor($decoder.PixelWidth * $scale))
                $transform.ScaledHeight = [uint32][Math]::Max(1, [Math]::Floor($decoder.PixelHeight * $scale))
                $bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync(
                    [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
                    [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,
                    $transform,
                    [Windows.Graphics.Imaging.ExifOrientationMode]::RespectExifOrientation,
                    [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
                )) ([Windows.Graphics.Imaging.SoftwareBitmap])
                try {
                    $result = Await-WinRT ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
                    $ordered = $result.Lines | Sort-Object @{Expression={ ($_.Words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum }}, @{Expression={ ($_.Words | ForEach-Object { $_.BoundingRect.X } | Measure-Object -Minimum).Minimum }}
                    $text = ($ordered | ForEach-Object { $_.Text }) -join "`n"
                    Send-Event @{ event='text'; text=$text; elapsed_ms=$started.ElapsedMilliseconds }
                    $recognized = $fingerprint
                } finally { $bitmap.Dispose() }
            } finally { $random.Dispose() }
        } elseif (([DateTime]::UtcNow - $heartbeat).TotalSeconds -ge 3) {
            Send-Event @{ event='unchanged' }
            $heartbeat = [DateTime]::UtcNow
        }
        Start-Sleep -Milliseconds $interval
    }
} catch {
    Send-Event @{ event='error'; message=('Live reading stopped. Run from the normal unlocked Windows desktop. Remote/disconnected or isolated desktops may not allow screen reading. Details: ' + $_.Exception.ToString()) }
    exit 1
}
