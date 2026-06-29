@echo off
setlocal EnableExtensions EnableDelayedExpansion

echo Installing FFmpeg via winget...
where winget >nul 2>nul
if errorlevel 1 (
  echo winget was not found. Install App Installer from Microsoft Store and try again.
  exit /b 1
)

winget install --id Gyan.FFmpeg --exact --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo FFmpeg install failed.
  exit /b 1
)

echo Looking for installed FFmpeg bin folder...
set "FFMPEG_BIN="
for /f "usebackq delims=" %%F in (`powershell -NoProfile -Command "Get-ChildItem '$env:LOCALAPPDATA\Microsoft\WinGet\Packages' -Directory -Filter 'Gyan.FFmpeg_*' | Get-ChildItem -Directory | Where-Object { $_.Name -like 'ffmpeg-*' } | Get-ChildItem -Directory -Filter bin | Select-Object -First 1 -ExpandProperty FullName"`) do (
  set "FFMPEG_BIN=%%F"
)

if not defined FFMPEG_BIN (
  echo FFmpeg was installed, but its bin folder could not be located automatically.
  echo Open the WinGet package folder and add the ^\bin path to your user PATH manually.
  exit /b 1
)

echo Found: !FFMPEG_BIN!
echo Adding FFmpeg to user PATH if needed...
for /f "usebackq delims=" %%O in (`powershell -NoProfile -Command ^
  "$bin = '%FFMPEG_BIN%';" ^
  "$userPath = [Environment]::GetEnvironmentVariable('Path', 'User');" ^
  "if ([string]::IsNullOrWhiteSpace($userPath)) { $userPath = '' };" ^
  "$parts = $userPath -split ';' | Where-Object { $_ };" ^
  "if ($parts -notcontains $bin) {" ^
  "  $newPath = if ($userPath) { $userPath + ';' + $bin } else { $bin };" ^
  "  [Environment]::SetEnvironmentVariable('Path', $newPath, 'User');" ^
  "  'User PATH updated.'" ^
  "} else {" ^
  "  'User PATH already contains FFmpeg.'" ^
  "}"`) do echo %%O

echo.
echo Verifying local binary...
"!FFMPEG_BIN!\ffmpeg.exe" -version

echo.
echo Done. Open a new terminal and run:
echo ffmpeg -version
exit /b 0
