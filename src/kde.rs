//! KDE Plasma Wayland session services (as in the Python app, 0.7.x):
//!
//! * KGlobalAccel owns the global shortcut (also shown in System Settings).
//! * A KWin script reports the active window (id, resource class, pid) and
//!   centres/activates the popup, so it gets keyboard focus for the release.
//! * `wl-paste --watch` (ext-data-control) records clipboard changes; offers
//!   flagged `x-kde-passwordManagerHint` arrive as "sensitive" and are skipped.
//! * `wl-copy` publishes a chosen clipping.

use std::collections::{HashSet, VecDeque};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use zbus::MatchRule;
use zbus::blocking::{Connection, MessageIterator};
use zbus::message::Type as MessageType;
use zbus::zvariant::OwnedObjectPath;

use crate::shortcut::Shortcut;

pub const ACTION: &str = "show-history";
const KGLOBALACCEL: &str = "org.kde.kglobalaccel";
const KWIN: &str = "org.kde.KWin";
const WINDOWS_PATH: &str = "/io/github/jumpkut/KWinWindows";
const WINDOWS_INTERFACE: &str = "io.github.jumpkut.KWinWindows";

// KGlobalAccel::SetShortcutFlag values.
const IS_DEFAULT: u32 = 1;
const SET_PRESENT: u32 = 2;
const NO_AUTOLOADING: u32 = 4;

/// The KGlobalAccel component: Jumpkut's own (`jumpkut`, shared with the
/// Python app) unless `JUMPKUT_KDE_COMPONENT` overrides it for testing.
pub fn component() -> String {
    std::env::var("JUMPKUT_KDE_COMPONENT")
        .ok()
        .filter(|c| !c.is_empty())
        .unwrap_or_else(|| "jumpkut".into())
}

const SCRIPT: &str = r#"
const SERVICE = %SERVICE%;
const APP = %APP%;
const TITLE = %TITLE%;
const OUTPUT = %OUTPUT%;
function report(w) {
  callDBus(SERVICE, %PATH%, %INTERFACE%, "Activated",
           w ? String(w.internalId) : "",
           w ? String(w.resourceClass || "") : "",
           w && w.windowId ? String(w.windowId) : "",
           w ? String(w.pid || 0) : "0",
           !!w && String(w.resourceClass) === APP && String(w.caption) === TITLE);
}
function setup(w) {
  if (!w || String(w.resourceClass) !== APP || String(w.caption) !== TITLE) return;
  w.keepAbove = true;
  w.skipTaskbar = true;
  w.skipPager = true;
  w.skipSwitcher = true;
  w.skipsCloseAnimation = true;
  const output = (OUTPUT && workspace.screens.find(s => s.name === OUTPUT)) || workspace.activeScreen;
  const area = workspace.clientArea(KWin.MaximizeArea, output, workspace.currentDesktop);
  const g = w.frameGeometry;
  w.frameGeometry = {
    x: Math.round(area.x + (area.width - g.width) / 2),
    y: Math.round(area.y + (area.height - g.height) / 2),
    width: g.width, height: g.height
  };
  workspace.activeWindow = w;
}
workspace.windowActivated.connect(report);
workspace.windowAdded.connect(setup);
report(workspace.activeWindow);
"#;

const ACTIVATE_SCRIPT: &str = r#"
const target = %TARGET%;
for (const w of workspace.windowList()) {
  if (String(w.internalId) === target) { workspace.activeWindow = w; break; }
}
"#;

fn js(value: &str) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "\"\"".into())
}

#[derive(Clone, Debug, Default)]
pub struct WindowInfo {
    pub id: String,
    pub class: String,
    pub pid: String,
    /// Jumpkut's quick popup (matched by app id and title in KWin).
    pub popup: bool,
}

#[derive(Default)]
pub struct ActiveWindows {
    pub active: Option<WindowInfo>,
    /// Recent window id -> pid.
    owners: VecDeque<(String, String)>,
    kwin_owner: String,
}

struct WindowsObject(Arc<Mutex<ActiveWindows>>);

