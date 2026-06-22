# 7th Convert

First working CLI prototype for a desktop media converter backend.

The current implementation is a Python prototype because the local machine does not have the Rust toolchain installed yet. It uses the architecture planned for the Rust backend:

```text
CLI command
  -> command builder
      -> ffmpeg / ffprobe subprocesses
```

## Requirements

- Python 3.11+
- PySide6
- ffmpeg
- ffprobe

## Usage

Start the Qt UI prototype:

```bash
python3 -m seventh_convert.ui
```

List presets:

```bash
python3 -m seventh_convert presets
```

Probe a file:

```bash
python3 -m seventh_convert probe input.mov
```

Preview the generated ffmpeg command:

```bash
python3 -m seventh_convert build input.mov output.mp4 --preset h264_mp4
```

Convert a file:

```bash
python3 -m seventh_convert convert input.mov output.mp4 --preset h264_mp4 --overwrite --log logs/job.log
```

Convert only a range:

```bash
python3 -m seventh_convert convert input.mov output.mov --preset prores_hq_mov --in 00:00:10.000 --out 00:00:20.000
```

Export a PNG sequence:

```bash
python3 -m seventh_convert convert input.mov frames/shot.%04d.png --preset png_sequence
```

## Current Presets

- `h264_mp4`
- `h265_mp4`
- `prores_hq_mov`
- `png_sequence`
- `jpg_sequence`
- `wav_pcm`
