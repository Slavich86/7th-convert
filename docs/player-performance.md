# Player performance investigation — 2026-10-18

Linux, Python 3.11+, PySide6 6.11.1, Rust 1.99.0, 32 logical CPUs.
Source: `/mnt/ARCHIVE/PROJECT_ARCHIVE/BREVNO/SRC/ASSASSIU_S001_S001_T013.MOV`:
3840×2160 ProRes HQ, 10-bit 4:2:2, 25 FPS, 679 frames, duration 27.16 s;
48 kHz, four-channel 24-bit PCM audio. Source file was not modified.

## Reproduction and causes

Open the source in `MainWindow(use_media=True)`, start after the first frame,
record each `frameReady` monotonic arrival time and source timestamp through
`ended`. Silent runs set `current_input_has_audio=False` before the deferred open.
Use isolated QSettings so tests do not alter user settings. Runs used Qt offscreen
presentation and, for sound, the real Linux default audio output outside the
sandbox. Timing measures Qt delivery, not a monitor's physical refresh.
The initial baseline and final runs used the same default 1180×844 window.
Those initial runs had no active color filter. They did not reproduce the user's
later color-managed playback settings; see the native-trace investigation below.
Disk caches were warm; these are not cold-start benchmarks.

Three bottlenecks were confirmed separately:

- The decoder was hard limited to two threads. FFmpeg-only decoding of the same
  100 frames to RGBA/PAM with checksum disabled took 4.73 s with two threads,
  versus 2.85 s with eight. The decoder now uses up to eight available threads.
- Full 4K RGBA buffers carried 33,177,600 bytes through both FFmpeg→Rust and
  Rust→Python pipes, then incurred Qt copies/scaling. Even after increasing
  decoder threads, full-resolution UI delivery reached only 19.92 FPS.
  The UI now requests viewport-sized display buffers (703×395, 1,110,740 bytes,
  in the default test window). Original raster dimensions travel separately;
  export still reads the original source and retains original dimensions/PAR.
  Resizing requests an updated display resolution after a debounce.
- The audio adapter repeatedly sought and called play to follow delayed frames.
  A 6.5-second trace recorded 22 position resets; the corresponding fixed trace
  recorded just one initial positioning call. Removing these resets alone made
  audio continuous but exposed video drift, so display throughput also needed
  correction. A 250 ms frame watchdog that paused audio was removed.

The unused full-frame `showinfo` checksums were also disabled. FFmpeg-only
100-frame decoding with two threads fell from 5.41 s to 4.73 s. Source PTS and
original raster geometry are still read from `showinfo` before display scaling.

## Full-clip comparison

| Measurement | Before, silent | After, silent | Before, sound | After, sound |
|---|---:|---:|---:|---:|
| Delivered FPS | 19.782 | 24.993 | 16.367 | 24.997 |
| Frames | 679/679 | 679/679 | 679/679 | 679/679 |
| First frame (ms) | 956 | 1070 | 1323 | 1202 |
| Playback wall time (s) | 34.274 | 27.128 | 41.425 | 27.123 |
| Wall time minus media time (ms) | 7154 | 8 | 14305 | 3 |
| Frame interval p95 (ms) | 57.8 | 41.7 | 97.1 | 41.7 |
| Intervals >60 ms | 12 | 1 | 179 | 0 |
| Audio decoded (s) | — | — | 43.565 | 27.104 |

All full-clip runs delivered exactly `[0, 40000, ..., 27120000]` microseconds:
no source frames were missing or duplicated at the protocol/Qt delivery boundary.
These measurements did **not** count QWidget paint events and do not establish
that every delivered frame was painted or physically displayed. A later user
report identified visible skipping despite correct delivery; the temporary
[paint-level trace](player-debugging.md) now separates these stages. In the fixed sound run, audio/video
position difference was median −38 ms, p95 absolute 56 ms, maximum absolute 59 ms.
Audio remained playing during playback; no player/audio errors were reported.
The final short trace also confirmed no periodic seeks.

