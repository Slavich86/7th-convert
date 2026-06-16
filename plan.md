# 7th Convert - development plan

## 0. Current Decision

Use this architecture for the first real version:

```text
Qt 6 Widgets UI
  -> Rust CLI/backend
      -> ffmpeg / ffprobe CLI processes
```

Do not start with direct FFmpeg API bindings in Rust. For this project, FFmpeg/FFprobe as subprocesses are simpler, easier to debug, easier to log, and enough for an MVP.

Target OS first: **Linux Fedora**.

Important reminder for later:

```text
Return to:
- MVP spec
- backend JSON contract
- preset schema
```

## 1. Product Goal

Working name:

- 7th Convert
- FFConvert Studio

Purpose:

A desktop converter for video, audio, and image sequences built around FFmpeg. The focus is VFX, editing, 3D, YouTube delivery, proxy generation, ProRes, EXR, PNG, JPG, WAV, MP4, and MKV.

The app should feel like a practical production tool, not a generic toy converter.

## 2. Stack Decision

### Recommended Stack

```text
UI: Qt 6 Widgets
UI layout: Qt Designer .ui files
Preview: QMediaPlayer + QVideoWidget
Backend/core: Rust
FFmpeg layer: ffmpeg / ffprobe command wrapper
Presets: JSON
Jobs: queue with per-job logs
```

### Why FFmpeg CLI First

FFmpeg CLI already supports:

- transcoding
- stream copy
- stream mapping
- audio/video filters
- image sequences
- progress output
- container and codec handling

FFprobe CLI already supports:

- JSON output
- stream inspection
- format inspection
- metadata
- duration
- stream selection

For MVP, the CLI approach avoids the complexity of FFmpeg library bindings and keeps debugging transparent.

### Rust vs Python

Use **Rust** for the real backend.

Rust is better here because:

- strong types help with jobs, presets, validation, and command building
- easier to package as a single backend executable
- safer command argument handling
- better long-term maintainability for a desktop tool

Python is useful only for quick experiments or helper scripts, for example:

```text
scripts/
  test_probe.py
  ffmpeg_experiments.py
  preset_experiments.py
```

Do not make Python the main backend unless the project direction changes.

## 3. High-Level Architecture

```text
app/
  ui/
    main_window.ui
    player_panel.ui
    media_info_panel.ui
    convert_settings_panel.ui
    queue_panel.ui
    log_panel.ui

  backend/
    rust/
      src/
        main.rs
        media_probe/
        preset_manager/
        command_builder/
        job_queue/
        progress_parser/
        path_manager/
        validation/
        logging/

  presets/
    video_delivery.json
    prores.json
    image_sequence.json
    audio.json
    vfx.json

  tests/
```

## 4. MVP Scope

The first version must be narrow and usable.

### MVP Must Have

1. Open video/audio/image sequence.
2. Show media info through ffprobe.
3. Show video preview through Qt.
4. Set In/Out range.
5. Choose one of the initial presets.
6. Start conversion.
7. Show progress.
8. Save per-job log.
9. Open output folder.
10. Copy generated FFmpeg command.

### MVP Initial Presets

```text
Video:
- H.264 MP4
- H.265 MP4
- ProRes 422 HQ MOV

Image sequence:
- PNG sequence
- JPG sequence

Audio:
- WAV PCM
```

### Not MVP

These are useful, but should come after the first stable version:

- audio normalization
- thumbnail strip
- advanced color management
- custom preset editor UI
- batch metadata editing
- direct FFmpeg library integration
- deep CXX-Qt integration
- waveform display
- hardware encoding

## 5. UI Plan

### Main Areas

```text
File / Presets / Queue / Tools / Help

Input
Preview player
Media info summary
In/Out controls
Conversion settings
Output path
Queue
Logs
```

### Main Tabs

```text
Convert
Media Info
Queue
Presets
Logs
```

### Convert Tab

Must include:

- input file/folder/sequence picker
- media info summary
- preview player
- timeline slider
- frame step backward/forward
- play/pause
- current timecode
- In button
- Out button
- Clear In/Out button
- range duration
- preset selector
- format selector
- codec summary
- resolution options
- FPS options
- audio options
- output path
- Add to Queue
- Convert Now

