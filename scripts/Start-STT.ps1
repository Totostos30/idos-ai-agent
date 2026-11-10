$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$exe=Join-Path $root 'tools\gigastt\bin\gigastt.exe'
if(-not (Test-Path $exe)){ throw "Не найден $exe. Установи GigaSTT по инструкции." }
New-Item -ItemType Directory -Force (Join-Path $root 'models\gigastt') | Out-Null
$env:GIGASTT_PUNCT_MODEL_DIR=Join-Path $root 'models\gigastt\punct'
$env:GIGASTT_VAD_MODEL_DIR=Join-Path $root 'models\gigastt\vad'
Write-Host 'Запускаю локальный GigaSTT (CPU). При первом запуске модель может загружаться несколько минут.'
& $exe serve --host 127.0.0.1 --port 9876 --model-dir (Join-Path $root 'models\gigastt') --pool-size 1
