# Сборка переносимого релиза IDOS: тесты → Vite → exe Tauri → папка release\IDOS-<версия> (+ zip).
# Установщик NSIS намеренно не используется: приложению нужно Python-ядро (core/) и .venv рядом с exe,
# поэтому релиз — папка с кодом, которую пользователь один раз готовит через scripts\Setup.ps1.
[CmdletBinding()]
param(
  [switch]$SkipTests,
  [switch]$NoZip
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
[Console]::OutputEncoding = [Text.Encoding]::UTF8
function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }

$version = (Get-Content package.json -Raw | ConvertFrom-Json).version
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Нет .venv. Сначала выполните: powershell -ExecutionPolicy Bypass -File scripts\Setup.ps1" }

if (-not $SkipTests) {
  Step "Тесты Python"
  & $py -m unittest discover -s tests
  if ($LASTEXITCODE -ne 0) { throw "Тесты не прошли — релиз не собираем." }
}

Step "Сборка интерфейса (Vite)"
& npm run build
if ($LASTEXITCODE -ne 0) { throw "npm run build завершился с ошибкой." }

Step "Сборка оболочки (Tauri, без бандла)"
& npm run tauri build
if ($LASTEXITCODE -ne 0) { throw "Tauri build завершился с ошибкой: посмотрите первую строку error[...]." }
$exe = Join-Path $Root "src-tauri\target\release\idos.exe"
if (-not (Test-Path $exe)) { throw "Не найден $exe — проверьте настройки Tauri." }

Step "Комплект release\IDOS-$version"
$out = Join-Path $Root "release\IDOS-$version"
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Force $out | Out-Null

$dirs = @("core", "skills", "scripts", "src", "src-tauri")
foreach ($dir in $dirs) {
  if (-not (Test-Path $dir)) { continue }
  $exclude = @("__pycache__", "target", "node_modules")
  if ($dir -eq "src-tauri") { $exclude += "icons" }
  $copyArgs = @("`"$dir`"", "`"$(Join-Path $out $dir)`"", "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
  foreach ($name in $exclude) { $copyArgs += "/XD"; $copyArgs += "`"$name`"" }
  & robocopy @copyArgs | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy $dir завершился с кодом $LASTEXITCODE" }
}

# exe кладём по пути, который ожидает Start-IDOS.bat --release
$exeOut = Join-Path $out "src-tauri\target\release"
New-Item -ItemType Directory -Force $exeOut | Out-Null
Copy-Item $exe (Join-Path $exeOut "idos.exe") -Force

$files = @("package.json", "package-lock.json", "requirements.txt", "index.html", "vite.config.js",
           "README.md", "INSTALL.md", "ARCHITECTURE.md", "SECURITY.md", "CONTRIBUTING.md", "LICENSE",
           "Start-IDOS.bat", "Selftest-IDOS.bat", ".gitignore")
foreach ($file in $files) {
  if (Test-Path $file) { Copy-Item $file $out -Force }
}

# data/ и .env в релиз не попадают; пустая папка data создастся при первом запуске.
Set-Content -Path (Join-Path $out "VERSION") -Value $version -Encoding ASCII

if (-not $NoZip) {
  Step "Архив release\IDOS-$version.zip"
  $zip = Join-Path $Root "release\IDOS-$version.zip"
  if (Test-Path $zip) { Remove-Item $zip -Force }
  Compress-Archive -Path (Join-Path $out "*") -DestinationPath $zip -CompressionLevel Optimal
  Write-Host "Архив: $zip" -ForegroundColor Green
}

Write-Host "`nГотово: $out" -ForegroundColor Green
Write-Host "Пользователю: распаковать, выполнить scripts\Setup.ps1, затем Start-IDOS.bat --release" -ForegroundColor Green
