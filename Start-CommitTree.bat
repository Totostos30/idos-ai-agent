@echo off
chcp 65001 >nul
cd /d "%~dp0"
title IDOS - дерево коммитов

where py >nul 2>&1
if errorlevel 1 (
  echo Не найден Python Launcher ^(py^).
  echo Установите Python 3.11+ и повторите запуск.
  pause
  exit /b 1
)

echo Сайт дерева коммитов: http://127.0.0.1:8787
echo Коммиты подтягиваются из git в реальном времени.
start "IDOS commit tree" "http://127.0.0.1:8787"
py -3 "%~dp0commit-tree\serve.py" 8787
if errorlevel 1 pause