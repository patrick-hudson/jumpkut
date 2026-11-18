//! X11: global shortcut, hold-to-select, XTEST paste
//! and clipboard recording with XFixes (what GTK's `owner-change` uses).
//!
//! The shortcut is a passive grab on the root window for every lock-key mask.
//! While the popup is open this connection holds an active keyboard grab, so
//! every key — including the modifier release — arrives here; the popup is an
//! override-redirect window, so focus never leaves the target application.
//! Keyboard-layout changes (`MappingNotify`) re-read the map and re-grab.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::sync::mpsc::{Receiver, Sender, channel};
use std::time::{Duration, Instant};

use x11rb::connection::Connection;
use x11rb::protocol::Event;
use x11rb::protocol::xfixes::{self, ConnectionExt as _};
use x11rb::protocol::xproto::{
    self, AtomEnum, ConnectionExt as _, EventMask, GrabMode, GrabStatus, ModMask, Window,
};
use x11rb::protocol::xtest::ConnectionExt as _;
use x11rb::rust_connection::RustConnection;
use x11rb::wrapper::ConnectionExt as _;

use crate::shortcut::Shortcut;

const KEY_PRESS: u8 = 2;
const KEY_RELEASE: u8 = 3;

mod keysym {
    pub const ESCAPE: u32 = 0xff1b;
    pub const RETURN: u32 = 0xff0d;
    pub const KP_ENTER: u32 = 0xff8d;
    pub const TAB: u32 = 0xff09;
    pub const ISO_LEFT_TAB: u32 = 0xfe20;
    pub const LEFT: u32 = 0xff51;
    pub const UP: u32 = 0xff52;
    pub const RIGHT: u32 = 0xff53;
    pub const DOWN: u32 = 0xff54;
    pub const HOME: u32 = 0xff50;
    pub const END: u32 = 0xff57;
    pub const DELETE: u32 = 0xffff;
    pub const SHIFT_L: u32 = 0xffe1;
    pub const SHIFT_R: u32 = 0xffe2;
    pub const CONTROL_L: u32 = 0xffe3;
    pub const CONTROL_R: u32 = 0xffe4;
    pub const CAPS_LOCK: u32 = 0xffe5;
    pub const SHIFT_LOCK: u32 = 0xffe6;
    pub const ALT_L: u32 = 0xffe9;
    pub const ALT_R: u32 = 0xffea;
    pub const SUPER_L: u32 = 0xffeb;
    pub const SUPER_R: u32 = 0xffec;
    pub const NUM_LOCK: u32 = 0xff7f;
    pub const SCROLL_LOCK: u32 = 0xff14;
    pub const V: u32 = 0x76;
}

/// Events for the UI thread.
#[derive(Debug)]
pub enum X11Event {
    /// The shortcut fired; `previous` is the active window at that moment.
    Hotkey {
        at: Instant,
        previous: Option<u32>,
    },
    Step(i64),
    First,
    Last,
    Accept,
    Cancel,
    Delete,
    /// A paste attempt finished (`true` if the chord was typed).
    Pasted,
    Error(String),
}

pub enum Command {
    /// The popup closed for another reason; release the keyboard.
    Close,
    /// Grab the keyboard for a popup opened without the shortcut
    /// (`--show`); Enter selects, the modifier release does not.
    Grab,
    /// Type the paste chord into `target` (or the active window).
    Paste(Option<u32>),
    /// Replace the shortcut; the result is sent back.
    Rebind(Shortcut, Sender<Result<(), String>>),
    /// The active window (for tray pastes).
    Active(Sender<Option<u32>>),
}

#[derive(Clone)]
pub struct X11Handle {
    tx: Sender<Command>,
    waker: std::sync::Arc<UnixStream>,
}

impl X11Handle {
    pub fn send(&self, command: Command) {
        if self.tx.send(command).is_ok() {
            let _ = (&*self.waker).write(&[1]);
        }
    }
}

pub const TERMINAL_CLASSES: &[&str] = &[
    "alacritty",
    "blackbox",
    "com.mitchellh.ghostty",
    "com.raggesilver.blackbox",
    "foot",
    "footclient",
    "ghostty",
    "gnome-terminal",
    "gnome-terminal-server",
    "kgx",
    "kitty",
    "konsole",
    "lxterminal",
    "mate-terminal",
    "org.gnome.console",
    "org.gnome.ptyxis",
    "org.gnome.terminal",
    "org.kde.konsole",
    "org.kde.yakuake",
    "org.wezfurlong.wezterm",
    "ptyxis",
    "qterminal",
    "rxvt",
    "st",
    "st-256color",
    "terminator",
    "terminology",
    "tilix",
    "urxvt",
    "wezterm",
    "xfce4-terminal",
    "xterm",
    "yakuake",
];

