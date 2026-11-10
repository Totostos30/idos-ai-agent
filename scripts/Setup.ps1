# Установка Эйдоса (IDOS): venv, Python-пакеты, npm, браузер Playwright, ffmpeg.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
[Console]::OutputEncoding = [Text.Encoding]::UTF8
function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }

Step "Python venv"
if (-not (Test-Path ".venv\Scripts\python.exe")) { py -3.11 -m venv .venv }
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py -m pip install --upgrade pip wheel
& $py -m pip install -r requirements.txt

Step "Playwright Chromium (браузер агента)"
try { & $py -m playwright install chromium } catch { Write-Warning "Playwright: $_" }

Step "npm install"
npm install

Step "ffmpeg (запись экрана)"
$ff = Join-Path $Root "tools\ffmpeg\bin\ffmpeg.exe"
if (-not (Test-Path $ff)) {
  try {
    $zip = Join-Path $env:TEMP "ffmpeg-idos.zip"
    Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zip -UseBasicParsing
    $tmp = Join-Path $env:TEMP "ffmpeg-idos"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip $tmp
    $bin = Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    New-Item -ItemType Directory -Force (Split-Path $ff) | Out-Null
    Copy-Item (Join-Path $bin.DirectoryName "*.exe") (Split-Path $ff) -Force
  } catch { Write-Warning "ffmpeg не скачан: $_. Положите ffmpeg.exe в tools\ffmpeg\bin вручную." }
}

Step "Самопроверка"
& $py -m core --selftest
Write-Host "`nГотово. Запуск: Start-IDOS.bat" -ForegroundColor Green
