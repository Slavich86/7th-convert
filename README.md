<p align="center">
  <img src="docs/images/social-preview.jpg" alt="7th VFX convertor" width="900">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Player-Rust-orange" alt="Rust player">
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/UI-Qt%206-green" alt="Qt 6">
</p>

# 7th VFX convertor

Desktop converter for VFX video, image sequences, still images and audio, with a **Rust preview player**, a Python/PySide6 (Qt 6) interface and FFmpeg conversion.

[Українська](README_UA.md) · [Install and run](#install-and-run) · [Rust player](#rust-player) · [Formats](#formats) · [Clean distribution](#clean-distribution)

![7th VFX convertor main window](docs/images/main-window.png)

## Features

- Video and image-sequence playback, seeking, frame stepping and In/Out markers through the Rust player.
- Sequence detection, original frame numbering and separate ranges for sequences with missing frames.
- Full-source or selected-range conversion, batch queue, saved presets and drag and drop.
- Frame sampling: save the first frame and every Nth source frame while preserving global source numbering.
- Output size, scale and automatic/manual pixel aspect, including conversion to square pixels.
- OCIO color management: bundled Nuke-style config or a custom config, including ACES.
- Source/external audio, audio-only export and adding audio to MP4 without re-encoding video.
- Media information, metadata and opening the output folder after conversion.

## Rust player

**Playback control, frame selection, timing and seeking run in a standalone Rust engine.** FFmpeg decodes the media; Python/Qt displays the frames and handles the interface and audio. The converter and export pipeline remain Python + FFmpeg.

The player supports video timestamps (including VFR), image sequences with their source numbering, frame stepping and color transforms on pause and during playback. Timeline dragging uses a bounded preview cache; releasing the slider selects the exact final position and restores play/pause state.

Preview uses an 8-bit RGBA display buffer sized for the viewport. This does not reduce export precision or change source media. GPU decoding and individual channel views are not implemented; long or uncached backward seeks can take time.

A native player build is required for each OS/CPU. Install Rust/Cargo using [rustup](https://rustup.rs/) and native linker/build tools (MSVC Build Tools on Windows; Apple Command Line Tools on macOS). The pinned toolchain is in `rust/player/rust-toolchain.toml`.

Use the platform launcher to prepare the local environment, build the Rust player when Cargo is available, and start the converter. A current compatible player binary is reused automatically; no manual Python command is needed.

```bash
# Linux / macOS
./7th-vfx-convertor.sh
```

```powershell
# Windows
.\7th-vfx-convertor.bat
```

The executable is installed in `seventh_convert/bin/`. Cargo is not needed to run an existing compatible build. If neither a compatible binary nor Cargo is available, the launcher reports use of Qt preview. A Cargo build failure stops startup and shows diagnostics.

## Install and run

Requirements: **Python 3.11+**, **FFmpeg and ffprobe** on `PATH`. `uv` is optional; launchers create a local `.venv` using `uv` or Python's `venv`/`pip`. Do not copy a virtual environment between computers.

Get the sources once:

```bash
git clone https://github.com/Slavich86/7th-convert.git
cd 7th-convert
```

### Linux

Install FFmpeg through your distribution's package manager, then run:

```bash
chmod +x 7th-vfx-convertor.sh
./7th-vfx-convertor.sh
```

The optional `.desktop` launcher must stay beside the `.sh` file.

### Windows

Install Python 3.11+ and run:

```powershell
.\7th-vfx-convertor.bat
```

If FFmpeg is missing, the launcher offers the included `install-ffmpeg.bat`, which uses `winget`. Keep `install-ffmpeg.ps1` beside it. After installation, reopen the terminal so the updated `PATH` is available.

### macOS

With [Homebrew](https://docs.brew.sh/Installation) installed:

```bash
brew install python@3.13 uv ffmpeg
chmod +x 7th-vfx-convertor.sh
./7th-vfx-convertor.sh
```

Alternatively, make `7th-vfx-convertor.command` executable and double-click it in Finder; it starts the same `.sh` launcher and must stay beside it. The launcher includes Homebrew paths for Apple Silicon and Intel. To build the native Rust player, install Cargo and Apple Command Line Tools (`xcode-select --install`). A signed/notarized `.app` is not included.

**Platform verification:** playback and integration checks have run on Linux. Windows and macOS launchers are included, but installation and playback on actual Windows/macOS machines still need verification.

Launchers resolve paths relative to their own folder, including paths containing spaces. On Windows, you can also double-click `7th-vfx-convertor.bat`.

`Help → Check for Updates` requires a Git clone. ZIP downloads and copied `publish/` contents have no `.git` directory and cannot self-update this way.

## Formats

| Media | Input | Output |
|---|---|---|
| Video | MOV, MP4, MKV, TS, MXF, M4V, AVI, WEBM | MOV (ProRes), MP4 (H.264 / H.265, including NVENC) |
| Images / sequences | EXR, DPX, PNG, JPG/JPEG, TGA/TARGA, TIF/TIFF, GIF | EXR, DPX, PNG, JPG, TARGA, GIF |
| Audio | WAV, MP3, AAC, M4A, FLAC, OGG | WAV, MP3, AAC |

Actual codec availability depends on the installed FFmpeg build and hardware. EXR defaults to half float with selectable compression; PNG supports RGB/RGBA at 8 or 16 bits. JPG quality and GIF palette/dithering are configurable.

## Color, geometry and audio

Input and output transforms are separate. `Color Management: None` applies no OCIO LUT, FFmpeg color transform or forced output color metadata. Choose the bundled `Nuke Default OCIO` config or your own `config.ocio` for managed workflows, including ACES.

Pixel aspect can be detected automatically (including EXR metadata) or set manually. `Keep Original Pixels` preserves the raster; `Resize to Square Pixels` adjusts it for square-pixel delivery. For example, a 2880 × 2160 source with PAR 2.0 displays at 5760 × 2160.

Use embedded audio or `Audio Input` for an external file. `Add Audio Without Re-encoding Video` copies the MP4 video stream and encodes/muxes audio; resize, FPS and color controls are disabled in that mode.

Presets store conversion, range, geometry, color and audio settings locally. Overwriting output requires the explicit overwrite option; source media must not be overwritten.

## Hotkeys

Active in the player area, preview placeholder, Play button and timeline; typing in In/Out fields is preserved.

| Key | Action |
|---|---|
| Left / Right | Back / forward one frame |
| Down / Up | Back / forward ten frames |
| I / O | Set In / Out marker |

## Clean distribution

This repository contains the ready converter copied from the development workspace's `publish/` folder. Clone it or extract the downloaded ZIP, then run the launcher for your platform from that folder.

The package includes runtime Python code, presets, OCIO/LUT resources, icons, launchers, the FFmpeg installer, user documentation, minimal Rust sources and a release **Linux** player. Tests, development scripts, benchmarks, old logs, caches and virtual environments are excluded. Detailed player tracing is off by default.

Dependencies are installed into a new local `.venv`; FFmpeg/ffprobe are still required. Windows and macOS need their own native player build from the included Rust sources, as described above. The generated `publish/` folder itself is not nested inside this repository: its contents are placed directly at the repository root.

## Donate

Support development via PayPal: `sl.oxuta@gmail.com`.