/// Whether a window class (X11 `WM_CLASS`, NUL-separated, or a Wayland app
/// id / resource class) belongs to a terminal, which pastes with Ctrl+Shift+V.
pub fn is_terminal_class(class: &str) -> bool {
    class
        .split('\0')
        .any(|name| !name.is_empty() && TERMINAL_CLASSES.contains(&name.to_lowercase().as_str()))
}

fn key_down(keymap: &[u8], code: u8) -> bool {
    keymap
        .get(code as usize / 8)
        .is_some_and(|b| b & (1 << (code % 8)) != 0)
}

struct Keys {
    hotkey: u8,
    grab_masks: Vec<u16>,
    held: Vec<u8>,
    all_modifiers: Vec<u8>,
    shift_mask: u16,
    by_keycode: HashMap<u8, Vec<u32>>,
    control: u8,
    shift: u8,
    v: u8,
}

fn load_keys(conn: &RustConnection, shortcut: &Shortcut) -> Result<Keys, String> {
    let setup = conn.setup();
    let (min, max) = (setup.min_keycode, setup.max_keycode);
    let mapping = conn
        .get_keyboard_mapping(min, max - min + 1)
        .map_err(|e| e.to_string())?
        .reply()
        .map_err(|e| e.to_string())?;
    let per = (mapping.keysyms_per_keycode as usize).max(1);
    let mut by_keycode = HashMap::new();
    for (offset, syms) in mapping.keysyms.chunks(per).enumerate() {
        by_keycode.insert(
            min + offset as u8,
            syms.iter().copied().filter(|s| *s != 0).collect::<Vec<_>>(),
        );
    }
    let codes_for = |wanted: &[u32]| -> Vec<u8> {
        let mut codes: Vec<u8> = by_keycode
            .iter()
            .filter(|(_, s)| s.iter().any(|x| wanted.contains(x)))
            .map(|(c, _)| *c)
            .collect();
        codes.sort_unstable();
        codes
    };
    let sym = shortcut.keysym();
    let upper = if (0x61..=0x7a).contains(&sym) {
        sym - 0x20
    } else {
        sym
    };
    let hotkey = codes_for(&[sym, upper])
        .first()
        .copied()
        .ok_or_else(|| format!("The X11 keyboard has no key named {:?}.", shortcut.key))?;

    let modmap = conn
        .get_modifier_mapping()
        .map_err(|e| e.to_string())?
        .reply()
        .map_err(|e| e.to_string())?;
    let per_mod = (modmap.keycodes_per_modifier() as usize).max(1);
    let groups: Vec<&[u8]> = modmap.keycodes.chunks(per_mod).collect();
    let masks_for = |codes: &[u8]| -> Vec<u16> {
        groups
            .iter()
            .enumerate()
            .filter(|(_, g)| g.iter().any(|c| *c != 0 && codes.contains(c)))
            .map(|(i, _)| 1u16 << i)
            .collect()
    };
    let m = shortcut.modifiers;
    let mut held = Vec::new();
    let mut base: Vec<u16> = vec![0];
    for (on, syms, name) in [
        (
            m.control,
            &[keysym::CONTROL_L, keysym::CONTROL_R][..],
            "Control",
        ),
        (m.alt, &[keysym::ALT_L, keysym::ALT_R][..], "Alt"),
        (m.shift, &[keysym::SHIFT_L, keysym::SHIFT_R][..], "Shift"),
        (m.super_, &[keysym::SUPER_L, keysym::SUPER_R][..], "Super"),
    ] {
        if !on {
            continue;
        }
        let codes = codes_for(syms);
        let masks = masks_for(&codes);
        if masks.is_empty() {
            return Err(format!("The X11 keyboard has no {name} modifier mapped."));
        }
        held.extend(codes);
        base = base
            .iter()
            .flat_map(|b| masks.iter().map(move |x| b | x))
            .collect();
    }
    let locks = masks_for(&codes_for(&[
        keysym::CAPS_LOCK,
        keysym::SHIFT_LOCK,
        keysym::NUM_LOCK,
        keysym::SCROLL_LOCK,
    ]));
    let mut combos: Vec<u16> = vec![0];
    for lock in locks {
        let extra: Vec<u16> = combos.iter().map(|c| c | lock).collect();
        combos.extend(extra);
    }
    let mut grab_masks: Vec<u16> = base
        .iter()
        .flat_map(|b| combos.iter().map(move |c| b | c))
        .collect();
    grab_masks.sort_unstable();
    grab_masks.dedup();
    let first = |s: u32| codes_for(&[s]).first().copied().unwrap_or(0);
    Ok(Keys {
        hotkey,
        grab_masks,
        held,
        all_modifiers: groups
            .iter()
            .flat_map(|g| g.iter().copied())
            .filter(|c| *c != 0)
            .collect(),
        shift_mask: u16::from(ModMask::SHIFT),
        control: first(keysym::CONTROL_L),
        shift: first(keysym::SHIFT_L),
        v: first(keysym::V),
        by_keycode,
    })
}

