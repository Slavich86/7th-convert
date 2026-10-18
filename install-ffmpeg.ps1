$ErrorActionPreference = 'Stop'
try {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'winget was not found. Install App Installer from Microsoft Store.'
    }
    & winget install --id Gyan.FFmpeg --exact --silent --accept-package-agreements --accept-source-agreements
    # winget may report a nonzero result when the package is already current;
    # only accept that case if both installed executables can be verified below.
    $installResult = $LASTEXITCODE
    $candidates = @((Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'))
    $packages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $packages) {
        $candidates += @(Get-ChildItem -LiteralPath $packages -Directory -Filter 'Gyan.FFmpeg_*' |
            Get-ChildItem -Recurse -Directory -Filter bin | Select-Object -ExpandProperty FullName)
    }
    $bin = $candidates | Where-Object {
        (Test-Path -LiteralPath (Join-Path $_ 'ffmpeg.exe')) -and
        (Test-Path -LiteralPath (Join-Path $_ 'ffprobe.exe'))
    } | Select-Object -First 1
    if (-not $bin) { throw "FFmpeg/ffprobe not found after winget (exit $installResult)." }
    & (Join-Path $bin 'ffmpeg.exe') -version
    if ($LASTEXITCODE -ne 0) { throw 'FFmpeg verification failed.' }
    & (Join-Path $bin 'ffprobe.exe') -version
    if ($LASTEXITCODE -ne 0) { throw 'ffprobe verification failed.' }
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @($userPath -split ';' | Where-Object { $_ })
    if ($parts -notcontains $bin) {
        [Environment]::SetEnvironmentVariable('Path', (($parts + $bin) -join ';'), 'User')
    }
    Write-Host "FFmpeg verified in $bin. Open a new terminal before launching the converter."
    exit 0
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
