# Запуск Эйдоса: сервер речи (если есть) + оболочка Tauri (она сама запускает Python-ядро).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:IDOS_ROOT = $Root
if (-not (Test-Path ".venv\Scripts\python.exe")) { Write-Host "Сначала запустите scripts\Setup.ps1" -ForegroundColor Yellow; exit 1 }
$stt = Join-Path $Root "tools\gigastt\bin\gigastt.exe"
$busy = @(Get-NetTCPConnection -State Listen -LocalPort 9876 -ErrorAction SilentlyContinue).Count -gt 0
if ((Test-Path $stt) -and -not $busy) {
  Start-Process powershell.exe -ArgumentList "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "Start-STT.ps1") -WindowStyle Minimized
}
if ($args -contains "--release" -and (Test-Path "src-tauri\target\release\idos.exe")) { & "src-tauri\target\release\idos.exe"; exit }
npm run tauri dev