struct Backend {
    sticky: std::sync::Arc<std::sync::atomic::AtomicBool>,
    conn: RustConnection,
    root: Window,
    shortcut: Shortcut,
    keys: Keys,
    grabbed_codes: Vec<(u8, u16)>,
    active_atom: u32,
    open: bool,
    grabbed: bool,
    release_selects: bool,
    emit: Box<dyn Fn(X11Event) + Send>,
}

impl Backend {
    /// Grab the shortcut for every mask; replaces the previous grabs atomically.
    fn grab_hotkey(&mut self) -> Result<(), String> {
        let wanted: Vec<(u8, u16)> = self
            .keys
            .grab_masks
            .iter()
            .map(|m| (self.keys.hotkey, *m))
            .collect();
        let new: Vec<(u8, u16)> = wanted
            .iter()
            .filter(|g| !self.grabbed_codes.contains(g))
            .copied()
            .collect();
        let mut cookies = Vec::new();
        for (code, mask) in &new {
            cookies.push(
                self.conn
                    .grab_key(
                        false,
                        self.root,
                        ModMask::from(*mask),
                        *code,
                        GrabMode::ASYNC,
                        GrabMode::ASYNC,
                    )
                    .map_err(|e| e.to_string())?,
            );
        }
        let failed = cookies.into_iter().filter_map(|c| c.check().err()).next();
        if let Some(error) = failed {
            for (code, mask) in &new {
                let _ = self.conn.ungrab_key(*code, self.root, ModMask::from(*mask));
            }
            let _ = self.conn.flush();
            let label = self.shortcut.label();
            return Err(if format!("{error:?}").contains("Access") {
                format!(
                    "Cannot bind {label}: another application already uses this global shortcut."
                )
            } else {
                format!("Cannot bind {label}: the X11 server rejected the shortcut ({error:?}).")
            });
        }
        for (code, mask) in self.grabbed_codes.iter().filter(|g| !wanted.contains(g)) {
            let _ = self.conn.ungrab_key(*code, self.root, ModMask::from(*mask));
        }
        self.grabbed_codes = wanted;
        self.conn.flush().map_err(|e| e.to_string())
    }

    fn rebind(&mut self, shortcut: Shortcut) -> Result<(), String> {
        let keys = load_keys(&self.conn, &shortcut)?;
        let (old_shortcut, old_keys) = (
            std::mem::replace(&mut self.shortcut, shortcut),
            std::mem::replace(&mut self.keys, keys),
        );
        if let Err(e) = self.grab_hotkey() {
            self.shortcut = old_shortcut;
            self.keys = old_keys;
            return Err(e);
        }
        Ok(())
    }

    fn keymap(&self) -> Option<Vec<u8>> {
        self.conn
            .query_keymap()
            .ok()?
            .reply()
            .ok()
            .map(|r| r.keys.to_vec())
    }

    fn held_down(&self) -> bool {
        self.keymap()
            .is_none_or(|map| self.keys.held.iter().any(|c| key_down(&map, *c)))
    }

    fn active_window(&self) -> Option<u32> {
        let reply = self
            .conn
            .get_property(false, self.root, self.active_atom, AtomEnum::WINDOW, 0, 1)
            .ok()?
            .reply()
            .ok()?;
        let window = reply
            .value32()
            .and_then(|mut v| v.next())
            .filter(|w| *w != 0 && *w != self.root);
        window.or_else(|| {
            let focus = self.conn.get_input_focus().ok()?.reply().ok()?.focus;
            (focus > 1 && focus != self.root).then_some(focus)
        })
    }