### Media Info Tab

Must include:

- format
- streams
- video
- audio
- subtitles
- metadata
- color
- timecode
- chapters
- raw ffprobe JSON
- Copy Media Info
- Save Report

### Queue Tab

Columns:

```text
Job | Input | Output | Preset | Status | Progress | ETA | Actions
```

Actions:

- start
- pause after current job
- cancel
- retry
- open output folder
- copy FFmpeg command
- save log

### Logs Tab

Show:

- generated command
- ffmpeg stderr
- progress events
- warnings
- errors
- exit code
- timestamps

## 6. Player and In/Out

### Player MVP

Use:

```text
QMediaPlayer + QVideoWidget
```

MVP player controls:

- preview area
- timeline slider
- play/pause
- frame step forward/back
- current timecode
- In
- Out
- Clear In/Out
- range duration
- mute/volume

### In/Out Accuracy

There are two cut modes:

```text
Fast cut:
- seek near keyframes
- fast
- not always frame accurate
- can use stream copy

Accurate cut:
- decode/transcode
- frame accurate
- slower
- required when filters are used
```

MVP decision:

Use **accurate cut** when the user sets In/Out. This is simpler and more predictable.

Important distinction:

- Qt preview may be approximate.
- FFmpeg conversion should be the authority for final In/Out output.

## 7. Media Info

Backend command:

```text
ffprobe -v error -print_format json -show_format -show_streams -show_chapters INPUT
```

Show at least:

```text
File:
- path
- name
- size
- container
- duration

Video:
- codec
- resolution
- fps
- pixel format
- color range
- color space
- bit depth
- alpha yes/no/unknown

Audio:
- codec
- sample rate
- channels
- bitrate
- language

Timecode:
- start time
- duration
- estimated frame count

Streams:
- video
- audio
- subtitles
- attachments

Metadata:
- creation time
- encoder
- tags

Other:
- chapters
- probe errors
- raw JSON
```

## 8. Supported Formats

### MVP Input

```text
Video:
- mp4
- mov
- mkv
- avi
- webm

Audio:
- wav
- mp3
- aac
- flac
- m4a

Image sequences:
- png
- jpg
- exr
```

### MVP Output

```text
Video:
- MP4 H.264
- MP4 H.265
- MOV ProRes

Audio:
- WAV
- MP3
- AAC
- FLAC

Image:
- PNG sequence
- JPG sequence
- EXR sequence later
```

## 9. VFX-Specific Requirements

### Pixel Formats

Useful formats:

```text
yuv420p
yuv422p10le
yuv444p10le
rgb24
rgba
gbrp10le
gbrap12le
```

Important for:

- ProRes 4444
- ProRes 4444 XQ
- PNG with alpha
- EXR
- high-bit-depth workflows

### Alpha Channel

The app should show:

```text
Has alpha: yes / no / unknown
```

Alpha-aware presets:

- ProRes 4444 with alpha
- PNG RGBA sequence
- EXR sequence

### FPS Modes

Do not mix these in UI. They are different operations:

```text
Keep source FPS
Force output FPS
Interpret source FPS
Convert FPS
```

For MVP:

- Keep source FPS
- Force output FPS

### Resolution Modes

Full plan:

```text
Keep original
Scale width
Scale height
Scale to preset
Fit inside
Crop
Pad
```

For MVP:

- Keep original
- Scale to width
- Scale to height
- Force even dimensions

### Safe Dimensions

H.264/H.265 commonly need even dimensions.

Validation should warn about:

```text
width not divisible by 2
height not divisible by 2
```

The app should offer:

```text
Force even dimensions
```

## 10. Presets

### Preset Groups

```text
ProRes
YouTube / Web
Image Sequence
Audio
VFX
Custom
```

### ProRes Presets

```text
ProRes Proxy
ProRes LT
ProRes 422
ProRes 422 HQ
ProRes 4444
ProRes 4444 XQ
```

Common settings:

```text
container: mov
video codec: prores_ks
profile: proxy / lt / standard / hq / 4444 / 4444xq
audio codec: pcm_s16le or pcm_s24le
```

### Web Presets

```text
H.264 MP4 High Quality
H.264 MP4 Small Size
H.265 MP4
WebM VP9 later
```