A second full-clip sound run used a 1920×1080 window (1166×656 display buffer):
24.993 FPS, all 679 frames, p95 frame interval 42.1 ms, maximum 52.2 ms,
maximum absolute audio/video position difference 43 ms. Sampled peak RSS for
Python + Rust + their children was 212.8 MiB (100 ms sampling; shared pages may
be counted more than once). This is the fixed run only, not a baseline memory
comparison. [Large-window result](benchmarks/player-after-large-sound.json).

Machine-readable results: [before silent](benchmarks/player-before-silent.json),
[before sound](benchmarks/player-before-sound.json),
[after silent](benchmarks/player-after-silent.json),
[after sound](benchmarks/player-after-sound.json).

## Native trace: active OCIO LUT (follow-up)

The user's Wayland trace (2026-10-18, PID 44861) identified a different bottleneck:
all 330 received frames were painted, Qt queue p95 was 0.31 ms, callback p95
0.77 ms and paint p95 0.27 ms. During playback, incoming video ran at **2.451
FPS**, with a median 416.6 ms interval. Rust lateness reached 119.99 s at source
PTS 13.08 s. The source had an active OCIO-generated 32³ tetrahedral LUT;
previous benchmarks used an empty filter. This was upstream color processing,
not missing Qt paints. [Native trace summary](benchmarks/player-native-lut-before.json).

FFmpeg reproduced it using the same MOV, exact cached LUT, 684×692 bounds,
eight decode threads and RGBA/PAM output to `/dev/null`. Whole-command timings
include startup and a warm filesystem cache:

| Pipeline | Filter threads | Frames | Throughput |
|---|---:|---:|---:|
| Full 4K LUT → resize | 1 | 25 | 2.26 FPS |
| Full 4K LUT → resize | 8 | 25 | 10.99 FPS |
| Full 4K LUT → resize | 16 | 25 | 15.65 FPS |
| Full 4K LUT → resize | 32 | 25 | 16.31 FPS |
| Resize → float RGB → LUT | 8 | 50 | 40.86 FPS |

The decoder now downsamples **before** display color processing and uses up to
eight filter threads. Active transforms receive float RGB with straight alpha;
8-bit quantization happens afterwards. Color math and LUT selection remain the
same on pause and play. Export processing is unchanged. As resampling and a
nonlinear transform do not commute, a reduced-resolution preview is not an exact
pixel match to a full-resolution export subsequently resized. On the first
actual MOV frame, the old/new preview difference was mean 0.145 RGB code values,
p95 1, maximum 54 at an edge, with identical alpha. A synthetic nonlinear HDR
EXR regression checks order, preservation of values above 1 before color math,
straight alpha, original geometry and equal paused/playing pixels.

Full-clip follow-up uses the **same LUT and 684×692 bounds as the native trace**
(resulting 684×385 RGBA8 buffer), isolated Qt settings, offscreen Qt painting and
real Linux audio output for the sound run:

| Measurement | Silent | With sound |
|---|---:|---:|
| Delivered FPS | 24.996 | 24.996 |
| Delivered / painted frames | 679 / 679 | 679 / 679 |
| Replaced before paint | 0 | 0 |
| Wall time (27.12 s source interval) | 27.124 s | 27.125 s |
| First frame | 1098 ms | 1208 ms |
| Frame interval p95 / maximum | 41.0 / 47.1 ms | 41.8 / 47.3 ms |
| Intervals over 60 ms | 0 | 0 |
| Maximum absolute audio/video position difference | — | 48 ms |