    fn start_grab(&mut self, time: u32, release_selects: bool) {
        self.open = true;
        self.release_selects = release_selects;
        let reply = self
            .conn
            .grab_keyboard(false, self.root, time, GrabMode::ASYNC, GrabMode::ASYNC)
            .ok()
            .and_then(|c| c.reply().ok());
        self.grabbed = reply.is_some_and(|r| r.status == GrabStatus::SUCCESS);
        if !self.grabbed {
            eprintln!("Jumpkut: keyboard grab failed; polling the modifier state instead");
        }
        let _ = self.conn.flush();
    }

    fn close_popup(&mut self) {
        if self.grabbed {
            let _ = self.conn.ungrab_keyboard(x11rb::CURRENT_TIME);
            let _ = self.conn.flush();
        }
        self.open = false;
        self.grabbed = false;
    }

    fn syms(&self, code: u8) -> Vec<u32> {
        self.keys.by_keycode.get(&code).cloned().unwrap_or_default()
    }

    fn handle(&mut self, event: Event) {
        match event {
            Event::MappingNotify(e) => {
                // Keyboard layout or modifier map changed: re-read and re-grab.
                if e.request != xproto::Mapping::POINTER {
                    match load_keys(&self.conn, &self.shortcut) {
                        Ok(keys) => {
                            self.keys = keys;
                            if let Err(err) = self.grab_hotkey() {
                                (self.emit)(X11Event::Error(err));
                            }
                        }
                        Err(err) => (self.emit)(X11Event::Error(err)),
                    }
                }
            }
            Event::KeyPress(e) if e.detail == self.keys.hotkey && !self.open => {
                let at = Instant::now();
                let previous = self.active_window();
                let sticky = self.sticky.load(std::sync::atomic::Ordering::Relaxed);
                self.start_grab(e.time, !sticky);
                (self.emit)(X11Event::Hotkey { at, previous });
            }
            Event::KeyPress(e) if self.open => {
                let syms = self.syms(e.detail);
                let shift = u16::from(e.state) & self.keys.shift_mask != 0;
                let is = |s: u32| syms.contains(&s);
                if e.detail == self.keys.hotkey {
                    (self.emit)(X11Event::Step(if shift { -1 } else { 1 }));
                } else if !self.grabbed {
                    // Without the active grab only the passive grab reports keys.
                } else if is(keysym::ESCAPE) {
                    self.close_popup();
                    (self.emit)(X11Event::Cancel);
                } else if is(keysym::RETURN) || is(keysym::KP_ENTER) {
                    self.close_popup();
                    (self.emit)(X11Event::Accept);
                } else if is(keysym::UP) || is(keysym::LEFT) || is(keysym::ISO_LEFT_TAB) {
                    (self.emit)(X11Event::Step(-1));
                } else if is(keysym::DOWN) || is(keysym::RIGHT) || is(keysym::TAB) {
                    (self.emit)(X11Event::Step(if shift { -1 } else { 1 }));
                } else if is(keysym::HOME) {
                    (self.emit)(X11Event::First);
                } else if is(keysym::END) {
                    (self.emit)(X11Event::Last);
                } else if is(keysym::DELETE) {
                    (self.emit)(X11Event::Delete);
                }
            }
            Event::KeyRelease(e)
                if self.open
                    && self.grabbed
                    && self.release_selects
                    && self.keys.held.contains(&e.detail)
                    && !self.held_down() =>
            {
                self.close_popup();
                (self.emit)(X11Event::Accept);
            }
            _ => {}
        }
    }

    fn wm_class(&self, window: u32) -> String {
        self.conn
            .get_property(false, window, AtomEnum::WM_CLASS, AtomEnum::STRING, 0, 256)
            .ok()
            .and_then(|c| c.reply().ok())
            .map(|r| String::from_utf8_lossy(&r.value).into_owned())
            .unwrap_or_default()
    }

    /// Whether X input focus is `target` or one of its children.
    fn focus_is_target(&self, target: u32) -> bool {
        let Some(mut focus) = self
            .conn
            .get_input_focus()
            .ok()
            .and_then(|c| c.reply().ok())
            .map(|r| r.focus)
        else {
            return false;
        };
        for _ in 0..64 {
            if focus == target {
                return true;
            }
            if focus <= 1 || focus == self.root {
                return false;
            }
            match self
                .conn
                .query_tree(focus)
                .ok()
                .and_then(|c| c.reply().ok())
            {
                Some(tree) => focus = tree.parent,
                None => return false,
            }
        }
        false
    }

