# WinOCR.ps1 - Batch OCR images with the built-in Windows OCR engine.
# Output format matches ocr_run.py: <Out>/<sample_id>.md  (one recognized line per row, UTF-8 no BOM)
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File WinOCR.ps1 -Images <dir> -Out <dir> [-Limit N] [-Lang zh-Hans-CN]
param(
  [Parameter(Mandatory=$true)][string]$Images,
  [Parameter(Mandatory=$true)][string]$Out,
  [int]$Limit = 0,
  [string]$Lang = "zh-Hans-CN"
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime

[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.FileAccessMode,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null

$script:asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
  Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
                 $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
  $t = $script:asTask.MakeGenericMethod($type).Invoke($null, @($op))
  $t.Wait(-1) | Out-Null
  return $t.Result
}

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) {
  $lang = [Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]::new($Lang)
  $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
}
if (-not $engine) { throw "Cannot create OCR engine (language $Lang)" }
Write-Output ("Engine ready: " + $engine.RecognizerLanguage.LanguageTag + "  max dim " + $engine.MaxImageDimension)

New-Item -ItemType Directory -Force -Path $Out | Out-Null
$imgs = Get-ChildItem $Images -File | Where-Object { $_.Extension -match '^\.(png|jpg|jpeg|bmp)$' } | Sort-Object Name
if ($Limit -gt 0) { $imgs = $imgs | Select-Object -First $Limit }
Write-Output ("Images to process: " + $imgs.Count)

$n = 0; $fail = 0
$utf8 = New-Object System.Text.UTF8Encoding($false)
foreach ($img in $imgs) {
  $sid = [IO.Path]::GetFileNameWithoutExtension($img.Name)
  try {
    $sf  = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($img.FullName)) ([Windows.Storage.StorageFile])
    $stm = Await ($sf.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stm)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
    $stm.Dispose(); $bmp.Dispose()
    $lines = @($res.Lines | ForEach-Object { $_.Text })
    [IO.File]::WriteAllText((Join-Path $Out ($sid + ".md")), (($lines -join "`n") + "`n"), $utf8)
    $n++
    if ($n % 10 -eq 0) { Write-Output ("  done " + $n + " / " + $imgs.Count) }
  } catch {
    $fail++
    Write-Output ("  FAILED " + $img.Name + " : " + $_.Exception.Message)
  }
}
Write-Output ("DONE ok=" + $n + " fail=" + $fail + "  ->  " + $Out)