#[zbus::interface(name = "io.github.jumpkut.KWinWindows")]
impl WindowsObject {
    fn activated(
        &self,
        #[zbus(header)] header: zbus::message::Header<'_>,
        id: String,
        class: String,
        _x11: String,
        pid: String,
        popup: bool,
    ) -> zbus::fdo::Result<()> {
        let mut state = self.0.lock().unwrap();
        // Only KWin may report windows.
        if header.sender().map(|s| s.as_str()) != Some(state.kwin_owner.as_str()) {
            return Err(zbus::fdo::Error::AccessDenied(
                "Only KWin may report windows.".into(),
            ));
        }
        if id.is_empty() {
            state.active = None;
        } else {
            state.owners.retain(|(i, _)| *i != id);
            state.owners.push_back((id.clone(), pid.clone()));
            while state.owners.len() > 64 {
                state.owners.pop_front();
            }
            if std::env::var_os("JUMPKUT_DEBUG").is_some() {
                eprintln!("Jumpkut: active window {id} class {class:?}");
            }
            state.active = Some(WindowInfo {
                id,
                class,
                pid,
                popup,
            });
        }
        Ok(())
    }
}

pub struct Kde {
    conn: Connection,
    component: String,
    action: Vec<String>,
    registered: bool,
    listening: bool,
    script: Option<String>,
    script_dir: Option<std::path::PathBuf>,
    pub windows: Arc<Mutex<ActiveWindows>>,
}

impl Kde {
    pub fn new(conn: Connection) -> Self {
        let component = component();
        Kde {
            action: vec![
                component.clone(),
                ACTION.into(),
                "Jumpkut".into(),
                "Show clipboard history".into(),
            ],
            component,
            conn,
            registered: false,
            listening: false,
            script: None,
            script_dir: crate::paths::runtime_dir(),
            windows: Arc::new(Mutex::new(ActiveWindows::default())),
        }
    }

    fn accel<B>(&self, method: &str, body: &B) -> zbus::Result<zbus::message::Message>
    where
        B: serde::Serialize + zbus::zvariant::DynamicType,
    {
        self.conn.call_method(
            Some(KGLOBALACCEL),
            "/kglobalaccel",
            Some("org.kde.KGlobalAccel"),
            method,
            body,
        )
    }

    /// Register (or re-bind) the shortcut. The first call also starts the
    /// thread that reports presses.
    pub fn bind(
        &mut self,
        shortcut: &Shortcut,
        on_pressed: impl Fn(Instant) + Send + 'static,
    ) -> Result<(), String> {
        let key = shortcut.qt_key() as i32;
        let label = shortcut.label();
        let err = |e: zbus::Error| format!("KDE session services are unavailable: {e}");
        self.accel("doRegister", &(&self.action,)).map_err(err)?;
        self.registered = true;
        let available: bool = self
            .accel("globalShortcutAvailable", &((vec![key],), &self.component))
            .and_then(|m| m.body().deserialize())
            .map_err(err)?;
        if !available {
            let ours: Vec<(Vec<i32>,)> = self
                .accel("shortcutKeys", &(&self.action,))
                .and_then(|m| m.body().deserialize())
                .unwrap_or_default();
            if !ours.iter().any(|(seq,)| seq.contains(&key)) {
                return Err(format!(
                    "Cannot bind {label}: another application already uses this global shortcut"
                ));
            }
        }
        let keys = vec![(vec![key],)];
        self.accel(
            "setShortcutKeys",
            &(&self.action, &keys, IS_DEFAULT | NO_AUTOLOADING),
        )
        .map_err(err)?;
        let assigned: Vec<(Vec<i32>,)> = self
            .accel(
                "setShortcutKeys",
                &(&self.action, &keys, SET_PRESENT | NO_AUTOLOADING),
            )
            .and_then(|m| m.body().deserialize())
            .map_err(err)?;
        if !assigned.iter().any(|(seq,)| seq.contains(&key)) {
            return Err(format!(
                "Cannot bind {label}: KDE did not accept the shortcut."
            ));
        }
        if !self.listening {
            let path: OwnedObjectPath = self
                .accel("getComponent", &(&self.component,))
                .and_then(|m| m.body().deserialize())
                .map_err(err)?;
            let rule = MatchRule::builder()
                .msg_type(MessageType::Signal)
                .sender(KGLOBALACCEL)
                .and_then(|b| b.interface("org.kde.kglobalaccel.Component"))
                .and_then(|b| b.member("globalShortcutPressed"))
                .and_then(|b| b.path(path))
                .map_err(err)?
                .build();
            let iter = MessageIterator::for_match_rule(rule, &self.conn, Some(16)).map_err(err)?;
            let component = self.component.clone();
            std::thread::Builder::new()
                .name("kglobalaccel".into())
                .spawn(move || {
                    for message in iter.flatten() {
                        let at = Instant::now();
                        if let Ok((c, action, _)) =
                            message.body().deserialize::<(String, String, i64)>()
                            && c == component
                            && action == ACTION
                        {
                            on_pressed(at);
                        }
                    }
                })
                .map_err(|e| e.to_string())?;
            self.listening = true;
        }
        Ok(())
    }