    /// Type Ctrl+V (Ctrl+Shift+V in terminals) into `target` once every
    /// modifier is released and it has focus.
    fn paste(&mut self, target: Option<u32>) -> bool {
        let Some(target) = target.or_else(|| self.active_window()) else {
            return false;
        };
        if self.keys.control == 0 || self.keys.v == 0 {
            return false;
        }
        let deadline = Instant::now() + Duration::from_millis(1500);
        loop {
            let up = self
                .keymap()
                .is_some_and(|m| !self.keys.all_modifiers.iter().any(|c| key_down(&m, *c)));
            if up && self.focus_is_target(target) {
                break;
            }
            if Instant::now() > deadline {
                return false;
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        let class = self.wm_class(target);
        let terminal = is_terminal_class(&class);
        if terminal && self.keys.shift == 0 {
            return false;
        }
        let mut chord = vec![self.keys.control];
        if terminal {
            chord.push(self.keys.shift);
        }
        chord.push(self.keys.v);
        // The shortcut may itself be the paste chord: suspend our grabs under
        // a short server grab so the synthetic keys reach the target.
        let _ = self.conn.grab_server();
        let suspended = self.grabbed_codes.clone();
        for (code, mask) in &suspended {
            let _ = self.conn.ungrab_key(*code, self.root, ModMask::from(*mask));
        }
        for code in &chord {
            let _ = self.conn.xtest_fake_input(
                KEY_PRESS,
                *code,
                x11rb::CURRENT_TIME,
                self.root,
                0,
                0,
                0,
            );
        }
        for code in chord.iter().rev() {
            let _ = self.conn.xtest_fake_input(
                KEY_RELEASE,
                *code,
                x11rb::CURRENT_TIME,
                self.root,
                0,
                0,
                0,
            );
        }
        let _ = self.conn.sync();
        for (code, mask) in &suspended {
            let _ = self.conn.grab_key(
                false,
                self.root,
                ModMask::from(*mask),
                *code,
                GrabMode::ASYNC,
                GrabMode::ASYNC,
            );
        }
        let _ = self.conn.ungrab_server();
        let _ = self.conn.sync();
        let name = class
            .split('\0')
            .nth(1)
            .filter(|n| !n.is_empty())
            .unwrap_or("the target window")
            .to_owned();
        eprintln!(
            "Jumpkut: pasted with {} into {name} (XTEST)",
            if terminal { "Ctrl+Shift+V" } else { "Ctrl+V" }
        );
        true
    }

    fn run(mut self, rx: Receiver<Command>, mut waker: UnixStream) {
        let fd = self.conn.stream().as_raw_fd();
        let waker_fd = waker.as_raw_fd();
        loop {
            loop {
                match self.conn.poll_for_event() {
                    Ok(Some(event)) => self.handle(event),
                    Ok(None) => break,
                    Err(e) => {
                        (self.emit)(X11Event::Error(format!(
                            "The X11 display connection failed: {e}"
                        )));
                        return;
                    }
                }
            }
            while let Ok(command) = rx.try_recv() {
                match command {
                    Command::Close => self.close_popup(),
                    Command::Grab => {
                        if !self.open {
                            self.start_grab(x11rb::CURRENT_TIME, false);
                        }
                    }
                    Command::Paste(target) => {
                        if self.paste(target) {
                            (self.emit)(X11Event::Pasted);
                        }
                    }
                    Command::Rebind(shortcut, reply) => {
                        let _ = reply.send(self.rebind(shortcut));
                    }
                    Command::Active(reply) => {
                        let _ = reply.send(self.active_window());
                    }
                }
            }
            if self.open && !self.grabbed && self.release_selects && !self.held_down() {
                self.close_popup();
                (self.emit)(X11Event::Accept);
            }
            let mut fds = [
                libc::pollfd {
                    fd,
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd: waker_fd,
                    events: libc::POLLIN,
                    revents: 0,
                },
            ];
            let timeout = if self.open && !self.grabbed { 15 } else { -1 };
            // SAFETY: `fds` is a valid array for the duration of the call.
            unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, timeout) };
            if fds[1].revents & libc::POLLIN != 0 {
                let mut buffer = [0u8; 64];
                let _ = waker.read(&mut buffer);
            }
        }
    }
}

