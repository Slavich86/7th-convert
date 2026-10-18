# Rust preview player contract

The converter stays Python/PySide6. `rust/player` is a standalone safe-Rust
playback engine; FFmpeg remains the decoder. No PostEffects project/UI or GPU
framework is imported. Rust owns playback state, timestamp scheduling and a
bounded decode queue. Python's adapter presents copied RGBA8 frames in Qt.

## Transport and ownership

Protocol v1 uses UTF-8 JSON commands, one per stdin line. Stdout consists of one
JSON event line followed by exactly `bytes` raw bytes for a `frame` event. Other
events have no payload. Stderr is diagnostic only. Commands carry `generation`;
open/seek/scrub/step/filter changes advance it. The adapter rejects stale events.
Only one frame may be awaiting an `ack` at a time, bounding the IPC backlog.

Commands: `open` (source spec), `play`, `pause`, `seek` (microseconds or sequence
index), `scrub` (drag-preview timestamp or sequence index), `step` (signed frame
count), `ack`, `close`, `quit`. Events: `frame`,
`state` (paused/playing/ended), `error`. Closing stops the decoder; quit also
exits the engine. EOF on stdin is quit. The Qt adapter kills an unresponsive
engine after a bounded shutdown wait.

Frames are tightly packed, top-down, **straight-alpha RGBA, 8 bits/channel**,
stride = width * 4. Color conversion occurs in FFmpeg before RGBA8 quantization.
An optional source `max_width`/`max_height` bounds the display raster (both
zero means native size; otherwise both must be 1..8192). FFmpeg fits within
these bounds without upscaling, **before** the display color transform. When a
color filter is active, scaling feeds float RGB with straight alpha to the
transform, followed by the final RGBA8 conversion. This order is identical on
pause and during playback. For nonlinear transforms, downsampling before the
transform can differ at edges from downsampling an already transformed full-size
image; preview is not a pixel-exact export proof at reduced resolution. Native
resolution keeps the original transform order without downsampling. Export
filters are unchanged. Frame headers additionally carry original
`source_width`/`source_height`; output geometry must use these, never the
scaled buffer. Qt requests its viewport size in device pixels and reloads after
a debounced resize. Pixel aspect compensates integer scaling rounding.
None means no forced color transform. The frame is display-only and is never
used as export input. Qt takes its own QImage copy; no pointers cross processes.
The adapter retains only the current display image. The Rust decoder queue is
bounded to one queued frame, one scheduled frame and the reader's current frame
(stepping may retain one additional skipped frame); each frame has a
64 MiB limit. During video dragging only, an additional recent-frame cache has
a total 64 MiB / 128-frame limit. It is cleared by exact seek, play, step, open
and close. No whole-sequence pixel cache is allocated.

## Sources and time

Video positions are signed integer microseconds relative to the source start.
FFmpeg `showinfo` timestamps govern playback, including VFR; wall-clock deadlines
are anchored to an Instant and a media timestamp, never accumulated rounded FPS.
Sequence positions are zero-based indices in an explicit ordered file list.
Their timestamps derive from index and rational FPS. Source filenames/numbers
are preserved by the Python sequence resolver and are not renumbered by Rust.
Sequence input uses an FFconcat manifest fed over stdin, including explicit
per-file framerate and duration. Paths are escaped, never shell-interpolated.

Seek selects the first decoded video frame at or after the requested timestamp;
seeking beyond EOF holds the last frame.
Dragging pauses audio/video and coalesces pointer movements: one preview request
is in flight and only the latest pending position is retained. Video `scrub`
can reuse the current decoder for forward targets within one second, or reuse a
cached frame within 40 ms of the requested timestamp. Otherwise it restarts
decoding. Sequence scrubbing selects the exact requested index without this
video cache. Release always issues an exact `seek`, discards stale drag results,
and restores the previous playing/paused state after the exact frame arrives.
Audio is positioned once on playback resumption. This leaves normal playback
scheduling and export independent of the drag-preview path.
Forward stepping decodes from the displayed frame. Backward stepping uses
ffprobe timestamps (a bounded tail of metadata, not pixel buffers) to identify
the preceding source frame; this can be slower on long files. Step pauses.
Playback stops on the last frame; Play at EOF restarts. In/Out markers continue
to describe the export interval; the player does not change export range rules.
The UI uses an icon-only Play/Pause toggle and a separate Stop button, with
tooltips and accessible action names. Stop cancels an active drag/pending seek,
stops audio and seeks to the first source frame in the paused state. The source
stays open and In/Out markers are preserved; Play can start again immediately.

## Threading, cancellation, errors

Qt controls and paints on its main thread. Protocol IO runs in a Python worker;
Rust frame reading and FFmpeg stderr draining run off the command/timing loop.
FFmpeg decoding and filter slicing each use up to eight available CPU threads;
color-managed playback must not force the filter graph to one thread.
New opens/exact seeks terminate and reap the prior decoder before replacement. Old
frame results are ignored by generation. Pipes are drained continuously and
FFmpeg stderr is retained as a bounded diagnostic tail. Errors report the
operation and decoder diagnostic; no automatic fallback masks decoder failure.

Video audio continues through a separate Qt multimedia audio output. It is
positioned once when starting/resuming or after a user seek, then runs
continuously. Individual frames must never seek or restart audio to correct
small timing differences; doing so causes repeated decoding and interruptions.
Pause, stepping and source changes pause/stop it. Image sequences and silent
sources must never restart a previous source's audio. Export/external audio
settings remain independent. Video uses its monotonic playback clock, so
sustained overload on other hardware can still cause A/V drift; this is not an
audio-master-clock implementation. Linux real-output measurements and limits
are recorded in [the performance report](player-performance.md).

## Build and distribution

Only serde/serde_json are added to Rust, for the versioned protocol. The engine
uses the existing runtime ffmpeg/ffprobe executables, not an FFmpeg SDK. Build
with Cargo on Linux or Windows; no Python ABI extension or new Python package
is required. A Python build command installs the release binary into
`seventh_convert/bin/`. The native engine is selected when installed; otherwise
the UI explicitly reports that the existing Qt preview is in use.

Detailed diagnostics are disabled by default; enable them explicitly with
`SEVENTH_PLAYER_TRACE=1` when investigating visible skips. See
[player debugging](player-debugging.md). Frame `late_us` reports lateness
against the Rust monotonic scheduling deadline. The adapter traces pipe reads,
UI dispatch, image preparation, paint completion and replacement before paint.
Diagnostic fields do not change scheduling, acknowledgements or audio behavior.

Verification: Rust unit tests; real FFmpeg protocol tests; offscreen Qt integration
for open/play/pause/seek/step/reopen/close and transformed pixels. Visual playback,
audio-device behavior and Windows execution must be reported separately.