### Image Sequence Presets

```text
PNG 8-bit
PNG 16-bit later if supported
JPG quality 2-5
EXR later
```

### Audio Presets

```text
WAV 48kHz 24-bit
WAV 44.1kHz 16-bit
MP3 320k
AAC 192k
FLAC
```

## 11. Backend JSON Contract Draft

This section is a draft. Return to it before implementation.

### Probe Request

```json
{
  "action": "probe",
  "input": "/path/input.mov"
}
```

### Probe Response

```json
{
  "ok": true,
  "input": "/path/input.mov",
  "format": {
    "container": "mov",
    "duration_seconds": 120.5,
    "size_bytes": 123456789,
    "bitrate": 8000000
  },
  "streams": [
    {
      "index": 0,
      "type": "video",
      "codec": "prores",
      "width": 1920,
      "height": 1080,
      "fps": 25.0,
      "pix_fmt": "yuv422p10le",
      "has_alpha": "unknown"
    }
  ],
  "metadata": {},
  "chapters": [],
  "raw_ffprobe_json": {}
}
```

### Job Request

```json
{
  "id": "uuid",
  "input": "/path/input.mov",
  "output": "/path/output.mov",
  "in_point": "00:00:10.000",
  "out_point": "00:00:20.000",
  "preset": "prores_422_hq",
  "video": {
    "codec": "prores_ks",
    "profile": "hq",
    "scale": {
      "mode": "keep",
      "force_even": false
    },
    "fps": {
      "mode": "source"
    },
    "pix_fmt": "yuv422p10le"
  },
  "audio": {
    "mode": "encode",
    "codec": "pcm_s24le",
    "sample_rate": 48000
  },
  "overwrite": "ask"
}
```

### Progress Event

```json
{
  "job_id": "uuid",
  "status": "running",
  "progress": 0.42,
  "time_seconds": 42.0,
  "duration_seconds": 100.0,
  "speed": "1.25x",
  "eta_seconds": 46,
  "output_size_bytes": 12345678
}
```

### Job Result

```json
{
  "job_id": "uuid",
  "ok": true,
  "status": "finished",
  "output": "/path/output.mov",
  "exit_code": 0,
  "log_path": "/path/job.log",
  "ffmpeg_args": ["-i", "/path/input.mov", "-c:v", "prores_ks", "/path/output.mov"]
}
```

## 12. Preset Schema Draft

This section is a draft. Return to it before implementation.

```json
{
  "id": "prores_422_hq",
  "name": "ProRes 422 HQ MOV",
  "group": "ProRes",
  "description": "High-quality editing format.",
  "output": {
    "container": "mov",
    "extension": "mov"
  },
  "video": {
    "enabled": true,
    "codec": "prores_ks",
    "profile": "hq",
    "pix_fmt": "yuv422p10le",
    "bitrate": null,
    "crf": null,
    "quality": null
  },
  "audio": {
    "enabled": true,
    "codec": "pcm_s24le",
    "sample_rate": 48000,
    "channels": "source"
  },
  "filters": {
    "scale": "keep",
    "fps": "source",
    "force_even_dimensions": false
  },
  "capabilities": {
    "supports_alpha": false,
    "supports_stream_copy": false,
    "supports_in_out": true
  }
}
```

## 13. Command Builder Rules

Never build FFmpeg commands as one string.

Correct:

```text
command: ffmpeg
args: ["-i", input, "-c:v", "libx264", output]
```

This avoids bugs with:

- spaces in paths
- quotes
- Cyrillic paths
- shell escaping
- special characters

### Command Builder Must Decide

- input type: file, folder, or image sequence
- output type: video, audio, or image sequence
- stream mapping
- stream copy vs transcode
- In/Out mode
- video codec
- audio codec
- filters
- pixel format
- frame rate
- output naming
- overwrite behavior

### Stream Copy vs Transcode

Use stream copy only when:

- codec/container combination is valid
- no filters are used
- no accurate In/Out is required
- no resize/FPS/pix_fmt conversion is required
- no audio processing is required

Use transcode when:

- changing codec
- changing resolution
- changing FPS
- changing pixel format
- applying filters
- doing accurate cuts
- changing audio format

## 14. Progress Handling