fn intern(conn: &RustConnection, name: &str) -> Result<u32, String> {
    Ok(conn
        .intern_atom(false, name.as_bytes())
        .map_err(|e| e.to_string())?
        .reply()
        .map_err(|e| e.to_string())?
        .atom)
}

/// `sticky`: when set, the modifier release does not select (Enter does).
pub fn start(
    shortcut: &Shortcut,
    sticky: std::sync::Arc<std::sync::atomic::AtomicBool>,
    emit: impl Fn(X11Event) + Send + 'static,
) -> Result<X11Handle, String> {
    let (conn, screen) =
        x11rb::connect(None).map_err(|e| format!("Cannot connect to the X11 display: {e}"))?;
    let root = conn.setup().roots[screen].root;
    let keys = load_keys(&conn, shortcut)?;
    let active_atom = intern(&conn, "_NET_ACTIVE_WINDOW")?;
    let mut backend = Backend {
        sticky,
        conn,
        root,
        shortcut: shortcut.clone(),
        keys,
        grabbed_codes: Vec::new(),
        active_atom,
        open: false,
        grabbed: false,
        release_selects: true,
        emit: Box::new(emit),
    };
    backend.grab_hotkey()?;
    let (tx, rx) = channel();
    let (waker, thread_waker) = UnixStream::pair().map_err(|e| e.to_string())?;
    thread_waker
        .set_nonblocking(true)
        .map_err(|e| e.to_string())?;
    std::thread::Builder::new()
        .name("x11-hotkey".into())
        .spawn(move || backend.run(rx, thread_waker))
        .map_err(|e| e.to_string())?;
    Ok(X11Handle {
        tx,
        waker: std::sync::Arc::new(waker),
    })
}

/// What a clipboard change contained.
#[derive(Debug)]
pub enum ClipboardChange {
    Text(String),
    /// Offered with a password-manager "sensitive" target: never recorded.
    Sensitive,
    /// No owner, or no text.
    Nothing,
}

const SENSITIVE: [&str; 3] = [
    "x-kde-passwordManagerHint",
    "application/x-nspasteboard-concealed-type",
    "application/x-keepassxc-secret",
];

struct Watcher {
    conn: RustConnection,
    window: u32,
    clipboard: u32,
    targets: u32,
    utf8: u32,
    incr: u32,
    property: u32,
    sensitive: Vec<u32>,
    pending: bool,
    /// Counts CLIPBOARD ownership changes seen, so a capture can tell
    /// whether its TARGETS check and text read saw the same owner.
    changes: u64,
}