    fn scripting<B>(&self, method: &str, body: &B) -> zbus::Result<zbus::message::Message>
    where
        B: serde::Serialize + zbus::zvariant::DynamicType,
    {
        self.conn.call_method(
            Some(KWIN),
            "/Scripting",
            Some("org.kde.kwin.Scripting"),
            method,
            body,
        )
    }

    fn load_script(&self, name: &str, file: &str, source: &str) -> Result<i32, String> {
        let dir = self
            .script_dir
            .as_ref()
            .ok_or("No private directory for KWin scripts is available.")?;
        crate::paths::ensure_private_dir(dir)?;
        let path = dir.join(file);
        std::fs::write(&path, source).map_err(|e| e.to_string())?;
        let _ = self.scripting("unloadScript", &(name,));
        let id: i32 = self
            .scripting("loadScript", &(path.to_string_lossy().as_ref(), name))
            .and_then(|m| m.body().deserialize())
            .map_err(|e| format!("KWin scripting: {e}"))?;
        if id < 0 {
            return Err("KWin could not load the script.".into());
        }
        Ok(id)
    }

    fn run_script(&self, id: i32, wait: bool) -> Result<(), String> {
        let path = format!("/Scripting/Script{id}");
        if wait {
            self.conn
                .call_method(
                    Some(KWIN),
                    path.as_str(),
                    Some("org.kde.kwin.Script"),
                    "run",
                    &(),
                )
                .map(|_| ())
                .map_err(|e| e.to_string())
        } else {
            // The script calls back into this process; don't wait for it.
            let conn = self.conn.clone();
            std::thread::spawn(move || {
                let _ = conn.call_method(
                    Some(KWIN),
                    path.as_str(),
                    Some("org.kde.kwin.Script"),
                    "run",
                    &(),
                );
            });
            Ok(())
        }
    }

    /// Serve the window reports and load the script for the popup titled
    /// `popup_title` of `app_id` (centred on `output`, or the active screen).
    pub fn track_windows(
        &mut self,
        app_id: &str,
        popup_title: &str,
        output: Option<&str>,
    ) -> Result<(), String> {
        let owner: String = self
            .conn
            .call_method(
                Some("org.freedesktop.DBus"),
                "/org/freedesktop/DBus",
                Some("org.freedesktop.DBus"),
                "GetNameOwner",
                &(KWIN,),
            )
            .and_then(|m| m.body().deserialize())
            .map_err(|e| format!("KWin is not running: {e}"))?;
        self.windows.lock().unwrap().kwin_owner = owner;
        self.conn
            .object_server()
            .at(WINDOWS_PATH, WindowsObject(self.windows.clone()))
            .map_err(|e| e.to_string())?;
        let service = self
            .conn
            .unique_name()
            .map(|n| n.to_string())
            .unwrap_or_default();
        let source = SCRIPT
            .replace("%SERVICE%", &js(&service))
            .replace("%APP%", &js(app_id))
            .replace("%TITLE%", &js(popup_title))
            .replace("%OUTPUT%", &js(output.unwrap_or("")))
            .replace("%PATH%", &js(WINDOWS_PATH))
            .replace("%INTERFACE%", &js(WINDOWS_INTERFACE));
        let name = format!("{}-windows", self.component);
        let id = self.load_script(&name, "windows.js", &source)?;
        self.script = Some(name);
        self.run_script(id, false)
    }

