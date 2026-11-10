@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_dimka.ps1"
if errorlevel 1 (
  echo.
  echo Не удалось запустить Димку. Прочитай сообщение об ошибке выше.
  pause
)