impl Watcher {
    /// Wait for an event matching `want` (other events are inspected only
    /// for new clipboard owners), up to `timeout`.
    fn wait<T>(
        &mut self,
        timeout: Duration,
        mut want: impl FnMut(&Event) -> Option<T>,
    ) -> Option<T> {
        let deadline = Instant::now() + timeout;
        loop {
            match self.conn.poll_for_event() {
                Ok(Some(event)) => {
                    if matches!(event, Event::XfixesSelectionNotify(_)) {
                        self.pending = true;
                        self.changes = self.changes.wrapping_add(1);
                    }
                    if let Some(v) = want(&event) {
                        return Some(v);
                    }
                    continue;
                }
                Ok(None) => {}
                Err(_) => return None,
            }
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return None;
            }
            let mut fds = [libc::pollfd {
                fd: self.conn.stream().as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            }];
            // SAFETY: valid single pollfd.
            unsafe {
                libc::poll(
                    fds.as_mut_ptr(),
                    1,
                    left.as_millis().min(i32::MAX as u128) as i32,
                )
            };
        }
    }

    /// ConvertSelection and read the result, following INCR transfers.
    fn read(&mut self, target: u32) -> Option<(u32, Vec<u8>)> {
        self.conn.delete_property(self.window, self.property).ok()?;
        self.conn
            .convert_selection(
                self.window,
                self.clipboard,
                target,
                self.property,
                x11rb::CURRENT_TIME,
            )
            .ok()?;
        self.conn.flush().ok()?;
        let window = self.window;
        let property = self.wait(Duration::from_secs(2), |e| match e {
            Event::SelectionNotify(n) if n.requestor == window => Some(n.property),
            _ => None,
        })?;
        if property == x11rb::NONE {
            return None;
        }
        let reply = self
            .conn
            .get_property(
                true,
                self.window,
                self.property,
                AtomEnum::ANY,
                0,
                u32::MAX / 4,
            )
            .ok()?
            .reply()
            .ok()?;
        self.conn.flush().ok()?;
        if reply.type_ != self.incr {
            return Some((reply.type_, reply.value));
        }
        // INCR: each chunk arrives as a new property value; empty ends it.
        let mut data = Vec::new();
        let mut kind = 0;
        loop {
            let prop = self.property;
            self.wait(Duration::from_secs(2), |e| match e {
                Event::PropertyNotify(p)
                    if p.window == window
                        && p.atom == prop
                        && p.state == xproto::Property::NEW_VALUE =>
                {
                    Some(())
                }
                _ => None,
            })?;
            let chunk = self
                .conn
                .get_property(
                    true,
                    self.window,
                    self.property,
                    AtomEnum::ANY,
                    0,
                    u32::MAX / 4,
                )
                .ok()?
                .reply()
                .ok()?;
            self.conn.flush().ok()?;
            if chunk.value.is_empty() {
                return Some((kind, data));
            }
            kind = chunk.type_;
            data.extend_from_slice(&chunk.value);
        }
    }

    fn owner(&self) -> Option<u32> {
        self.conn
            .get_selection_owner(self.clipboard)
            .ok()
            .and_then(|c| c.reply().ok())
            .map(|r| r.owner)
    }

    /// Read the clipboard text, unless its TARGETS mark it as sensitive. The
    /// check and the read must see one ownership: if the owner changed in
    /// between (say a password manager took over), discard and start again.
    fn capture(&mut self) -> ClipboardChange {
        for _ in 0..5 {
            self.pending = false;
            let changes = self.changes;
            let owner = self.owner();
            if owner.is_none_or(|o| o == x11rb::NONE) {
                return ClipboardChange::Nothing;
            }
            let result = self.capture_once();
            // The owner reply is a round trip: ownership-change events sent
            // before it are queued by then; drain them into `changes`.
            let same_owner = self.owner() == owner;
            let _ = self.wait(Duration::ZERO, |_| None::<()>);
            if same_owner && self.changes == changes {
                return result;
            }
        }
        ClipboardChange::Nothing
    }

    fn capture_once(&mut self) -> ClipboardChange {
        let Some((_, targets)) = self.read(self.targets) else {
            return ClipboardChange::Nothing;
        };
        let atoms: Vec<u32> = targets
            .as_chunks::<4>()
            .0
            .iter()
            .map(|c| u32::from_ne_bytes(*c))
            .collect();
        if atoms.iter().any(|a| self.sensitive.contains(a)) {
            return ClipboardChange::Sensitive;
        }
        let target = if atoms.contains(&self.utf8) || atoms.is_empty() {
            self.utf8
        } else {
            u32::from(AtomEnum::STRING)
        };
        match self.read(target) {
            Some((kind, bytes)) if kind == u32::from(AtomEnum::STRING) => {
                // STRING is Latin-1.
                ClipboardChange::Text(bytes.iter().map(|b| *b as char).collect())
            }
            Some((_, bytes)) => ClipboardChange::Text(String::from_utf8_lossy(&bytes).into_owned()),
            None => ClipboardChange::Nothing,
        }
    }
}