    /// The active keyboard layout index (the XKB group), if Plasma answers
    /// within a moment.
    pub fn layout(&self) -> Option<u32> {
        let conn = self.conn.clone();
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let layout = conn
                .call_method(
                    Some("org.kde.keyboard"),
                    "/Layouts",
                    Some("org.kde.KeyboardLayouts"),
                    "getLayout",
                    &(),
                )
                .and_then(|reply| reply.body().deserialize::<u32>());
            let _ = tx.send(layout);
        });
        rx.recv_timeout(Duration::from_millis(200))
            .ok()?
            .map_err(|e| eprintln!("Jumpkut: keyboard layout: {e}"))
            .ok()
    }

    pub fn active(&self) -> Option<WindowInfo> {
        self.windows.lock().unwrap().active.clone()
    }

    pub fn is_own_window(&self, id: &str) -> bool {
        let me = std::process::id().to_string();
        self.windows
            .lock()
            .unwrap()
            .owners
            .iter()
            .any(|(i, pid)| i == id && *pid == me)
    }

    /// Ask KWin to activate `id` (focus returns asynchronously otherwise).
    pub fn activate(&self, id: &str) {
        let name = format!("{}-activate-{}", self.component, std::process::id());
        if let Ok(script) = self.load_script(
            &name,
            "activate.js",
            &ACTIVATE_SCRIPT.replace("%TARGET%", &js(id)),
        ) {
            let _ = self.run_script(script, true);
        }
        let _ = self.scripting("unloadScript", &(name.as_str(),));
    }

    pub fn close(&mut self) {
        if let Some(name) = self.script.take() {
            let _ = self.scripting("unloadScript", &(name.as_str(),));
        }
        let _ = self
            .conn
            .object_server()
            .remove::<WindowsObject, _>(WINDOWS_PATH);
        if let Some(dir) = &self.script_dir {
            let _ = std::fs::remove_dir_all(dir);
        }
        if self.registered {
            self.registered = false;
            if self.component == "jumpkut" {
                // Like the Python app: keep the System Settings entry, stop firing.
                let _ = self.accel("setInactive", &(&self.action,));
            } else {
                let _ = self.accel("unregister", &(self.component.as_str(), ACTION));
            }
        }
    }
}

impl Drop for Kde {
    fn drop(&mut self) {
        self.close();
    }
}

/// Runs once per clipboard change with the text on stdin (same as the Python app).
const WATCH_SCRIPT: &str = r#"case ${CLIPBOARD_STATE:-unknown} in
  data) printf 'data '; base64 -w0 ;;
  *) printf '%s' "${CLIPBOARD_STATE:-unknown}"; cat >/dev/null ;;
esac
printf '\n'
"#;

/// `(state, text)` for one watcher line; anything but valid "data" has no text.
pub fn parse_watch_line(line: &str) -> (String, Option<String>) {
    let (state, payload) = line.split_once(' ').unwrap_or((line, ""));
    if state != "data" {
        return (state.to_owned(), None);
    }
    match base64_decode(payload.trim_end()) {
        Some(bytes) => (
            "data".into(),
            Some(String::from_utf8_lossy(&bytes).into_owned()),
        ),
        None => ("invalid".into(), None),
    }
}

