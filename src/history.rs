//! The clipboard archive, compatible byte-for-byte with the Python app (0.7.x):
//! `{"version": 1, "clips": [{"id", "text", "created_at"}, ...]}`, newest
//! first, written atomically (private temp file, fsync, rename) into a 0700
//! directory, saved on a background thread that coalesces bursts.

use std::collections::{HashMap, HashSet};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::time::SystemTime;

use crate::pyjson;

#[derive(Clone, Debug, PartialEq)]
pub struct Clip {
    pub id: String,
    pub text: String,
    pub created_at: f64,
}

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64())
}

fn random_bytes<const N: usize>() -> [u8; N] {
    let mut bytes = [0u8; N];
    if let Ok(mut file) = File::open("/dev/urandom") {
        let _ = std::io::Read::read_exact(&mut file, &mut bytes);
    }
    bytes
}

/// `uuid.uuid4().hex`.
pub fn new_id() -> String {
    let mut b: [u8; 16] = random_bytes();
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// Decode the whole archive or fail (as the Python app did); never partial.
pub fn decode(raw: &[u8]) -> Result<Vec<Clip>, String> {
    let text = std::str::from_utf8(raw).map_err(|e| format!("invalid UTF-8: {e}"))?;
    let data: serde_json::Value = serde_json::from_str(text).map_err(|e| e.to_string())?;
    let format_error = || "unrecognized history format".to_string();
    let object = data.as_object().ok_or_else(format_error)?;
    // `type(version) is int and version == 1` (so not 1.0 or true).
    if !object
        .get("version")
        .is_some_and(|v| v.is_i64() && v.as_i64() == Some(1))
    {
        return Err(format_error());
    }
    let entries = object
        .get("clips")
        .and_then(|c| c.as_array())
        .ok_or_else(format_error)?;
    let mut ids = HashSet::new();
    let mut texts = HashSet::new();
    let mut clips = Vec::with_capacity(entries.len());
    for entry in entries {
        let entry = entry.as_object().ok_or("invalid clipboard entry")?;
        let invalid = || "invalid or duplicate clipboard entry".to_string();
        let id = entry
            .get("id")
            .and_then(|v| v.as_str())
            .ok_or_else(invalid)?;
        let text = entry
            .get("text")
            .and_then(|v| v.as_str())
            .ok_or_else(invalid)?;
        let created_at = entry
            .get("created_at")
            .filter(|v| v.is_number())
            .ok_or_else(invalid)?;
        if id.trim().is_empty()
            || ids.contains(id)
            || text.trim().is_empty()
            || texts.contains(text)
        {
            return Err(invalid());
        }
        let created_at = created_at
            .as_f64()
            .filter(|t| t.is_finite())
            .ok_or("invalid clipboard timestamp")?;
        ids.insert(id.to_owned());
        texts.insert(text.to_owned());
        clips.push(Clip {
            id: id.to_owned(),
            text: text.to_owned(),
            created_at,
        });
    }
    Ok(clips)
}

/// `json.dumps({"version": 1, "clips": [...]}, ensure_ascii=False, indent=2) + "\n"`.
pub fn encode(clips: &[Clip]) -> String {
    let mut out = String::from("{\n  \"version\": 1,\n  \"clips\": [");
    if clips.is_empty() {
        out.push_str("]\n}\n");
        return out;
    }
    for (index, clip) in clips.iter().enumerate() {
        out.push_str(if index == 0 { "\n" } else { ",\n" });
        out.push_str("    {\n      \"id\": ");
        out.push_str(&pyjson::string(&clip.id));
        out.push_str(",\n      \"text\": ");
        out.push_str(&pyjson::string(&clip.text));
        out.push_str(",\n      \"created_at\": ");
        out.push_str(&pyjson::float(clip.created_at));
        out.push_str("\n    }");
    }
    out.push_str("\n  ]\n}\n");
    out
}

fn prepare_parent(path: &Path, private: bool) -> std::io::Result<()> {
    let parent = path.parent().unwrap_or(Path::new("."));
    std::fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(parent)?;
    if private {
        std::fs::set_permissions(parent, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

/// Python's `tempfile.mkstemp` + fsync + `os.replace`, file mode 0600.
pub fn atomic_write(path: &Path, content: &[u8], private_parent: bool) -> std::io::Result<()> {
    prepare_parent(path, private_parent)?;
    let parent = path.parent().unwrap_or(Path::new("."));
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let (temporary, mut file) = loop {
        let suffix: String = random_bytes::<6>()
            .iter()
            .map(|b| (b'a' + b % 26) as char)
            .collect();
        let candidate = parent.join(format!(".{name}-{suffix}.tmp"));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&candidate)
        {
            Ok(file) => break (candidate, file),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    };
    let result = (|| {
        file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
        file.write_all(content)?;
        file.flush()?;
        file.sync_all()?;
        std::fs::rename(&temporary, path)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&temporary);
    }
    result
}

type ErrorCallback = Box<dyn Fn(String) + Send + Sync>;

#[derive(Default)]
struct WriterState {
    pending: Option<(PathBuf, Vec<Clip>)>,
    active: bool,
    closed: bool,
    error: Option<String>,
}

/// One ordered writer with at most one waiting snapshot.
struct Writer {
    shared: Arc<(Mutex<WriterState>, Condvar)>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl Writer {
    fn new(on_error: ErrorCallback) -> Self {
        let shared = Arc::new((Mutex::new(WriterState::default()), Condvar::new()));
        let worker = shared.clone();
        let thread = std::thread::Builder::new()
            .name("jumpkut-archive".into())
            .spawn(move || {
                let (lock, condvar) = &*worker;
                loop {
                    let (path, items) = {
                        let mut state = lock.lock().unwrap();
                        while state.pending.is_none() && !state.closed {
                            state = condvar.wait(state).unwrap();
                        }
                        let Some(job) = state.pending.take() else {
                            return;
                        };
                        state.active = true;
                        job
                    };
                    let result = atomic_write(&path, encode(&items).as_bytes(), true)
                        .map_err(|e| e.to_string());
                    if let Err(e) = &result {
                        on_error(e.clone());
                    }
                    let mut state = lock.lock().unwrap();
                    state.error = result.err();
                    state.active = false;
                    condvar.notify_all();
                }
            })
            .expect("spawn archive writer");
        Writer {
            shared,
            thread: Some(thread),
        }
    }

    fn submit(&self, path: PathBuf, items: Vec<Clip>) {
        let (lock, condvar) = &*self.shared;
        lock.lock().unwrap().pending = Some((path, items));
        condvar.notify_all();
    }

    fn close(&mut self) -> Result<(), String> {
        {
            let (lock, condvar) = &*self.shared;
            lock.lock().unwrap().closed = true;
            condvar.notify_all();
        }
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
        let state = self.shared.0.lock().unwrap();
        state.error.clone().map_or(Ok(()), Err)
    }
}

pub struct History {
    path: Option<PathBuf>,
    items: Vec<Clip>,
    pub load_error: Option<String>,
    unsafe_to_overwrite: bool,
    writer: Option<Writer>,
}

impl History {
    /// `path=None` keeps history in memory only. With `on_error`, saves run
    /// on a background thread and report failures through it.
    pub fn open(path: Option<PathBuf>, on_error: Option<ErrorCallback>) -> Self {
        let mut history = History {
            path: path.clone(),
            items: Vec::new(),
            load_error: None,
            unsafe_to_overwrite: false,
            writer: None,
        };
        if let Some(path) = &path {
            history.load(path);
            if let Some(on_error) = on_error {
                history.writer = Some(Writer::new(on_error));
            }
        }
        history
    }

    fn load(&mut self, path: &Path) {
        let raw = match std::fs::read(path) {
            Ok(raw) => raw,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return,
            Err(e) => {
                self.load_error = Some(format!("Could not read clipboard history: {e}"));
                self.unsafe_to_overwrite = true;
                return;
            }
        };
        match decode(&raw) {
            Ok(items) => self.items = items,
            Err(e) => {
                let mut message = format!("Invalid clipboard history: {e}");
                match preserve_corrupt(path, &raw) {
                    Ok(backup) => {
                        message += &format!("; original preserved at {}", backup.display())
                    }
                    Err(err) => {
                        message += &format!("; could not preserve original: {err}");
                        self.unsafe_to_overwrite = true;
                    }
                }
                self.load_error = Some(message);
            }
        }
    }

    pub fn items(&self) -> &[Clip] {
        &self.items
    }

    pub fn len(&self) -> usize {
        self.items.len()
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    pub fn recent(&self, limit: usize) -> &[Clip] {
        &self.items[..limit.min(self.items.len())]
    }

    pub fn get(&self, id: &str) -> Option<&Clip> {
        self.items.iter().find(|c| c.id == id)
    }

    /// Record a copy: blank text is ignored; a repeated text keeps its id,
    /// gets a new timestamp and moves to the front. The memory copy is always
    /// updated; `Err` reports a save failure.
    pub fn add(&mut self, text: &str) -> Result<Option<Clip>, String> {
        if text.trim().is_empty() {
            return Ok(None);
        }
        let id = self
            .items
            .iter()
            .find(|c| c.text == text)
            .map_or_else(new_id, |c| c.id.clone());
        let clip = Clip {
            id,
            text: text.to_owned(),
            created_at: now(),
        };
        self.items.retain(|c| c.text != text);
        self.items.insert(0, clip.clone());
        self.save().map(|()| Some(clip))
    }

    pub fn remove(&mut self, id: &str) -> Result<(), String> {
        let before = self.items.len();
        self.items.retain(|c| c.id != id);
        if self.items.len() != before {
            self.save()
        } else {
            Ok(())
        }
    }

    pub fn clear(&mut self) -> Result<(), String> {
        self.items.clear();
        self.save()
    }

    /// A portable backup; does not change an existing directory's mode.
    pub fn export_to(&self, path: &Path) -> Result<(), String> {
        atomic_write(path, encode(&self.items).as_bytes(), false).map_err(|e| e.to_string())
    }

    /// Merge a backup; returns how many new texts were added. The whole file
    /// is validated before history changes. Existing texts keep their local id
    /// and take the newer timestamp; colliding foreign ids get fresh ones.
    pub fn import_from(&mut self, path: &Path) -> Result<usize, String> {
        let raw = std::fs::read(path).map_err(|e| e.to_string())?;
        let imported = decode(&raw)?;
        let (count, merged) = merge(&self.items, imported, new_id);
        self.items = merged;
        self.save().map(|()| count)
    }

    fn save(&mut self) -> Result<(), String> {
        let Some(path) = self.path.clone() else {
            return Ok(());
        };
        if self.unsafe_to_overwrite {
            return Err(
                "Clipboard history could not be read or backed up; original will be preserved"
                    .into(),
            );
        }
        match &self.writer {
            Some(writer) => {
                writer.submit(path, self.items.clone());
                Ok(())
            }
            None => {
                atomic_write(&path, encode(&self.items).as_bytes(), true).map_err(|e| e.to_string())
            }
        }
    }

    /// Replace the in-memory list without saving (demo mode).
    pub fn replace_all(&mut self, items: Vec<Clip>) {
        self.items = items;
    }

    pub fn close(&mut self) -> Result<(), String> {
        self.writer.as_mut().map_or(Ok(()), Writer::close)
    }
}

/// The backup merge rule (as in the Python app), with an injectable id source.
pub fn merge(
    current: &[Clip],
    imported: Vec<Clip>,
    mut fresh_id: impl FnMut() -> String,
) -> (usize, Vec<Clip>) {
    let existing: HashSet<&str> = current.iter().map(|c| c.text.as_str()).collect();
    let mut order: Vec<String> = Vec::new();
    let mut merged: HashMap<String, Clip> = HashMap::new();
    for clip in current {
        order.push(clip.text.clone());
        merged.insert(clip.text.clone(), clip.clone());
    }
    let mut used: HashSet<String> = current.iter().map(|c| c.id.clone()).collect();
    for clip in imported {
        if let Some(previous) = merged.get_mut(&clip.text) {
            if clip.created_at > previous.created_at {
                previous.created_at = clip.created_at;
            }
        } else {
            let mut id = clip.id.clone();
            while used.contains(&id) {
                id = fresh_id();
            }
            used.insert(id.clone());
            order.push(clip.text.clone());
            merged.insert(clip.text.clone(), Clip { id, ..clip });
        }
    }
    let mut items: Vec<Clip> = order.iter().filter_map(|t| merged.remove(t)).collect();
    // Python's sorted(..., reverse=True) is stable for equal timestamps.
    items.sort_by(|a, b| {
        b.created_at
            .partial_cmp(&a.created_at)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let count = items
        .iter()
        .filter(|c| !existing.contains(c.text.as_str()))
        .count();
    (count, items)
}

fn preserve_corrupt(path: &Path, raw: &[u8]) -> std::io::Result<PathBuf> {
    prepare_parent(path, true)?;
    let nanos = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos());
    let name = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    let backup = path.with_file_name(format!("{name}.corrupt-{nanos}"));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&backup)?;
    let result = file.write_all(raw).and_then(|()| file.sync_all());
    if result.is_err() {
        let _ = std::fs::remove_file(&backup);
    }
    result.map(|()| backup)
}

/// "just now", "5 min ago", "3 h ago", "yesterday", "2 days ago".
pub fn relative_age(created_at: f64, now: f64) -> String {
    match (now - created_at).max(0.0) {
        s if s < 45.0 => "just now".into(),
        s if s < 3_600.0 => format!("{} min ago", ((s / 60.0).round() as u64).max(1)),
        s if s < 86_400.0 => format!("{} h ago", (s / 3_600.0).round() as u64),
        s if s < 2.0 * 86_400.0 => "yesterday".into(),
        s => format!("{} days ago", (s / 86_400.0).floor() as u64),
    }
}

/// A bounded multi-line preview for the popup; copying uses the full text.
pub fn preview(text: &str, max_lines: usize, max_line_chars: usize) -> String {
    let mut out = String::new();
    let mut lines = text.split('\n');
    for (index, line) in lines.by_ref().take(max_lines).enumerate() {
        if index > 0 {
            out.push('\n');
        }
        let line = line.trim_end_matches('\r').replace('\t', "    ");
        if line.chars().count() > max_line_chars {
            out.extend(line.chars().take(max_line_chars));
            out.push('…');
        } else {
            out.push_str(&line);
        }
    }
    if lines.next().is_some() {
        out.push_str("\n…");
    }
    out
}

/// "3 lines · 42 chars".
pub fn summary(text: &str) -> String {
    let lines = text.lines().count().max(1);
    let chars = text.chars().count();
    format!(
        "{} · {}",
        if lines == 1 {
            "1 line".to_owned()
        } else {
            format!("{lines} lines")
        },
        if chars == 1 {
            "1 char".to_owned()
        } else {
            format!("{chars} chars")
        }
    )
}

/// `" ".join(text.split())` cut to `limit` characters plus "…" (tray, list rows).
pub fn one_line(text: &str, limit: usize, scan: usize) -> String {
    let head: String = text.chars().take(scan).collect();
    let joined = head.split_whitespace().collect::<Vec<_>>().join(" ");
    if joined.chars().count() > limit || text.chars().count() > scan {
        joined.chars().take(limit).collect::<String>() + "…"
    } else {
        joined
    }
}

/// `datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")` (local time).
pub fn local_date(timestamp: f64) -> String {
    if !timestamp.is_finite() {
        return "Unknown date".into();
    }
    let seconds = timestamp.floor() as libc::time_t;
    // SAFETY: localtime_r writes into the provided struct only.
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    if unsafe { libc::localtime_r(&seconds, &mut tm) }.is_null() {
        return "Unknown date".into();
    }
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}",
        tm.tm_year + 1900,
        tm.tm_mon + 1,
        tm.tm_mday,
        tm.tm_hour,
        tm.tm_min
    )
}

/// Demo clippings for `--demo` and screenshots, newest first.
pub fn demo_clips() -> Vec<Clip> {
    let now = now();
    [
        (20.0, "Meet at the north entrance at 9:30 — bring the printed tickets."),
        (300.0, "fn main() {\n    let popup = Popup::new()?;\n    popup.show()?;\n    slint::run_event_loop()\n}"),
        (3_600.0, "https://github.com/patrick-hudson/jumpkut/releases/latest"),
        (
            7_200.0,
            "Jumpkut keeps the newest clippings first. Hold Alt, tap the shortcut to cycle, and let go to paste. \
             Esc cancels, the arrows move, and nothing leaves your machine.",
        ),
        (86_400.0, "ssh -L 8080:localhost:80 build@ci.internal"),
        (172_800.0, "Grüße aus Köln! 你好 🌍 — Unicode works too."),
    ]
    .iter()
    .enumerate()
    .map(|(i, (age, text))| Clip { id: format!("demo{i}"), text: (*text).to_owned(), created_at: now - age })
    .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(name: &str) -> Vec<u8> {
        std::fs::read(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures")
                .join(name),
        )
        .unwrap()
    }

    #[test]
    fn python_archives_round_trip_byte_for_byte() {
        for name in [
            "history.json",
            "empty-history.json",
            "merge-base.json",
            "merge-expected.json",
        ] {
            let raw = fixture(name);
            let clips = decode(&raw).unwrap();
            assert_eq!(encode(&clips).as_bytes(), raw.as_slice(), "{name}");
        }
        let clips = decode(&fixture("history.json")).unwrap();
        assert_eq!(
            clips[1].text,
            "e\u{301}\r\n\u{2028}\u{7f}\u{1}\u{1f}\u{8}\u{c}"
        );
        assert_eq!(clips[2].created_at, 1.7e20);
    }

    #[test]
    fn rejects_what_python_rejects() {
        for bad in [
            r#"{"version": 2, "clips": []}"#,
            r#"{"version": 1.0, "clips": []}"#,
            r#"{"version": true, "clips": []}"#,
            r#"{"version": 1, "clips": [{"id": "a", "text": " ", "created_at": 1}]}"#,
            r#"{"version": 1, "clips": [{"id": "a", "text": "x", "created_at": true}]}"#,
            r#"{"version": 1, "clips": [{"id": "a", "text": "x", "created_at": 1}, {"id": "b", "text": "x", "created_at": 1}]}"#,
        ] {
            assert!(decode(bad.as_bytes()).is_err(), "{bad}");
        }
    }

    #[test]
    fn backup_merge_matches_python() {
        let base = decode(&fixture("merge-base.json")).unwrap();
        let backup = decode(&fixture("merge-backup.json")).unwrap();
        let (count, merged) = merge(&base, backup, || "regenerated".to_owned());
        assert_eq!(
            count.to_string(),
            String::from_utf8(fixture("merge-count.txt")).unwrap()
        );
        assert_eq!(
            encode(&merged).as_bytes(),
            fixture("merge-expected.json").as_slice()
        );
    }

    #[test]
    fn export_import_and_corrupt_preservation_on_disk() {
        let dir = std::env::temp_dir().join(format!("jumpkut-test-{}", new_id()));
        let path = dir.join("history.json");
        let mut history = History::open(Some(path.clone()), None);
        history.add("first").unwrap();
        history.add("second").unwrap();
        history.add("first").unwrap();
        assert_eq!(
            history
                .items()
                .iter()
                .map(|c| c.text.as_str())
                .collect::<Vec<_>>(),
            ["first", "second"]
        );
        let backup = dir.join("backup.json");
        history.export_to(&backup).unwrap();
        assert_eq!(
            decode(&std::fs::read(&path).unwrap()).unwrap(),
            history.items()
        );
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        history.clear().unwrap();
        assert_eq!(history.import_from(&backup).unwrap(), 2);

        std::fs::write(&path, b"{not json").unwrap();
        let reopened = History::open(Some(path.clone()), None);
        assert!(
            reopened
                .load_error
                .as_deref()
                .unwrap()
                .contains("original preserved")
        );
        assert!(std::fs::read_dir(&dir).unwrap().any(|e| {
            e.unwrap()
                .file_name()
                .to_string_lossy()
                .contains(".corrupt-")
        }));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