/// Watch CLIPBOARD ownership with XFixes; reports every change. Send `()` on
/// the returned channel to re-read the current clipboard (start, unpause).
pub fn watch_clipboard(
    emit: impl Fn(ClipboardChange) + Send + 'static,
) -> Result<Sender<()>, String> {
    let (conn, screen) =
        x11rb::connect(None).map_err(|e| format!("Cannot connect to the X11 display: {e}"))?;
    let root = conn.setup().roots[screen].root;
    conn.xfixes_query_version(5, 0)
        .map_err(|e| e.to_string())?
        .reply()
        .map_err(|e| format!("XFixes: {e}"))?;
    let window = conn.generate_id().map_err(|e| e.to_string())?;
    conn.create_window(
        0,
        window,
        root,
        -10,
        -10,
        1,
        1,
        0,
        xproto::WindowClass::INPUT_ONLY,
        0,
        &xproto::CreateWindowAux::new().event_mask(EventMask::PROPERTY_CHANGE),
    )
    .map_err(|e| e.to_string())?;
    let mut watcher = Watcher {
        clipboard: intern(&conn, "CLIPBOARD")?,
        targets: intern(&conn, "TARGETS")?,
        utf8: intern(&conn, "UTF8_STRING")?,
        incr: intern(&conn, "INCR")?,
        property: intern(&conn, "JUMPKUT_SELECTION")?,
        sensitive: SENSITIVE
            .iter()
            .map(|n| intern(&conn, n))
            .collect::<Result<_, _>>()?,
        conn,
        window,
        pending: false,
        changes: 0,
    };
    watcher
        .conn
        .xfixes_select_selection_input(
            window,
            watcher.clipboard,
            xfixes::SelectionEventMask::SET_SELECTION_OWNER
                | xfixes::SelectionEventMask::SELECTION_WINDOW_DESTROY
                | xfixes::SelectionEventMask::SELECTION_CLIENT_CLOSE,
        )
        .map_err(|e| e.to_string())?;
    watcher.conn.flush().map_err(|e| e.to_string())?;
    let (tx, rx) = channel::<()>();
    std::thread::Builder::new()
        .name("x11-clipboard".into())
        .spawn(move || {
            loop {
                let mut capture = rx.try_recv().is_ok() || std::mem::take(&mut watcher.pending);
                if !capture {
                    capture = watcher
                        .wait(Duration::from_millis(250), |e| {
                            matches!(e, Event::XfixesSelectionNotify(_)).then_some(())
                        })
                        .is_some();
                    watcher.pending = false;
                }
                if capture {
                    emit(watcher.capture());
                }
            }
        })
        .map_err(|e| e.to_string())?;
    Ok(tx)
}

/// The override-redirect popup's helpers on the UI thread: monitor geometry
/// for centring, and the KDE blur property.
pub struct X11Window {
    conn: RustConnection,
    root: u32,
}

impl X11Window {
    pub fn connect() -> Result<Self, String> {
        let (conn, screen) = x11rb::connect(None).map_err(|e| e.to_string())?;
        let root = conn.setup().roots[screen].root;
        Ok(X11Window { conn, root })
    }

    pub fn set_blur(&self, window: u32, rects: &[crate::region::Rect]) -> Result<(), String> {
        let atom = intern(&self.conn, "_KDE_NET_WM_BLUR_BEHIND_REGION")?;
        let data: Vec<u32> = rects
            .iter()
            .flat_map(|r| [r.x as u32, r.y as u32, r.w as u32, r.h as u32])
            .collect();
        self.conn
            .change_property32(
                xproto::PropMode::REPLACE,
                window,
                atom,
                AtomEnum::CARDINAL,
                &data,
            )
            .map_err(|e| e.to_string())?;
        self.conn.flush().map_err(|e| e.to_string())
    }

    /// The monitor that contains the pointer (x, y, w, h).
    pub fn pointer_monitor(&self) -> Option<(i32, i32, i32, i32)> {
        use x11rb::protocol::randr::ConnectionExt as _;
        let pointer = self.conn.query_pointer(self.root).ok()?.reply().ok()?;
        let (px, py) = (pointer.root_x as i32, pointer.root_y as i32);
        if let Some(monitors) = self
            .conn
            .randr_get_monitors(self.root, true)
            .ok()
            .and_then(|c| c.reply().ok())
        {
            let rect = |m: &x11rb::protocol::randr::MonitorInfo| {
                (m.x as i32, m.y as i32, m.width as i32, m.height as i32)
            };
            if let Some(m) = monitors
                .monitors
                .iter()
                .map(rect)
                .find(|(x, y, w, h)| px >= *x && px < x + w && py >= *y && py < y + h)
            {
                return Some(m);
            }
            if let Some(m) = monitors
                .monitors
                .iter()
                .find(|m| m.primary)
                .or(monitors.monitors.first())
            {
                return Some(rect(m));
            }
        }
        let g = self.conn.get_geometry(self.root).ok()?.reply().ok()?;
        Some((0, 0, g.width as i32, g.height as i32))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn terminal_classes() {
        for class in [
            "konsole\0konsole\0",
            "xterm\0XTerm\0",
            "org.kde.konsole",
            "com.mitchellh.ghostty",
            "kitty",
        ] {
            assert!(is_terminal_class(class), "{class}");
        }
        for class in ["kate\0kate\0", "org.kde.kate", "firefox", ""] {
            assert!(!is_terminal_class(class), "{class}");
        }
    }
}