fn base64_decode(text: &str) -> Option<Vec<u8>> {
    let value = |c: u8| -> Option<u32> {
        Some(match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return None,
        } as u32)
    };
    let bytes = text.as_bytes();
    if !bytes.len().is_multiple_of(4) {
        return None;
    }
    let mut out = Vec::with_capacity(bytes.len() / 4 * 3);
    for chunk in bytes.chunks(4) {
        let pad = chunk.iter().rev().take_while(|b| **b == b'=').count();
        if pad > 2 {
            return None;
        }
        let mut n = 0u32;
        for (i, b) in chunk.iter().enumerate() {
            n = (n << 6) | if i >= 4 - pad { 0 } else { value(*b)? };
        }
        out.extend_from_slice(&n.to_be_bytes()[1..4 - pad]);
    }
    Some(out)
}

/// Start `wl-paste --watch`; `emit(state, text)` runs on its thread for every
/// change. It is restarted (with back-off) if it exits.
pub fn watch_clipboard(emit: impl Fn(String, Option<String>) + Send + 'static) {
    std::thread::Builder::new()
        .name("wl-paste".into())
        .spawn(move || {
            let mut failures = 0u32;
            loop {
                let mut command = Command::new("wl-paste");
                command
                    .args(["--type", "text", "--watch", "sh", "-c", WATCH_SCRIPT])
                    .stdout(Stdio::piped())
                    .stderr(Stdio::null());
                // Don't outlive Jumpkut (this thread lives until the process exits).
                // SAFETY: prctl is async-signal-safe.
                unsafe {
                    std::os::unix::process::CommandExt::pre_exec(&mut command, || {
                        libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
                        Ok(())
                    });
                }
                let child = command.spawn();
                if let Ok(mut child) = child {
                    if let Some(stdout) = child.stdout.take() {
                        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                            failures = 0;
                            let (state, text) = parse_watch_line(&line);
                            if state != "invalid" {
                                emit(state, text);
                            }
                        }
                    }
                    let _ = child.kill();
                    let _ = child.wait();
                }
                failures += 1;
                let delay = 2u64.pow(failures.min(5)).min(30);
                eprintln!("Jumpkut: wl-paste --watch stopped; restarting in {delay} s");
                std::thread::sleep(Duration::from_secs(delay));
            }
        })
        .expect("spawn wl-paste watcher");
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Publish {
    Idle,
    /// The clipboard is not yet known to hold the wanted text.
    Pending,
    Done,
    Failed,
}

/// Publishes clippings with `wl-copy --foreground`; the newest always wins.
/// Paste waits until the watcher has seen the wanted text (`Done`).
pub struct Publisher {
    live: Vec<(Child, String)>,
    wanted: Option<String>,
    superseded: HashSet<String>,
    pub state: Publish,
    deadline: Option<Instant>,
}

impl Publisher {
    pub fn new() -> Self {
        Publisher {
            live: Vec::new(),
            wanted: None,
            superseded: HashSet::new(),
            state: Publish::Idle,
            deadline: None,
        }
    }

