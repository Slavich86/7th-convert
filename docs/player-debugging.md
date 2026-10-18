# Temporary playback diagnostics

The temporary trace is disabled by default. Set `SEVENTH_PLAYER_TRACE=1`
explicitly in the development environment, restart the converter, reproduce the
visible skips for 3–5 seconds in its normal desktop window, then pause. That is
enough to locate persistent slow decoding; longer captures are only useful for
intermittent stalls.
The Logs tab shows `Temporary player diagnostic log: <absolute path>`.
Default: `<repository>/logs/player-debug-<UTC timestamp>-<PID>.jsonl`. Each window/session gets a separate file; rotation keeps two 2 MiB backups per process in addition to the current
file. Preserve matching `.1`/`.2` files when analyzing longer sessions.

`SEVENTH_PLAYER_TRACE=0` disables tracing; `SEVENTH_PLAYER_TRACE_DIR` overrides
the directory. Write failures are reported in the Logs tab. The writer has a
bounded queue, runs separately from Qt, and counts lost diagnostic records.
The trace records media paths/settings, timestamps and timings, never pixels
or audio samples. Do not infer skipped frames from gaps in incomplete logs.

Events use a single-process `perf_counter_ns` clock (`mono_ns`); PTS is in
microseconds (`pts_us`). Session metadata includes UTC, PID, Qt/platform/screen,
refresh rate and selected Rust executable path/build mtime.

- `command`: opens, plays, pauses, seeks, steps, closes, and active source/filter/
  preview bounds. Source changes advance `generation`; a seek is intentional.
- `received`: full frame payload arrived from Rust; includes read duration,
  dimensions, byte count, source PTS and Rust scheduling lateness (`engine_late_us`).
- `ui_dispatch`: time the complete payload waited for the Qt thread (`queue_ms`).
- `submitted`: QImage copy, metadata work, QPixmap/scaling and total callback
  times; visibility, minimization, interval and generation identify context.
- `painted`: QLabel `paintEvent` completed with this frame; reports wait since
  submission, paint duration, and interval since the previous distinct frame paint.
- `replaced_before_paint`: another frame replaced a pending, unpainted frame.
  Compare generations and commands: intentional seeking/reopening and hidden
  windows must not be counted as decoder frame drops.
- `pending_discarded`: a source was stopped with a frame still pending paint.
- `stale_frame`: discarded obsolete generation, normally expected after seeking.
- `summary`: received/submitted/painted/replaced totals, UI timer stalls, audio
  position/state, and lost log records, once per second and on shutdown.

A `painted` event proves completion of Qt widget painting, **not** physical
presentation by the compositor/monitor. If paint timing is smooth but motion
still skips, compositor/display scheduling remains a possible boundary.
No `repaint()`, screenshot capture, video hashing, audio resync or changes to
frame acknowledgements are introduced by this trace.
