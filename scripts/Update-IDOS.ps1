# Обновление Эйдоса из скачанного архива idos-v*.zip без ручной распаковки.
# Запуск: powershell -ExecutionPolicy Bypass -File C:\IDOS-AI-AGENT\scripts\Update-IDOS.ps1 [-Zip путь] [-NoStart]
param([string]$Zip = "", [string]$Root = "C:\IDOS-AI-AGENT", [switch]$NoStart)
$ErrorActionPreference = "Stop"
if (-not (Test-Path (Join-Path $Root "core"))) { Write-Host "Не нашёл Эйдос в $Root. Укажите: -Root путь" -ForegroundColor Red; exit 1 }
[Console]::OutputEncoding = [Text.Encoding]::UTF8

# 1. Самый свежий архив из «Загрузок», если путь не указан
if (-not $Zip) {
  $dl = Join-Path $env:USERPROFILE "Downloads"
  $found = Get-ChildItem $dl -Filter "idos-v*.zip" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $found) { Write-Host "Не нашёл idos-v*.zip в $dl. Укажите: -Zip путь\к\архиву.zip" -ForegroundColor Red; exit 1 }
  $Zip = $found.FullName
}
Write-Host "Архив: $Zip" -ForegroundColor Cyan

# 2. Остановить Эйдос (оболочку, ядро, tauri dev); сервер речи GigaSTT не трогаем
Get-Process idos -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match '-m\s+core') -or
  ($_.Name -eq 'node.exe' -and $_.CommandLine -match 'tauri')
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

# 3. Распаковать во временную папку
# Временная папка внутри проекта: %TEMP% с русским именем пользователя даёт короткий путь 3EC2~1, на котором падает Remove-Item
function Remove-Quiet($path) { if (Test-Path -LiteralPath $path) { try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop } catch { cmd /c rmdir /s /q "`"$path`"" 2>$null } } }
$tmp = Join-Path $Root "backups\_update"
Remove-Quiet $tmp
Expand-Archive -Path $Zip -DestinationPath $tmp -Force
$src = Join-Path $tmp "idos"
if (-not (Test-Path (Join-Path $src "core"))) { $src = $tmp }

# 4. Резервная копия кода (без данных и тяжёлых папок)
$bak = Join-Path $Root ("backups\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Force -Path $bak | Out-Null
foreach ($p in "core", "src", "scripts", "src-tauri\src", "requirements.txt", "package.json", "src-tauri\tauri.conf.json", "src-tauri\Cargo.toml") {
  $from = Join-Path $Root $p
  if (Test-Path $from) { $to = Join-Path $bak $p; New-Item -ItemType Directory -Force -Path (Split-Path $to) | Out-Null; Copy-Item $from $to -Recurse -Force }
}
Write-Host "Резервная копия: $bak" -ForegroundColor DarkGray

# 5. Скопировать новые файлы поверх; данные, ключи, модели и окружение не трогаются
robocopy $src $Root /E /NFL /NDL /NJH /NJS /NP /XD data logs .venv node_modules target backups models __pycache__ _update | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Host "Ошибка копирования (robocopy $LASTEXITCODE)" -ForegroundColor Red; exit 1 }
Get-ChildItem $Root -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch '\\(\.venv|node_modules|target|data|models)\\' } | Unblock-File -ErrorAction SilentlyContinue
Remove-Quiet $tmp

# 6. Новые зависимости
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $py) {
  Write-Host "Обновляю пакеты Python…" -ForegroundColor Cyan
  & $py -m pip install -q --disable-pip-version-check -r (Join-Path $Root "requirements.txt")
}
Write-Host "Готово." -ForegroundColor Green

# 7. Запуск
if (-not $NoStart) { Start-Process (Join-Path $Root "Start-IDOS.bat") -WorkingDirectory $Root }
