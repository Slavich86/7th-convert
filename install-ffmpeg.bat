@echo off
setlocal EnableExtensions DisableDelayedExpansion
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-ffmpeg.ps1"
exit /b %ERRORLEVEL%
