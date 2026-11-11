@echo off
chcp 65001 >nul
cd /d "%~dp0"
title IDOS - дерево коммитов

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Update-CommitTree.ps1"
if errorlevel 1 (
  echo Не удалось обновить историю коммитов.
  pause
  exit /b 1
)

where py >nul 2>&1
if errorlevel 1 (
  echo Не найден Python Launcher ^(py^).
  echo Запустите сайт вручную через Python или установите Python 3.11+.
  pause
  exit /b 1
)

echo Сайт дерева коммитов: http://127.0.0.1:8787
start "IDOS commit tree" "http://127.0.0.1:8787"
py -3 -m http.server 8787 --directory "%~dp0commit-tree"
if errorlevel 1 pause