    pub fn set_text(&mut self, text: &str) -> Result<(), String> {
        // Stop every older publisher first, so none can take the clipboard
        // back after this one; their texts may echo back (Plasma restores
        // them) and are not new copies.
        for (mut child, previous) in self.live.drain(..) {
            if previous != text {
                self.superseded.insert(previous);
            }
            let _ = child.kill();
            let _ = child.wait();
        }
        self.wanted = Some(text.to_owned());
        self.state = Publish::Pending;
        self.deadline = Some(Instant::now() + Duration::from_millis(1500));
        // Test hook: publish as a password-manager copy, so a clipboard
        // manager running alongside (e.g. the Python Jumpkut) skips it.
        let sensitive = std::env::var_os("JUMPKUT_SENSITIVE_COPY").is_some();
        let mut child = Command::new("wl-copy")
            .args(["--foreground", "--type", "text/plain;charset=utf-8"])
            .args(if sensitive { &["--sensitive"][..] } else { &[] })
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| {
                self.state = Publish::Failed;
                format!("wl-copy could not start: {e}")
            })?;
        if let Some(mut stdin) = child.stdin.take() {
            let bytes = text.as_bytes().to_vec();
            std::thread::spawn(move || {
                let _ = stdin.write_all(&bytes);
            });
        }
        self.live.push((child, text.to_owned()));
        Ok(())
    }

    /// A watcher event. Returns true if it is an echo to ignore.
    pub fn observe(&mut self, state: &str, text: Option<&str>) -> bool {
        self.live
            .retain_mut(|(child, _)| matches!(child.try_wait(), Ok(None)));
        let own_sensitive =
            state == "sensitive" && std::env::var_os("JUMPKUT_SENSITIVE_COPY").is_some();
        let wanted =
            own_sensitive || (state == "data" && text.is_some() && text == self.wanted.as_deref());
        if self.state == Publish::Done && !wanted {
            // The clipboard changed again: ours is gone, so don't paste.
            self.state = Publish::Idle;
            return false;
        }
        if self.state == Publish::Pending && own_sensitive {
            self.state = Publish::Done;
            return false;
        }
        if self.state != Publish::Pending || state != "data" {
            return false;
        }
        if wanted {
            self.state = Publish::Done;
            self.superseded.clear();
            self.deadline = None;
            false
        } else {
            text.is_some_and(|t| self.superseded.contains(t))
        }
    }

    /// Check for timeouts or a publisher that died; returns a failure message.
    pub fn poll(&mut self) -> Option<String> {
        if self.state != Publish::Pending {
            return None;
        }
        let dead = self
            .live
            .last_mut()
            .is_none_or(|(child, _)| !matches!(child.try_wait(), Ok(None)));
        let timed_out = self.deadline.is_some_and(|d| Instant::now() > d);
        if dead || timed_out {
            self.state = Publish::Failed;
            self.superseded.clear();
            return Some(if dead {
                "Could not copy the clipping: wl-copy stopped before it owned the clipboard.".into()
            } else {
                "Could not copy the clipping: wl-copy did not take the clipboard in time.".into()
            });
        }
        None
    }
}

/// Why this Wayland session cannot run Jumpkut, if so.
pub fn wayland_problem() -> Option<String> {
    let desktop = std::env::var("XDG_CURRENT_DESKTOP").unwrap_or_default();
    if !desktop.split(':').any(|d| d == "KDE") {
        return Some(
            "On Wayland, Jumpkut currently supports KDE Plasma. On other desktops, choose an X11 session at login.".into(),
        );
    }
    let output = Command::new("wl-paste").arg("--version").output();
    let Ok(output) = output else {
        return Some(
            "Jumpkut needs wl-clipboard on Wayland (missing wl-copy, wl-paste).\nOn Fedora: sudo dnf install wl-clipboard"
                .into(),
        );
    };
    let text = String::from_utf8_lossy(&output.stdout);
    let version: Option<Vec<u32>> = text
        .split("wl-clipboard ")
        .nth(1)
        .and_then(|rest| rest.split_whitespace().next())
        .map(|v| v.split('.').filter_map(|p| p.parse().ok()).collect());
    let ok = version.as_ref().is_some_and(|v| {
        let v = [
            v.first().copied().unwrap_or(0),
            v.get(1).copied().unwrap_or(0),
            v.get(2).copied().unwrap_or(0),
        ];
        v >= [2, 3, 0]
    });
    if !ok {
        let found = version.map_or("an unknown version".into(), |v| {
            v.iter().map(u32::to_string).collect::<Vec<_>>().join(".")
        });
        return Some(format!(
            "Jumpkut needs wl-clipboard 2.3.0 or newer on Wayland (found {found}), so that password copies stay out of history."
        ));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_later_clipboard_change_invalidates_a_done_publication() {
        let mut publisher = Publisher::new();
        publisher.wanted = Some("A".into());
        publisher.state = Publish::Pending;
        assert!(!publisher.observe("data", Some("A")));
        assert_eq!(publisher.state, Publish::Done);
        publisher.observe("data", Some("A"));
        assert_eq!(publisher.state, Publish::Done);
        assert!(!publisher.observe("data", Some("B")));
        assert_eq!(publisher.state, Publish::Idle);
    }
}
