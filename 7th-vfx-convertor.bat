@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "APP_DIR=%~dp0"
cd /d "%APP_DIR%" || exit /b 1
set "VENV_DIR=%APP_DIR%.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "REQUIREMENTS_FILE=%APP_DIR%requirements.txt"
set "APP_PYTHON_ARGS="

where ffmpeg >nul 2>nul
if errorlevel 1 goto install_ffmpeg
where ffprobe >nul 2>nul
if errorlevel 1 goto install_ffmpeg

:prepare_python
set "APP_PYTHON=%VENV_PYTHON%"
if exist "%VENV_PYTHON%" goto dependencies
set "APP_PYTHON=python"
where python >nul 2>nul
if not errorlevel 1 goto create_venv
where py >nul 2>nul
if errorlevel 1 goto missing_python
set "APP_PYTHON=py"
set "APP_PYTHON_ARGS=-3"

:create_venv
where uv >nul 2>nul
if errorlevel 1 goto create_standard_venv
uv venv "%VENV_DIR%" --python ">=3.11"
if errorlevel 1 exit /b 1
goto use_venv

:create_standard_venv
call :run_python -c "import sys; sys.exit(sys.version_info < (3, 11))"
if errorlevel 1 goto missing_python
call :run_python -m venv "%VENV_DIR%"
if errorlevel 1 exit /b 1
set "APP_PYTHON=%VENV_PYTHON%"
set "APP_PYTHON_ARGS="
call :run_python -m pip install -r "%REQUIREMENTS_FILE%"
if errorlevel 1 exit /b 1

:use_venv
set "APP_PYTHON=%VENV_PYTHON%"
set "APP_PYTHON_ARGS="

:dependencies
call :run_python -c "import sys; sys.exit(sys.version_info < (3, 11))"
if errorlevel 1 goto missing_python
where uv >nul 2>nul
if errorlevel 1 goto check_modules
uv pip install --python "%VENV_PYTHON%" --link-mode=copy -r "%REQUIREMENTS_FILE%"
if errorlevel 1 exit /b 1

:check_modules
call :run_python -c "import numpy, PySide6, PyOpenColorIO, OpenEXR"
if errorlevel 1 goto missing_modules
call :run_python -m seventh_convert.build_player --if-available
if errorlevel 1 exit /b 1
call :run_python -m seventh_convert.ui %*
exit /b %ERRORLEVEL%

:install_ffmpeg
set /p "INSTALL_FFMPEG=FFmpeg/ffprobe missing. Install via winget? (Y/N): "
if /i not "%INSTALL_FFMPEG%"=="Y" exit /b 1
call "%APP_DIR%install-ffmpeg.bat"
if errorlevel 1 exit /b 1
echo Open a NEW terminal and run "%APP_DIR%7th-vfx-convertor.bat" again.
pause
exit /b 1

:missing_python
echo Python 3.11+ is required. Install Python, then recreate .venv on this computer. 1>&2
exit /b 1

:missing_modules
echo Install missing dependencies into the local environment: 1>&2
echo "%VENV_PYTHON%" -m pip install -r "%REQUIREMENTS_FILE%" 1>&2
exit /b 1

:run_python
"%APP_PYTHON%" %APP_PYTHON_ARGS% %*
exit /b %ERRORLEVEL%
