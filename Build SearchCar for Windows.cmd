@echo off
setlocal

where pwsh.exe >nul 2>nul
if errorlevel 1 (
  echo PowerShell 7 is required. Install it, then run this file again.
  echo https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows
  echo.
  pause
  exit /b 1
)

pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Build SearchCar for Windows.ps1"
exit /b %errorlevel%
