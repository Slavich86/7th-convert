@echo off
setlocal

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%" || exit /b 1

set "MISSING=0"
set "APP_PYTHON=python"
set "APP_PYTHON_ARGS="
set "VENV_DIR=%APP_DIR%.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "REQUIREMENTS_FILE=%APP_DIR%requirements.txt"

where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo Missing required command: python or py 1>&2
        set "MISSING=1"
    ) else (
        set "APP_PYTHON=py"
        set "APP_PYTHON_ARGS=-3"
    )
)

call :require_command ffmpeg
call :require_command ffprobe

if "%MISSING%"=="0" (
    where uv >nul 2>nul
    if errorlevel 1 (
        echo uv is not available; falling back to system Python. 1>&2
    ) else (
        if not exist "%VENV_PYTHON%" (
            echo Creating Python virtual environment with uv: %VENV_DIR%
            uv venv "%VENV_DIR%"
            if errorlevel 1 (
                echo uv failed to create .venv; falling back to system Python. 1>&2
            )
        )

        if exist "%VENV_PYTHON%" (
            echo Installing Python dependencies into %VENV_DIR% with uv pip-compatible installer
            uv pip install --python "%VENV_PYTHON%" --link-mode=copy -r "%REQUIREMENTS_FILE%"
            if errorlevel 1 (
                echo uv failed to install dependencies; falling back to system Python. 1>&2
            ) else (
                set "APP_PYTHON=%VENV_PYTHON%"
                set "APP_PYTHON_ARGS="
            )
        )
    )
)

if "%MISSING%"=="0" (
    call :require_python_module PySide6
    call :require_python_module PyOpenColorIO
    call :require_python_module OpenEXR
)

if not "%MISSING%"=="0" (
    echo.
    echo Install required dependencies first:
    echo   Windows: install FFmpeg and add its bin folder to PATH.
    echo   uv: winget install astral-sh.uv
    echo.
    echo Then run:
    echo   7th-vfx-convertor.bat
    echo.
    echo Fallback without uv:
    echo   python -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

call :run_python -m seventh_convert.ui %*
exit /b %ERRORLEVEL%

:require_command
where %1 >nul 2>nul
if errorlevel 1 (
    echo Missing required command: %1 1>&2
    set "MISSING=1"
)
exit /b 0

:require_python_module
call :run_python -c "import importlib, sys; importlib.import_module(sys.argv[1])" %1 >nul 2>nul
if errorlevel 1 (
    echo Missing required Python module: %1 1>&2
    set "MISSING=1"
)
exit /b 0

:run_python
"%APP_PYTHON%" %APP_PYTHON_ARGS% %*
exit /b %ERRORLEVEL%
