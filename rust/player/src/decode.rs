use serde::Deserialize;
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}, mpsc::{self, Receiver, SyncSender}};
use std::thread;
use std::time::Duration;

pub const MAX_FRAME_BYTES: usize = 64 * 1024 * 1024;
#[derive(Clone, Deserialize)]
pub struct Source {
    pub path: String,
    #[serde(default)] pub files: Vec<String>,
    pub fps_num: u32,
    pub fps_den: u32,
    #[serde(default)] pub filter: String,
    #[serde(default)] pub max_width: u32,
    #[serde(default)] pub max_height: u32,
}
impl Source {
    pub fn validate(&self) -> Result<(), String> {
        if (self.max_width == 0) != (self.max_height == 0) || self.max_width > 8192 || self.max_height > 8192 {
            return Err("Preview bounds must both be zero or between 1 and 8192".into());
        }
        if self.fps_num == 0 || self.fps_den == 0 { return Err("FPS must be positive".into()); }
        if self.path.is_empty() { return Err("Empty source path".into()); }
        if self.files.len() > 100_000 { return Err("Sequence exceeds 100000 files".into()); }
        for path in std::iter::once(&self.path).chain(self.files.iter()) {
            if path.contains(['\n', '\r', '\0']) { return Err("Media paths cannot contain newlines or NUL".into()); }
            if !std::path::Path::new(path).is_absolute() { return Err("Media paths must be absolute".into()); }
        }
        Ok(())
    }
    pub fn timestamp(&self, index: usize) -> i64 {
        ((index as u128 * 1_000_000 * self.fps_den as u128) / self.fps_num as u128).min(i64::MAX as u128) as i64
    }
}
#[derive(Clone)]
pub struct Frame { pub source_width: u32, pub source_height: u32, pub width: u32, pub height: u32, pub pixels: Vec<u8>, pub pts: i64, pub index: usize }
pub enum Decoded { Frame(Frame), End, Error(String) }
struct Control { cancelled: AtomicBool, child: Mutex<Option<Child>> }
pub struct DecodeTask { control: Arc<Control>, pub rx: Receiver<Decoded>, worker: Option<thread::JoinHandle<()>> }
impl DecodeTask {
    pub fn start(source: Source, position: i64, index: usize, step: i32) -> Self {
        let control = Arc::new(Control { cancelled: AtomicBool::new(false), child: Mutex::new(None) });
        let (tx, rx) = mpsc::sync_channel(1);
        let shared = control.clone();
        let worker = thread::spawn(move || {
            let result = decode(&source, position, index, step, &shared, &tx);
            let event = match result { Ok(()) => Decoded::End, Err(e) => Decoded::Error(e) };
            send(&tx, event, &shared);
            reap(&shared);
        });
        Self { control, rx, worker: Some(worker) }
    }
    pub fn cancel(&mut self) {
        self.control.cancelled.store(true, Ordering::Release);
        if let Ok(mut child) = self.control.child.lock() { if let Some(child) = child.as_mut() { let _ = child.kill(); } }
        if let Some(worker) = self.worker.take() { let _ = worker.join(); }
    }
}
impl Drop for DecodeTask { fn drop(&mut self) { self.cancel(); } }
fn send<T>(tx: &SyncSender<T>, mut event: T, control: &Control) -> bool {
    while !control.cancelled.load(Ordering::Acquire) {
        match tx.try_send(event) {
            Ok(()) => return true,
            Err(mpsc::TrySendError::Full(value)) => { event = value; thread::sleep(Duration::from_millis(2)); }
            Err(mpsc::TrySendError::Disconnected(_)) => return false,
        }
    }
    false
}
fn install(control: &Control, mut child: Child) -> Result<(), String> {
    let mut slot = control.child.lock().map_err(|_| "Decoder process lock failed")?;
    if control.cancelled.load(Ordering::Acquire) { let _ = child.kill(); let _ = child.wait(); return Err("Cancelled".into()); }
    *slot = Some(child); Ok(())
}
fn reap(control: &Control) {
    if let Ok(mut slot) = control.child.lock() {
        if let Some(mut child) = slot.take() { let _ = child.kill(); let _ = child.wait(); }
    }
}
fn preceding_timestamp(source: &Source, position: i64, count: usize, control: &Control) -> Result<i64, String> {
    let mut child = Command::new("ffprobe").args(["-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", "-i", &source.path])
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().map_err(|e| format!("Backward step: ffprobe: {e}"))?;
    let stdout = child.stdout.take().ok_or("ffprobe stdout missing")?;
    install(control, child)?;
    let mut tail = VecDeque::new();
    let mut origin = None;
    for line in BufReader::new(stdout).lines() {
        if control.cancelled.load(Ordering::Acquire) { return Err("Cancelled".into()); }
        let line = line.map_err(|e| format!("ffprobe timestamps: {e}"))?;
        let Some(pts) = line.split(',').next().and_then(parse_seconds) else { continue };
        let start = *origin.get_or_insert(pts);
        let pts = pts - start;
        if pts >= position - 2 { break; }
        tail.push_back(pts);
        if tail.len() > count { tail.pop_front(); }
    }
    reap(control);
    Ok(tail.front().copied().unwrap_or(0))
}
fn decode(source: &Source, position: i64, index: usize, step: i32, control: &Control, tx: &SyncSender<Decoded>) -> Result<(), String> {
    let sequence = !source.files.is_empty();
    let index = if sequence { (index.min(source.files.len() - 1) as i64 + step as i64).clamp(0, source.files.len() as i64 - 1) as usize } else { 0 };
    let position = if sequence { source.timestamp(index) } else if step < 0 { preceding_timestamp(source, position, step.unsigned_abs() as usize, control)? } else { position.max(0) };
    let mut command = Command::new("ffmpeg");
    let threads = thread::available_parallelism().map(|n| n.get().clamp(1, 8)).unwrap_or(2).to_string();
    command.args(["-hide_banner", "-nostdin", "-loglevel", "info", "-threads", &threads, "-filter_threads", &threads]);
    let manifest = if sequence {
        command.args(["-protocol_whitelist", "file,pipe", "-f", "concat", "-safe", "0", "-i", "pipe:0"]);
        Some(sequence_manifest(source, index))
    } else {
        command.args(["-ss", &format!("{:.6}", position as f64 / 1_000_000.0), "-i", &source.path]); None
    };
    // Capture source geometry before display-only scaling. Export never uses this buffer.
    let mut filters = vec!["showinfo=checksum=0".to_string()];
    if source.max_width > 0 {
        filters.push(format!("scale=w='min(iw,{})':h='min(ih,{})':force_original_aspect_ratio=decrease", source.max_width, source.max_height));
    }
    // Display transforms run at display resolution, on paused and playing frames alike.
    // Keep float RGB/straight alpha through color math; quantize only at the IPC boundary.
    if !source.filter.is_empty() {
        filters.push("format=gbrapf32le".into());
        filters.push(source.filter.clone());
    }
    filters.push("format=rgba".into());
    let filter = filters.join(",");
    command.args(["-map", "0:v:0", "-an", "-sn", "-dn", "-vf", &filter, "-fps_mode", "passthrough", "-c:v", "pam", "-threads", "1", "-pix_fmt", "rgba", "-f", "image2pipe", "pipe:1"]);
    let mut child = command.stdin(if sequence { Stdio::piped() } else { Stdio::null() }).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().map_err(|e| format!("Start FFmpeg: {e}"))?;
    let stdout = child.stdout.take().ok_or("FFmpeg stdout missing")?;
    let stderr = child.stderr.take().ok_or("FFmpeg stderr missing")?;
    let stdin = child.stdin.take();
    install(control, child)?;
    let result = thread::scope(|scope| -> Result<(), String> {
        if let (Some(mut stdin), Some(manifest)) = (stdin, manifest) { scope.spawn(move || { let _ = stdin.write_all(manifest.as_bytes()); }); }
        let (pts_tx, pts_rx) = mpsc::sync_channel(4);
        let diagnostics = Arc::new(Mutex::new(VecDeque::<String>::new()));
        let log = diagnostics.clone();
        scope.spawn(move || {
            let mut time_base = None;
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                if line.contains("showinfo") && line.contains("config in time_base:") {
                    time_base = line.split("time_base:").nth(1).and_then(|v| v.trim().split(',').next()).and_then(|v| v.split_once('/')).and_then(|(n,d)| Some((n.parse::<i64>().ok()?, d.parse::<i64>().ok()?)));
                }
                if let Some(pts) = showinfo_pts(&line, time_base) {
                    let size = line.split(" s:").nth(1).and_then(|v| v.split_whitespace().next()).and_then(|v| v.split_once('x')).and_then(|(w,h)| Some((w.parse::<u32>().ok()?, h.parse::<u32>().ok()?)));
                    if !send(&pts_tx, (pts, size), control) { break; }
                } else if let Ok(mut log) = log.lock() {
                    if log.len() == 12 { log.pop_front(); }
                    log.push_back(line.chars().take(1000).collect());
                }
            }
        });
        let mut reader = BufReader::new(stdout);
        let mut frame_index = index;
        let mut skipped = 0;
        let mut seen = false;
        let mut last_skipped = None;
        let result = (|| {
            while let Some((width, height, pixels)) = read_pam(&mut reader)? {
                let (relative, source_size) = pts_rx.recv().map_err(|_| "FFmpeg produced a frame without a timestamp")?;
                let (source_width, source_height) = source_size.ok_or("FFmpeg timestamp missing source dimensions")?;
                let pts = if sequence { source.timestamp(frame_index) } else { position.saturating_add(relative) };
                if !sequence && step > 0 && skipped < step {
                    skipped += 1;
                    last_skipped = Some(Frame { source_width, source_height, width, height, pixels, pts, index: frame_index });
                    continue;
                }
                last_skipped = None;
                seen = true;
                if !send(tx, Decoded::Frame(Frame { source_width, source_height, width, height, pixels, pts, index: frame_index }), control) { break; }
                frame_index += 1;
            }
            if !seen {
                if let Some(frame) = last_skipped.take() { seen = true; send(tx, Decoded::Frame(frame), control); }
            }
            Ok::<(), String>(())
        })();
        // Release both pipes on every exit; cancellation also releases a stderr sender.
        drop(pts_rx);
        if result.is_err() || control.cancelled.load(Ordering::Acquire) { reap(control); }
        result?;
        if !seen && !control.cancelled.load(Ordering::Acquire) {
            let detail = diagnostics.lock().map(|log| log.iter().cloned().collect::<Vec<_>>().join("\n")).unwrap_or_default();
            return Err(format!("Decode {}: no frames\n{detail}", source.path));
        }
        // Wait for process status after stdout EOF; stderr is drained concurrently.
        if let Ok(mut slot) = control.child.lock() {
            if let Some(mut child) = slot.take() {
                let status = child.wait().map_err(|e| format!("FFmpeg wait: {e}"))?;
                if !status.success() && !control.cancelled.load(Ordering::Acquire) {
                    let detail = diagnostics.lock().map(|log| log.iter().cloned().collect::<Vec<_>>().join("\n")).unwrap_or_default();
                    return Err(format!("Decode {}: FFmpeg {status}\n{detail}", source.path));
                }
            }
        }
        Ok(())
    });
    if !sequence && position > 0 && !control.cancelled.load(Ordering::Acquire)
        && result.as_ref().is_err_and(|message| message.contains(": no frames")) {
        reap(control);
        let last = preceding_timestamp(source, position, 1, control)?;
        if last < position { return decode(source, last, 0, 0, control, tx); }
    }
    result
}
pub fn sequence_manifest(source: &Source, start: usize) -> String {
    let mut text = String::from("ffconcat version 1.0\n");
    for path in &source.files[start..] {
        // FFconcat single quotes, with a backslash-escaped quote outside the quoted segment.
        let path = if cfg!(windows) { path.replace('\\', "/") } else { path.clone() };
        let path = path.replace('\'', "'\\''");
        text.push_str(&format!("file 'file:{path}'\noption framerate {}/{}\nduration {:.9}\n", source.fps_num, source.fps_den, source.fps_den as f64 / source.fps_num as f64));
    }
    text
}
pub fn parse_seconds(value: &str) -> Option<i64> {
    let value: f64 = value.parse().ok()?;
    (value.is_finite() && value.abs() < i64::MAX as f64 / 1_000_000.0).then(|| (value * 1_000_000.0).round() as i64)
}
fn showinfo_pts(line: &str, time_base: Option<(i64, i64)>) -> Option<i64> {
    if !line.contains("showinfo") || !line.contains(" n:") { return None; }
    if let Some((num, den)) = time_base.filter(|(n,d)| *n > 0 && *d > 0) {
        let pts: i128 = line.split(" pts:").nth(1)?.split_whitespace().next()?.parse().ok()?;
        return i64::try_from(pts * num as i128 * 1_000_000 / den as i128).ok();
    }
    parse_seconds(line.split("pts_time:").nth(1)?.split_whitespace().next()?)
}
type Pam = (u32, u32, Vec<u8>);
fn read_pam(reader: &mut impl BufRead) -> Result<Option<Pam>, String> {
    let mut line = String::new();
    if reader.read_line(&mut line).map_err(|e| e.to_string())? == 0 { return Ok(None); }
    if line.trim() != "P7" { return Err("Invalid FFmpeg PAM frame header".into()); }
    let (mut width, mut height, mut depth, mut maxval) = (0u32, 0u32, 0u32, 0u32);
    let mut ended = false;
    for _ in 0..16 {
        line.clear(); reader.read_line(&mut line).map_err(|e| e.to_string())?;
        let mut fields = line.split_whitespace();
        let key = fields.next().unwrap_or_default();
        if key == "ENDHDR" { ended = true; break; }
        let value = fields.next().and_then(|v| v.parse().ok()).unwrap_or(0);
        match key { "WIDTH" => width = value, "HEIGHT" => height = value, "DEPTH" => depth = value, "MAXVAL" => maxval = value, _ => {} }
    }
    let length = (width as usize).checked_mul(height as usize).and_then(|n| n.checked_mul(4)).ok_or("Frame dimensions overflow")?;
    if !ended || depth != 4 || maxval != 255 || length == 0 || length > MAX_FRAME_BYTES { return Err("Invalid or oversized RGBA8 frame (64 MiB maximum)".into()); }
    let mut pixels = vec![0; length]; reader.read_exact(&mut pixels).map_err(|e| format!("Truncated frame: {e}"))?;
    Ok(Some((width, height, pixels)))
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn pam_rejects_oversized_and_truncated() {
        assert!(read_pam(&mut &b"P7\nWIDTH 999999\nHEIGHT 999999\nDEPTH 4\nMAXVAL 255\nENDHDR\n"[..]).is_err());
        assert!(read_pam(&mut &b"P7\nWIDTH 1\nHEIGHT 1\nDEPTH 4\nMAXVAL 255\nENDHDR\nxx"[..]).is_err());
    }
    #[test] fn rational_clock_does_not_accumulate_rounding() {
        let source = Source { path: "/tmp/a".into(), files: vec![], fps_num: 24000, fps_den: 1001, filter: String::new(), max_width: 0, max_height: 0 };
        assert_eq!(source.timestamp(24000), 1_001_000_000);
    }
    #[test] fn parse_real_showinfo() { assert_eq!(showinfo_pts("[Parsed_showinfo_1] n:   2 pts: 42 pts_time:0.0417083 duration:1", None), Some(41708)); }
}
