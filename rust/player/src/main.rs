mod decode;
use decode::{DecodeTask, Decoded, Frame, Source};
use serde::Deserialize;
use serde_json::json;
use std::collections::VecDeque;
use std::io::{self, BufRead, Read, Write};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

#[derive(Deserialize)]
struct Command {
    command: String,
    #[serde(default)] generation: u64,
    source: Option<Source>,
    #[serde(default)] position_us: i64,
    #[serde(default)] index: usize,
    #[serde(default)] delta: i32,
}
struct Player {
    source: Option<Source>, task: Option<DecodeTask>, next: Option<Frame>,
    generation: u64, playing: bool, awaiting_ack: bool, initial: bool, ended: bool,
    position: i64, index: usize, anchor: Option<(Instant, i64)>,
    scrub_target: Option<i64>, scrub_cache: VecDeque<Frame>, scrub_bytes: usize,
    decode_cursor: i64,
}
impl Player {
    fn new() -> Self { Self { source: None, task: None, next: None, generation: 0, playing: false, awaiting_ack: false, initial: false, ended: false, position: 0, index: 0, anchor: None, scrub_target: None, scrub_cache: VecDeque::new(), scrub_bytes: 0, decode_cursor: 0 } }
    fn clear_scrub(&mut self) { self.scrub_target = None; self.scrub_cache.clear(); self.scrub_bytes = 0; }
    fn cache_scrub(&mut self, frame: &Frame) {
        const BUDGET: usize = 64 * 1024 * 1024;
        while self.scrub_bytes + frame.pixels.len() > BUDGET || self.scrub_cache.len() >= 128 {
            if let Some(old) = self.scrub_cache.pop_front() { self.scrub_bytes -= old.pixels.len(); } else { break; }
        }
        self.scrub_bytes += frame.pixels.len();
        self.scrub_cache.push_back(frame.clone());
    }
    fn state(&self, out: &mut impl Write, state: &str) -> io::Result<()> { event(out, json!({"event":"state", "generation":self.generation, "state":state})) }
    fn restart(&mut self, position: i64, index: usize, step: i32) {
        self.task = None;
        self.next = None;
        self.anchor = None;
        self.awaiting_ack = false;
        self.initial = true;
        self.ended = false;
        self.decode_cursor = position;
        if let Some(source) = &self.source { self.task = Some(DecodeTask::start(source.clone(), position, index, step)); }
    }
    fn command(&mut self, cmd: Command, out: &mut impl Write) -> io::Result<bool> {
        if cmd.command == "quit" { return Ok(false); }
        if matches!(cmd.command.as_str(), "open" | "seek" | "scrub" | "step" | "close") {
            if cmd.generation < self.generation { return Ok(true); }
            self.generation = cmd.generation;
        } else if cmd.generation != self.generation { return Ok(true); }
        if matches!(cmd.command.as_str(), "open" | "seek" | "step" | "close" | "play") { self.clear_scrub(); }
        match cmd.command.as_str() {
            "open" => {
                self.task = None; self.next = None; self.source = None; self.playing = false;
                self.position = 0; self.index = 0;
                let result = cmd.source.ok_or_else(|| "Open requires a source".to_string()).and_then(|source| { source.validate()?; Ok(source) });
                match result {
                    Ok(source) => { self.source = Some(source); self.restart(cmd.position_us, cmd.index, 0); self.state(out, "paused")?; }
                    Err(message) => event(out, json!({"event":"error", "generation":self.generation, "message":message}))?,
                }
            }
            "play" if self.source.is_some() => {
                if self.ended { self.restart(0, 0, 0); }
                self.playing = true;
                self.anchor = if self.initial { None } else { Some((Instant::now(), self.position)) };
                self.state(out, "playing")?;
            }
            "pause" => { self.playing = false; self.anchor = None; self.state(out, "paused")?; }
            "seek" => { self.restart(cmd.position_us, cmd.index, 0); }
            "scrub" => {
                self.playing = false;
                let target = cmd.position_us.max(0);
                let video = self.source.as_ref().is_some_and(|s| s.files.is_empty());
                // Drag previews may use a nearby cached frame; release uses exact seek.
                let cached = if video { self.scrub_cache.iter().min_by_key(|f| f.pts.abs_diff(target))
                    .filter(|f| f.pts.abs_diff(target) <= 40_000).cloned() } else { None };
                if let Some(frame) = cached {
                    self.next = Some(frame); self.scrub_target = None;
                } else if video && self.task.is_some() && target >= self.decode_cursor && target - self.decode_cursor <= 1_000_000 {
                    self.scrub_target = Some(target);
                } else {
                    self.restart(target, cmd.index, 0);
                    self.scrub_target = if video { Some(target) } else { None };
                }
                self.initial = true; self.awaiting_ack = false; self.anchor = None;
            }
            "step" => { self.playing = false; self.restart(self.position, self.index, cmd.delta.clamp(-100, 100)); self.state(out, "paused")?; }
            "ack" => { self.awaiting_ack = false; }
            "close" => { self.task = None; self.source = None; self.next = None; self.playing = false; self.state(out, "paused")?; }
            _ => {}
        }
        Ok(true)
    }
    fn tick(&mut self, out: &mut impl Write) -> io::Result<()> {
        if self.awaiting_ack || (!self.initial && !self.playing) { return Ok(()); }
        if self.next.is_none() {
            if let Some(task) = &self.task {
                match task.rx.try_recv() {
                    Ok(Decoded::Frame(frame)) => {
                        self.decode_cursor = frame.pts;
                        if self.scrub_target.is_some() { self.cache_scrub(&frame); }
                        self.next = Some(frame);
                    }
                    Ok(Decoded::End) => { self.playing = false; self.ended = true; self.initial = false; self.task = None; self.state(out, "ended")?; return Ok(()); }
                    Ok(Decoded::Error(message)) => { self.playing = false; self.initial = false; self.task = None; event(out, json!({"event":"error", "generation":self.generation, "message":message}))?; return Ok(()); }
                    Err(_) => return Ok(()),
                }
            }
        }
        if let (Some(target), Some(frame)) = (self.scrub_target, self.next.as_ref()) {
            if frame.pts < target { self.next = None; return Ok(()); }
        }
        if let Some(frame) = &self.next {
            let (started, media_start) = *self.anchor.get_or_insert((Instant::now(), frame.pts));
            let due = Duration::from_micros(frame.pts.saturating_sub(media_start).max(0) as u64);
            if !self.initial && started.elapsed() < due { return Ok(()); }
        }
        if let Some(frame) = self.next.take() {
            let late_us = self.anchor.map(|(started, media_start)| {
                let elapsed = started.elapsed().as_micros().min(i64::MAX as u128) as i64;
                elapsed.saturating_sub(frame.pts.saturating_sub(media_start).max(0)).max(0)
            }).unwrap_or(0);
            self.position = frame.pts; self.index = frame.index;
            event(out, json!({"event":"frame", "generation":self.generation, "position_us":frame.pts, "index":frame.index, "width":frame.width, "height":frame.height, "source_width":frame.source_width, "source_height":frame.source_height, "bytes":frame.pixels.len(), "late_us":late_us}))?;
            out.write_all(&frame.pixels)?; out.flush()?;
            self.awaiting_ack = true; self.initial = false;
        }
        Ok(())
    }
}
fn event(out: &mut impl Write, value: serde_json::Value) -> io::Result<()> { serde_json::to_writer(&mut *out, &value)?; out.write_all(b"\n")?; out.flush() }
fn main() -> io::Result<()> {
    let (tx, rx) = mpsc::sync_channel(64);
    thread::spawn(move || {
        let stdin = io::stdin();
        let mut reader = stdin.lock();
        loop {
            let mut line = Vec::new();
            // Bounded JSON control messages, even for a corrupt client.
            match reader.by_ref().take(16 * 1024 * 1024 + 1).read_until(b'\n', &mut line) {
                Ok(0) | Err(_) => break,
                Ok(_) if line.len() > 16 * 1024 * 1024 => break,
                Ok(_) => { if tx.send(serde_json::from_slice::<Command>(&line)).is_err() { break; } }
            }
        }
    });
    let mut player = Player::new();
    let stdout = io::stdout(); let mut out = stdout.lock();
    loop {
        match rx.recv_timeout(Duration::from_millis(2)) {
            Ok(Ok(command)) => { if !player.command(command, &mut out)? { break; } }
            Ok(Err(error)) => event(&mut out, json!({"event":"error", "generation":player.generation, "message":format!("Invalid command: {error}")}))?,
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
        player.tick(&mut out)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scrub_cache_bounds_bytes_and_frame_count_and_releases() {
        let mut player = Player::new();
        let mut frame = Frame { source_width: 2048, source_height: 1024, width: 2048,
            height: 1024, pixels: vec![0; 8 * 1024 * 1024], pts: 0, index: 0 };
        for index in 0..10 {
            frame.pts = index * 40_000;
            player.cache_scrub(&frame);
            assert!(player.scrub_bytes <= 64 * 1024 * 1024);
        }
        assert_eq!(player.scrub_cache.len(), 8);
        player.clear_scrub();
        assert_eq!(player.scrub_bytes, 0);
        assert!(player.scrub_cache.is_empty());
        frame.pixels = vec![0; 4];
        for _ in 0..140 { player.cache_scrub(&frame); }
        assert_eq!(player.scrub_cache.len(), 128);
    }
}