Do not parse random human-readable percentage text.

Preferred approach:

```text
ffmpeg -progress pipe:1 -nostats ...
```

Backend should parse progress fields and compare output time with duration from ffprobe.

UI should show:

- progress bar
- elapsed time
- remaining time
- current speed
- output size
- current status

## 15. Validation Before Convert

Validation is required for MVP.

Check:

- ffmpeg exists
- ffprobe exists
- input exists
- input is readable
- output folder is writable
- output already exists
- invalid output extension
- unsupported codec/container combination
- odd width/height for H.264/H.265
- missing audio stream when audio is requested
- missing video stream when video is requested
- invalid image sequence pattern
- invalid In/Out range
- In point is after Out point
- no duration available

Overwrite behavior:

```text
overwrite
auto rename
ask
skip
```

## 16. Output Naming Templates

Support templates:

```text
{input_name}_{preset}.{ext}
{input_name}_prores_hq.mov
{input_name}_png.%04d.png
{input_name}_jpg.%04d.jpg
```

For image sequences, validate that the output pattern contains a frame placeholder:

```text
%04d
%05d
%06d
```

## 17. Error Explanation Layer

FFmpeg errors should be translated into useful messages when possible.

Examples:

```text
width not divisible by 2
  -> H.264/H.265 usually need even dimensions. Enable "Force even dimensions" or choose another codec.

No such file or directory
  -> Input or output path is invalid.

Invalid argument
  -> Codec/container/settings combination may be invalid.
```

Always keep the raw FFmpeg log available.

## 18. Project / Session Save

Later feature, but the architecture should not block it.

A session should eventually store:

- input list
- job queue
- selected presets
- output paths
- In/Out ranges
- logs paths
- app settings

## 19. Implementation Phases

### Phase 1 - UI Prototype

Only UI, no real conversion logic:

- Main Window
- Player Panel
- Media Info Panel
- Convert Settings Panel
- Queue Panel
- Log Panel

Output:

- `.ui` files from Qt Designer
- screenshots or rough UI review

### Phase 2 - Rust CLI Backend Prototype

Backend features:

- probe file
- parse ffprobe JSON
- load presets
- validate job
- build FFmpeg args
- run conversion
- parse progress
- save logs

Output:

- working Rust CLI
- sample job JSON
- sample preset JSON
- sample logs

### Phase 3 - UI + Backend Connection

Possible integration options:

```text
Qt/C++ UI + Rust CLI backend
  simplest and most stable for start

Qt/C++ UI + Rust core through CXX-Qt
  better long-term integration, more complex

Full Rust GUI without Qt
  not preferred because UI should be built in Qt Designer
```

Recommended start:

```text
Qt Designer UI + separate Rust CLI backend
```

## 20. Rough UI Layout

```text
┌──────────────────────────────────────────────────────────────┐
│ File  Presets  Queue  Tools  Help                            │
├──────────────────────────────────────────────────────────────┤
│ Input:  /path/to/input.mov                         [Browse]  │
├──────────────────────────────┬───────────────────────────────┤
│                              │ Media Info                    │
│        Video Preview         │ Codec: ProRes                 │
│                              │ Size: 3840x2160               │
│                              │ FPS: 25                       │
│                              │ Audio: PCM 48kHz              │
├──────────────────────────────┴───────────────────────────────┤
│ 00:00:10:12  [<] [Play] [>]  Timeline              00:02:30  │
│ [Set In]  00:00:10:00    [Set Out] 00:00:30:00               │
├──────────────────────────────────────────────────────────────┤
│ Preset: ProRes 422 HQ MOV                                     │
│ Video: prores_ks / hq     Audio: PCM 24-bit                   │
│ Output: /path/to/output.mov                        [Browse]  │
├──────────────────────────────────────────────────────────────┤
│ [Add to Queue] [Convert Now]                                  │
├──────────────────────────────────────────────────────────────┤
│ Queue / Logs                                                   │
└──────────────────────────────────────────────────────────────┘
```

## 21. Next Planning Tasks

Before writing implementation code, create these planning documents/sections:

1. MVP spec
2. Backend JSON contract
3. Preset schema
4. Command builder rules
5. Validation rules
6. First UI wireframe