No player/audio errors occurred. No timestamps were omitted or duplicated.
Results: [silent](benchmarks/player-lut-after-silent.json),
[sound](benchmarks/player-lut-after-sound.json). FFmpeg isolation results:
[full-resolution LUT](benchmarks/player-native-lut-bench.json),
[threading / float-before-scale](benchmarks/player-native-lut-scaled-bench.json),
[early scaling](benchmarks/player-native-lut-early-bench.json).
This confirms throughput and Qt painting in the test environment; physical
Wayland/compositor presentation with the rebuilt binary still needs a native
user run. The original native process keeps its old executable until restart.
The installer now replaces the Linux executable atomically so installing an
update while that process is open does not fail with `Text file busy`.

## Timeline dragging (follow-up)

The old slider restarted a 40 ms debounce on every movement. At 20 ms pointer
intervals this sent no seeks until the pointer stopped. Each subsequent seek
also restarted FFmpeg, adding substantial startup time on this 4K source.

The UI now retains one in-flight preview and only the latest pending position.
A separate Rust `scrub` command reuses nearby forward decoding and a bounded
64 MiB / 128-frame video cache. The cache is used only while dragging; exact
seek, play and source changes clear it. Release performs an exact seek and
restores the previous playing/paused state. Audio remains paused during dragging
and resumes with one position reset. Incidental layout resizes no longer reopen
the source during a drag. Export and the normal playback scheduler are unchanged.

Measurements use the same MOV and active LUT, warm disk cache and Qt offscreen.
Pointer events arrive every 20 ms for three seconds:

| Drag workload | Frames delivered while held | Release to exact final frame |
|---|---:|---:|
| Before, 100 ms source movement per event | 0 | 705 ms |
| After, 100 ms per event (5× source speed) | 4 | 561 ms |
| After, 30 ms per event (1.5× source speed) | 97 | 632 ms |

The moderate forward drag delivers about 32 preview updates per second; the
first cold decoder start still takes 698 ms. Large jumps and uncached backward
positions still require decoder startup, so this does not promise that every
pointer position can be displayed instantly. The final 6500 ms request selects
the exact first frame at or after it, at 6520 ms for this 25 FPS source.

A separate play → drag → release test with real Linux audio output delivered
127 resumed frames at 25.000 FPS with contiguous 40 ms timestamps, a maximum
42.5 ms delivery interval and maximum sampled A/V difference of 24 ms. There
were exactly two audio positioning calls: initial play and post-drag resume.
No errors occurred. Results: [before](benchmarks/player-scrub-before.json),
[fast drag](benchmarks/player-scrub-fast-after.json),
[moderate drag](benchmarks/player-scrub-moderate-after.json),
[audio resumption](benchmarks/player-scrub-audio-after.json).
The full 679-frame sound/LUT playback was also repeated after this change:
24.995 FPS, 679 delivered and painted frames, zero replacements before paint,
maximum interval 46.7 ms, maximum sampled A/V difference 30 ms and no errors.
[Normal playback regression](benchmarks/player-scrub-normal-sound.json).
These count Qt frame delivery; physical pointer/compositor smoothness requires
a native user run after restarting the converter.

## Regression checks and limits

35 Python tests pass, including real FFmpeg playback, VFR, fractional FPS,
sequences with numbering gaps, LUT pixels, float EXR straight alpha, seek,
reverse step, EOF, cancellation, Qt controls, and new display-size/source-size
and audio-reset regressions, continuous dragging before release, exact final
position and restoration of play/pause. Four Rust unit tests pass, including
scrub-cache byte/frame limits and release. Release binary built
and installed in `seventh_convert/bin/`. Qt offscreen screenshots were inspected.
Cargo fmt/Clippy could not run because those components are not installed.

The audio checks used a real output device and decoded-buffer/position telemetry;
they do not constitute a listening test or a measured speaker/monitor lipsync
latency test. Windows and cold-cache startup were not tested. The audio output
and video still use separate clocks; sustained decode overload on other hardware
may cause drift. Preview resize can restart decoding/audio once; it is debounced.
Peak RSS was not sampled in the original baseline, so there is no measured
before/after total-memory comparison. The smaller display payload is an exact
byte-count comparison, not a claim about total-process RSS.
