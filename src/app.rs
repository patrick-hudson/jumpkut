//! The application: clipboard recording, the quick popup, paste, the tray,
//! and the Full History / Preferences / About windows.

use std::cell::RefCell;
use std::collections::VecDeque;
use std::path::PathBuf;
use std::rc::Rc;
use std::time::{Duration, Instant};

use slint::winit_030::{EventResult, WinitWindowAccessor, winit};
use slint::{ComponentHandle, VecModel};

use crate::history::{self, Clip, History};
use crate::kde::{self, Kde, Publish, Publisher};
use crate::picker::Picker;
use crate::settings::{self, Config, Settings};
use crate::shortcut::{self, Shortcut};
use crate::ui::{
    AboutWindow, HistoryRow, HistoryWindow, MessageWindow, Popup, PreferencesWindow, Theme,
};
use crate::{inject, portal, region, theme, tray, x11};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const POPUP_TITLE: &str = "Jumpkut — Clipboard history";
pub const ICON_SVG: &[u8] = include_bytes!("../assets/jumpkut.svg");
const POPUP_SIZE: (f32, f32) = (392.0, 262.0);

/// The xdg app id / X11 class (the desktop file is `jumpkut.desktop`).
pub fn app_id() -> String {
    std::env::var("JUMPKUT_APP_ID")
        .ok()
        .filter(|a| !a.is_empty())
        .unwrap_or_else(|| "jumpkut".into())
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Session {
    X11,
    KdeWayland,
}

/// The window a paste goes back to.
#[derive(Clone, Debug, PartialEq)]
pub enum Target {
    X11(u32),
    Kde(String),
}

#[derive(Debug)]
pub enum Ev {
    Hotkey(Instant, Option<Target>),
    Step(i64),
    First,
    Last,
    Accept,
    Cancel,
    PopupDelete,
    Modifiers {
        held: bool,
        any: bool,
    },
    Focus(bool),
    FocusSettled(u64),
    FocusDeadline(u64),
    Frame,
    WindowReady,
    /// Hide the prebuilt, unmapped popup from the taskbar (KDE Wayland).
    PrimeExtras(u8),
    Rebuild,
    WarmupDone,
    X11Clipboard(x11::ClipboardChange),
    WaylandClipboard(String, Option<String>),
    PasteTick(u64),
    BackendError(String),
    HistorySaveError(String),
    Command(String),
    Tray(tray::Action),
    HistorySearch(String),
    HistorySelect(i32),
    HistoryCopy,
    HistoryDelete,
    HistoryClosed,
    PrefsRecord(String, shortcut::Modifiers),
    PrefsRecording(bool),
    PrefsSave,
    PrefsClosed,
    AboutClosed,
    MessageAnswered(usize, bool),
    ClearHistory,
    Backup(bool),
    BackupChosen(bool, Result<Option<PathBuf>, String>),
    Quit,
}

thread_local! {
    static QUEUE: RefCell<VecDeque<Ev>> = const { RefCell::new(VecDeque::new()) };
    pub static APP: RefCell<Option<App>> = const { RefCell::new(None) };
    /// The shortcut's modifiers, whose release selects (read by the popup).
    static HELD: std::cell::Cell<shortcut::Modifiers> = const { std::cell::Cell::new(shortcut::Modifiers { control: false, alt: true, shift: false, super_: false }) };
}

/// Handle an event on the UI thread. Re-entrant calls (from inside Slint or
/// winit callbacks while the app runs) are queued and drained by the caller.
pub fn dispatch(ev: Ev) {
    QUEUE.with(|q| q.borrow_mut().push_back(ev));
    APP.with(|a| {
        let Ok(mut guard) = a.try_borrow_mut() else {
            return;
        };
        let Some(app) = guard.as_mut() else { return };
        while let Some(ev) = QUEUE.with(|q| q.borrow_mut().pop_front()) {
            app.handle(ev);
        }
    });
}

/// From any thread.
pub fn send(ev: Ev) {
    let _ = slint::invoke_from_event_loop(move || dispatch(ev));
}

/// `JUMPKUT_DEMO=1`: demo clippings in memory, nothing recorded (screenshots).
fn demo() -> bool {
    std::env::var_os("JUMPKUT_DEMO").is_some()
}

fn debug() -> bool {
    std::env::var_os("JUMPKUT_DEBUG").is_some()
}

pub fn rss_kib() -> Option<u64> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    status
        .lines()
        .find_map(|l| l.strip_prefix("VmRSS:"))
        .and_then(|v| v.trim().trim_end_matches(" kB").trim().parse().ok())
}

macro_rules! apply_theme {
    ($component:expr, $t:expr) => {{
        let t: &theme::Theme = $t;
        let g = $component.global::<Theme>();
        g.set_dark(t.dark);
        g.set_accent(t.accent.to_slint(1.0));
        g.set_on_accent(t.on_accent.to_slint(1.0));
        g.set_window_bg(t.window_bg.to_slint(1.0));
        g.set_view_bg(t.view_bg.to_slint(1.0));
        g.set_fg(t.fg.to_slint(1.0));
        g.set_muted(t.muted.to_slint(1.0));
        if let Some(family) = &t.font_family {
            g.set_font_family(family.into());
        }
        g.set_font_size(t.font_px);
    }};
}

#[derive(Default)]
struct Timing {
    signal: Option<Instant>,
    shown: Option<Instant>,
    frame: Option<Instant>,
    focus: Option<Instant>,
}

struct HistoryView {
    window: HistoryWindow,
    rows: Rc<VecModel<HistoryRow>>,
    ids: Vec<String>,
    query: String,
    previewed: Option<(String, String)>,
}

struct PrefsView {
    window: PreferencesWindow,
    shortcut: String,
    recording: bool,
}

/// Watches a Wayland publication until it succeeds or fails, then pastes
/// into `target`, if any.
struct PasteJob {
    generation: u64,
    target: Option<String>,
    deadline: Instant,
    /// Show the outcome in Full History's status line.
    report: bool,
    /// Asked KWin again to activate `target` after focus went elsewhere.
    reactivated: bool,
}

pub struct App {
    pub session: Session,
    popup: Popup,
    theme: theme::Theme,
    pub config: Config,
    history: History,
    shortcut: Shortcut,
    backend_ok: bool,
    paused: bool,
    ignore_text: Option<String>,
    last_selected: Option<String>,
    previous: Option<Target>,
    picker: Picker,
    popup_clips: Vec<Clip>,
    // X11
    x11: Option<x11::X11Handle>,
    x11_clipboard: Option<std::sync::mpsc::Sender<()>>,
    x11_window: Option<x11::X11Window>,
    x11_sticky: std::sync::Arc<std::sync::atomic::AtomicBool>,
    clipboard: Option<arboard::Clipboard>,
    warming: bool,
    // KDE Wayland
    pub kde: Option<Kde>,
    publisher: Publisher,
    last_wayland: Option<(String, Option<String>)>,
    injector: Option<inject::Injector>,
    paste_job: Option<PasteJob>,
    paste_generation: u64,
    paste_timer: slint::Timer,
    reported_conflict: bool,
    extras: Option<crate::blur::SurfaceExtras>,
    extras_failed: bool,
    extras_pending: bool,
    had_focus: bool,
    mods_any: bool,
    pending_accept: bool,
    focus_generation: u64,
    focus_timer: slint::Timer,
    deadline_timer: slint::Timer,
    output: Option<String>,
    // UI
    tray: Option<ksni::blocking::Handle<tray::JumpkutTray>>,
    history_view: Option<HistoryView>,
    prefs: Option<PrefsView>,
    about: Option<AboutWindow>,
    messages: Vec<(usize, MessageWindow, Option<Ev>)>,
    message_counter: usize,
    timing: Timing,
    shows: u32,
}

impl App {
    pub fn new(session: Session, theme: theme::Theme) -> Result<App, String> {
        let config = settings::load_default();
        let mut problems: Vec<String> = config.load_error.iter().cloned().collect();
        let demo = demo();
        let path = (config.settings.persist && !demo).then(crate::paths::history_path);
        let mut history = History::open(path, Some(Box::new(|e| send(Ev::HistorySaveError(e)))));
        if demo {
            // Screenshots and demos: built-in clippings, memory only.
            history.replace_all(history::demo_clips());
        }
        problems.extend(history.load_error.iter().cloned());
        let shortcut = match shortcut::parse(&config.settings.hotkey) {
            Ok(s) => s,
            Err(_) => {
                problems.push("Invalid saved keyboard shortcut. Using Alt+C; the original preferences file is preserved until you save Preferences.".into());
                shortcut::parse("<Alt>c").expect("default shortcut")
            }
        };
        let popup = build_popup(&theme, session)?;
        let sticky =
            std::sync::Arc::new(std::sync::atomic::AtomicBool::new(config.settings.sticky));
        let mut app = App {
            session,
            popup,
            theme,
            config,
            history,
            shortcut,
            backend_ok: false,
            paused: false,
            ignore_text: None,
            last_selected: None,
            previous: None,
            picker: Picker::default(),
            popup_clips: Vec::new(),
            x11: None,
            x11_clipboard: None,
            x11_window: None,
            x11_sticky: sticky,
            clipboard: None,
            warming: false,
            kde: None,
            publisher: Publisher::new(),
            last_wayland: None,
            injector: None,
            paste_job: None,
            paste_generation: 0,
            paste_timer: slint::Timer::default(),
            reported_conflict: false,
            extras: None,
            extras_failed: false,
            extras_pending: false,
            had_focus: false,
            mods_any: false,
            pending_accept: false,
            focus_generation: 0,
            focus_timer: slint::Timer::default(),
            deadline_timer: slint::Timer::default(),
            output: std::env::var("JUMPKUT_OUTPUT")
                .ok()
                .filter(|o| !o.is_empty()),
            tray: None,
            history_view: None,
            prefs: None,
            about: None,
            messages: Vec::new(),
            message_counter: 0,
            timing: Timing::default(),
            shows: 0,
        };
        app.update_popup_texts();
        match settings::migrate_autostart() {
            Ok(true) => eprintln!("Jumpkut: updated the startup entry left by the Python version"),
            Ok(false) => {}
            Err(e) => eprintln!("Jumpkut: could not update the startup entry: {e}"),
        }
        app.start_session()?;
        app.prime_extras();
        app.start_tray();
        for problem in problems {
            app.notify(&problem);
        }
        Ok(app)
    }

    fn start_session(&mut self) -> Result<(), String> {
        match self.session {
            Session::KdeWayland => {
                let conn = crate::instance::connect()?;
                let mut kde = Kde::new(conn);
                if let Err(e) = kde.track_windows(&app_id(), POPUP_TITLE, self.output.as_deref()) {
                    eprintln!("Jumpkut: {e}; paste and popup placement are limited");
                }
                self.kde = Some(kde);
                self.bind_shortcut();
                kde::watch_clipboard(|state, text| send(Ev::WaylandClipboard(state, text)));
                self.prepare_paste();
            }
            Session::X11 => {
                self.bind_shortcut();
                if !demo() {
                    match x11::watch_clipboard(|change| send(Ev::X11Clipboard(change))) {
                        Ok(tx) => {
                            let _ = tx.send(()); // capture the current clipboard
                            self.x11_clipboard = Some(tx);
                        }
                        Err(e) => self.notify(&format!("Clipboard recording is unavailable: {e}")),
                    }
                }
                slint::Timer::single_shot(Duration::from_millis(0), || {
                    APP.with(|a| {
                        if let Some(app) = a.borrow_mut().as_mut() {
                            app.warm_up_x11();
                        }
                    })
                });
            }
        }
        Ok(())
    }

    /// Bind the configured shortcut; on failure keep running (tray works).
    fn bind_shortcut(&mut self) {
        let result = self.rebind(&self.shortcut.clone());
        self.backend_ok = result.is_ok();
        if let Err(e) = result {
            self.notify(&format!(
                "Keyboard shortcut unavailable: {e}. Open history from the tray or change the shortcut in Preferences."
            ));
        }
    }

    fn rebind(&mut self, shortcut: &Shortcut) -> Result<(), String> {
        match self.session {
            Session::KdeWayland => self
                .kde
                .as_mut()
                .ok_or("KDE session services are unavailable")?
                .bind(shortcut, |at| send(Ev::Hotkey(at, None))),
            Session::X11 => {
                if let Some(x) = &self.x11 {
                    let (tx, rx) = std::sync::mpsc::channel();
                    x.send(x11::Command::Rebind(shortcut.clone(), tx));
                    rx.recv_timeout(Duration::from_secs(5))
                        .map_err(|e| e.to_string())?
                } else {
                    let handle = x11::start(shortcut, self.x11_sticky.clone(), |event| {
                        send(match event {
                            x11::X11Event::Hotkey { at, previous } => {
                                Ev::Hotkey(at, previous.map(Target::X11))
                            }
                            x11::X11Event::Step(d) => Ev::Step(d),
                            x11::X11Event::First => Ev::First,
                            x11::X11Event::Last => Ev::Last,
                            x11::X11Event::Accept => Ev::Accept,
                            x11::X11Event::Cancel => Ev::Cancel,
                            x11::X11Event::Delete => Ev::PopupDelete,
                            x11::X11Event::Pasted => return,
                            x11::X11Event::Error(e) => Ev::BackendError(e),
                        })
                    })?;
                    self.x11 = Some(handle);
                    Ok(())
                }
            }
        }
    }

    fn prepare_paste(&mut self) {
        if self.session == Session::KdeWayland {
            if self.config.settings.auto_paste {
                if self.injector.is_none() {
                    self.injector = Some(inject::Injector::new(true));
                }
            } else {
                self.injector = None;
            }
        }
    }

    fn start_tray(&mut self) {
        // JUMPKUT_TRAY_ICON: another SVG for the tray (tests and screenshots).
        let custom = std::env::var_os("JUMPKUT_TRAY_ICON").and_then(|p| std::fs::read(p).ok());
        let icons = tray::icon_pixmaps(custom.as_deref().unwrap_or(ICON_SVG));
        let tray = tray::JumpkutTray {
            tooltip: String::new(),
            count: 0,
            clips: Vec::new(),
            paused: false,
            icons,
            on_action: std::sync::Arc::new(|action| send(Ev::Tray(action))),
        };
        use ksni::blocking::TrayMethods;
        // Register even if the panel is not up yet (login race); the item
        // appears once a StatusNotifierWatcher does.
        match tray.assume_sni_available(true).spawn() {
            Ok(handle) => self.tray = Some(handle),
            Err(e) => eprintln!("Jumpkut: the tray icon is unavailable: {e}"),
        }
        self.update_tray();
    }

    fn update_tray(&self) {
        let Some(tray) = &self.tray else { return };
        let status = if self.paused {
            "Paused".to_owned()
        } else {
            format!("{} clips", self.history.len())
        };
        let tooltip = format!("Jumpkut {VERSION} · {status} · {}", self.shortcut.label());
        let clips: Vec<(String, String)> = self
            .history
            .recent(self.config.settings.tray_history_limit())
            .iter()
            .map(|c| (c.id.clone(), history::one_line(&c.text, 65, usize::MAX)))
            .collect();
        let (count, paused) = (self.history.len(), self.paused);
        tray.update(move |t| {
            t.tooltip = tooltip;
            t.count = count;
            t.clips = clips;
            t.paused = paused;
        });
    }

    pub fn notify(&self, text: &str) {
        eprintln!("Jumpkut: {text}");
        portal::notify(text.to_owned());
    }

    fn update_popup_texts(&self) {
        let s = &self.shortcut;
        HELD.with(|h| h.set(s.modifiers));
        let hint = if self.config.settings.sticky || !self.backend_ok {
            "↑ ↓ cycle  ·  Enter select  ·  Delete remove  ·  Esc cancel".to_owned()
        } else {
            format!(
                "{} or ↑ ↓ cycle  ·  release {} to select  ·  Esc",
                s.label(),
                s.modifier_label()
            )
        };
        self.popup.set_hint(hint.into());
        self.popup.set_hotkey_char(s.slint_char().into());
    }

    pub fn handle(&mut self, ev: Ev) {
        match ev {
            Ev::Hotkey(at, previous) => self.hotkey(at, previous),
            Ev::Step(d) => {
                if self.picker.step(d) {
                    self.render();
                }
            }
            Ev::First => {
                if self.picker.first() {
                    self.render();
                }
            }
            Ev::Last => {
                if self.picker.last() {
                    self.render();
                }
            }
            Ev::Accept => self.accept_popup(),
            Ev::Cancel => self.cancel_popup(true),
            Ev::PopupDelete => self.popup_delete(),
            Ev::Modifiers { held, any } => {
                if self.session != Session::KdeWayland {
                    return;
                }
                self.mods_any = any;
                if self.pending_accept && !any {
                    self.pending_accept = false;
                    self.accept_popup();
                } else if self.picker.release(held) {
                    self.accept_popup();
                }
            }
            Ev::Focus(focused) => self.focus_changed(focused),
            Ev::FocusSettled(generation) => {
                if generation == self.focus_generation && self.picker.focus_settled() {
                    self.accept_popup();
                }
            }
            Ev::FocusDeadline(generation) => {
                if generation == self.focus_generation && self.picker.is_open() && !self.had_focus {
                    self.cancel_popup(true);
                    self.notify(
                        "The history popup did not receive keyboard focus. Try the shortcut again.",
                    );
                }
            }
            Ev::Frame => self.frame(),
            Ev::PrimeExtras(attempt) => {
                if self.session != Session::KdeWayland || self.picker.is_open() {
                    return;
                }
                // Slint creates the winit window shortly after the event loop
                // starts; retry briefly until it exists.
                if self.popup.window().with_winit_window(|_| ()).is_some() {
                    self.prime_surface();
                } else if attempt < 100 {
                    slint::Timer::single_shot(Duration::from_millis(20), move || {
                        dispatch(Ev::PrimeExtras(attempt + 1))
                    });
                }
            }
            Ev::WindowReady => {
                if self.extras_pending && self.picker.is_open() {
                    self.apply_extras();
                }
            }
            Ev::Rebuild => {
                if !self.picker.is_open() {
                    match build_popup(&self.theme, self.session) {
                        Ok(popup) => {
                            self.popup = popup;
                            self.update_popup_texts();
                            self.prime_extras();
                        }
                        Err(e) => eprintln!("Jumpkut: could not rebuild the popup: {e}"),
                    }
                }
            }
            Ev::WarmupDone => {
                if std::mem::take(&mut self.warming) && !self.picker.is_open() {
                    let _ = self.popup.hide();
                    if debug() {
                        eprintln!(
                            "Jumpkut: popup warmed up (RSS {:.1} MiB)",
                            rss_kib().unwrap_or(0) as f64 / 1024.0
                        );
                    }
                }
            }
            Ev::X11Clipboard(change) => {
                if self.paused {
                    return;
                }
                match change {
                    x11::ClipboardChange::Sensitive => self.ignore_text = None,
                    x11::ClipboardChange::Text(text) => self.record_text(Some(text)),
                    x11::ClipboardChange::Nothing => self.record_text(None),
                }
            }
            Ev::WaylandClipboard(state, text) => {
                if self.publisher.observe(&state, text.as_deref()) {
                    return; // an echo of a superseded publication
                }
                self.last_wayland = Some((state.clone(), text.clone()));
                if self.paused || demo() {
                    return;
                }
                if state == "sensitive" {
                    self.ignore_text = None;
                } else if state == "data" {
                    self.record_text(text);
                }
            }
            Ev::PasteTick(generation) => self.paste_tick(generation),
            Ev::BackendError(e) => self.notify(&e),
            Ev::HistorySaveError(e) => self.notify(&format!(
                "History is available in memory, but could not be saved: {e}"
            )),
            Ev::Command(command) => self.command(&command),
            Ev::Tray(action) => self.tray_action(action),
            Ev::HistorySearch(query) => {
                if let Some(view) = &mut self.history_view {
                    view.query = query;
                }
                self.refresh_history(true);
            }
            Ev::HistorySelect(index) => self.history_selected(index),
            Ev::HistoryCopy => {
                if let Some(clip) = self.history_current()
                    && self.select_clip(&clip, None)
                {
                    match self.paste_job.as_mut() {
                        // Wayland: reported once wl-copy owns the clipboard.
                        Some(job) => job.report = true,
                        None => self.history_status("Copied to clipboard"),
                    }
                }
            }
            Ev::HistoryDelete => {
                if let Some(clip) = self.history_current() {
                    self.remove_clip(&clip.id);
                }
            }
            // Hide before dropping: Slint keeps a visible window on screen.
            Ev::HistoryClosed => {
                if let Some(view) = self.history_view.take() {
                    let _ = view.window.hide();
                }
            }
            Ev::PrefsRecord(text, modifiers) => {
                if let Some(shortcut) = Shortcut::from_slint(&text, modifiers) {
                    self.set_recorded_shortcut(shortcut.accel());
                }
            }
            Ev::PrefsRecording(on) => {
                if let Some(p) = &mut self.prefs {
                    p.recording = on;
                    p.window.set_recording(on);
                }
            }
            Ev::PrefsSave => self.save_preferences(),
            Ev::PrefsClosed => {
                if let Some(view) = self.prefs.take() {
                    let _ = view.window.hide();
                }
            }
            Ev::AboutClosed => {
                if let Some(about) = self.about.take() {
                    let _ = about.hide();
                }
            }
            Ev::MessageAnswered(id, ok) => {
                if let Some(pos) = self.messages.iter().position(|(i, ..)| *i == id) {
                    let (_, window, follow_up) = self.messages.remove(pos);
                    let _ = window.hide();
                    if ok && let Some(ev) = follow_up {
                        dispatch(ev);
                    }
                }
            }
            Ev::ClearHistory => {
                if let Err(e) = self.history.clear() {
                    self.message(&e, "", true, None);
                }
                self.history_changed();
            }
            Ev::Backup(save) => {
                portal::choose_backup(save, move |r| send(Ev::BackupChosen(save, r)))
            }
            Ev::BackupChosen(save, result) => self.backup_chosen(save, result),
            Ev::Quit => {
                let _ = slint::quit_event_loop();
            }
        }
    }

    fn command(&mut self, command: &str) {
        match command {
            "show" => self.show_popup(false, None),
            "history" => self.show_full_history(),
            "preferences" => self.show_preferences(),
            "about" => self.show_about(),
            "quit" => {
                let _ = slint::quit_event_loop();
            }
            _ => {}
        }
    }

    // ----- popup ---------------------------------------------------------

    fn hotkey(&mut self, at: Instant, previous: Option<Target>) {
        if let Some(p) = &self.prefs
            && p.recording
        {
            // A global shortcut never reaches the recorder: keep it.
            let current = self.config.settings.hotkey.clone();
            self.set_recorded_shortcut(current);
            self.x11_close();
            return;
        }
        if self.picker.is_open() {
            if self.picker.step(1) {
                self.render();
            }
            return;
        }
        if self.prefs.is_some() {
            self.x11_close();
            return;
        }
        self.previous = previous;
        self.show_popup(true, Some(at));
    }

    fn x11_close(&self) {
        if let Some(x) = &self.x11 {
            x.send(x11::Command::Close);
        }
    }

    fn show_popup(&mut self, held: bool, at: Option<Instant>) {
        // Cancel a pending paste, but keep watching its publication.
        if let Some(job) = self.paste_job.as_mut() {
            job.target = None;
        }
        if self.picker.is_open() {
            return;
        }
        if self.session == Session::KdeWayland {
            self.previous = self
                .kde
                .as_ref()
                .and_then(Kde::active)
                .filter(|w| !self.is_own(&w.id))
                .map(|w| Target::Kde(w.id));
        } else if !held {
            self.previous = None;
        }
        let release_selects = held && !self.config.settings.sticky && self.backend_ok;
        self.popup_clips = self
            .history
            .recent(self.config.settings.quick_history_limit())
            .to_vec();
        let start = if self.config.settings.resume_last_selection {
            self.last_selected
                .as_ref()
                .and_then(|id| self.popup_clips.iter().position(|c| &c.id == id))
                .unwrap_or(0)
        } else {
            0
        };
        self.picker
            .open(self.popup_clips.len(), start, release_selects);
        self.timing = Timing {
            signal: at.or_else(|| Some(Instant::now())),
            ..Timing::default()
        };
        self.warming = false;
        self.had_focus = false;
        self.pending_accept = false;
        self.mods_any = false;
        self.focus_generation += 1;
        self.render();
        if self.session == Session::X11 {
            self.center_x11();
        }
        if let Err(e) = self.popup.show() {
            eprintln!("Jumpkut: could not show the popup: {e}");
            self.picker.cancel();
            self.x11_close();
            return;
        }
        self.timing.shown = Some(Instant::now());
        self.shows += 1;
        self.extras_pending = true;
        self.apply_extras();
        self.popup.window().request_redraw();
        if self.session == Session::X11
            && !held
            && let Some(x) = &self.x11
        {
            x.send(x11::Command::Grab);
        }
        if self.session == Session::KdeWayland {
            let generation = self.focus_generation;
            self.deadline_timer.start(
                slint::TimerMode::SingleShot,
                Duration::from_millis(1500),
                move || dispatch(Ev::FocusDeadline(generation)),
            );
        }
    }

    fn render(&self) {
        let p = &self.popup;
        let Some(clip) = self.popup_clips.get(self.picker.index()) else {
            p.set_empty(true);
            p.set_number(0);
            p.set_count(0);
            p.set_preview("Your clipboard history starts here.\n\nCopy some text in any application, then use your history shortcut.".into());
            return;
        };
        p.set_empty(false);
        p.set_number(self.picker.index() as i32 + 1);
        p.set_count(self.popup_clips.len() as i32);
        p.set_preview(history::preview(&clip.text, 8, 400).into());
        p.set_age(history::relative_age(clip.created_at, history::now()).into());
        p.set_summary(history::summary(&clip.text).into());
    }

    fn focus_changed(&mut self, focused: bool) {
        if self.session != Session::KdeWayland || !self.picker.is_open() {
            return;
        }
        if focused {
            if self.timing.focus.is_none() {
                self.timing.focus = Some(Instant::now());
                self.report_timing();
            }
            self.had_focus = true;
            self.deadline_timer.stop();
            if debug()
                && let Some(w) = self.kde.as_ref().and_then(Kde::active)
            {
                eprintln!(
                    "Jumpkut: popup focused; KWin window {} class {:?}",
                    w.id, w.class
                );
            }
            self.focus_generation += 1;
            let generation = self.focus_generation;
            // KWin sends the modifier state right after keyboard focus.
            self.focus_timer.start(
                slint::TimerMode::SingleShot,
                Duration::from_millis(60),
                move || dispatch(Ev::FocusSettled(generation)),
            );
        } else if self.had_focus {
            // Another window took focus: cancel without restoring.
            self.cancel_popup(false);
        }
    }

    fn accept_popup(&mut self) {
        if self.session == Session::KdeWayland
            && self.config.settings.auto_paste
            && self.picker.is_open()
            && self.had_focus
            && self.mods_any
        {
            // KWin would merge a held modifier into the typed Ctrl+V.
            self.pending_accept = true;
            return;
        }
        let selected = self
            .picker
            .accept()
            .and_then(|i| self.popup_clips.get(i).cloned());
        // Restore focus explicitly: when the popup closes, KWin may activate
        // another window (e.g. one on a different screen) instead.
        self.hide_popup(true);
        if let Some(clip) = selected {
            let target = self.previous.clone();
            self.select_clip(&clip, target);
        }
    }

    fn cancel_popup(&mut self, restore: bool) {
        if !self.picker.cancel() {
            return;
        }
        self.hide_popup(restore);
    }

    fn hide_popup(&mut self, restore: bool) {
        self.deadline_timer.stop();
        self.focus_timer.stop();
        self.pending_accept = false;
        self.extras_pending = false;
        if let Some(extras) = self.extras.as_mut() {
            extras.release();
        }
        let _ = self.popup.hide();
        self.x11_close();
        if self.session == Session::KdeWayland {
            // Slint destroys a hidden Wayland window; build the next one now,
            // while idle, so the next show only maps it. Deferred: this may run
            // inside the old popup's own callback.
            slint::Timer::single_shot(Duration::from_millis(0), || dispatch(Ev::Rebuild));
        }
        if restore
            && let (Some(Target::Kde(id)), Some(kde)) = (&self.previous, &self.kde)
            && kde.active().is_none_or(|w| &w.id != id)
        {
            kde.activate(id);
        }
    }

    fn popup_delete(&mut self) {
        if !self.picker.is_open() {
            return;
        }
        let Some(clip) = self.popup_clips.get(self.picker.index()).cloned() else {
            return;
        };
        self.remove_clip(&clip.id);
        self.popup_clips.remove(self.picker.index());
        self.picker.remove_current();
        self.render();
    }

    fn is_own(&self, id: &str) -> bool {
        self.kde.as_ref().is_some_and(|k| k.is_own_window(id))
    }

    // ----- selection, clipboard, paste -------------------------------------

    /// Copies the clipping and pastes it into `target` if enabled. False if
    /// the copy failed outright (a Wayland publication is reported later).
    fn select_clip(&mut self, clip: &Clip, target: Option<Target>) -> bool {
        self.last_selected = Some(clip.id.clone());
        // Our own write comes back through the watcher: don't re-record it.
        self.ignore_text = Some(clip.text.clone());
        self.paste_job = None;
        let copied = match self.session {
            Session::KdeWayland => self.publisher.set_text(&clip.text),
            Session::X11 => {
                if self.clipboard.is_none() {
                    self.clipboard = arboard::Clipboard::new()
                        .map_err(|e| eprintln!("Jumpkut: clipboard: {e}"))
                        .ok();
                }
                self.clipboard
                    .as_mut()
                    .map_or(Err("the clipboard is unavailable".into()), |c| {
                        c.set_text(clip.text.clone()).map_err(|e| e.to_string())
                    })
            }
        };
        if let Err(e) = copied {
            self.notify(&format!("Could not copy the clipping: {e}."));
            return false;
        }
        let paste = self.config.settings.auto_paste && self.backend_ok;
        match target {
            Some(Target::X11(window)) if paste => {
                if let Some(x) = &self.x11 {
                    x.send(x11::Command::Paste(Some(window)));
                }
            }
            _ if self.session == Session::KdeWayland => {
                // Monitor every publication; paste only when enabled.
                let target = match target {
                    Some(Target::Kde(id))
                        if paste
                            && !self.is_own(&id)
                            && self.injector.as_ref().is_some_and(|i| i.available()) =>
                    {
                        Some(id)
                    }
                    _ => None,
                };
                self.paste_generation += 1;
                let generation = self.paste_generation;
                self.paste_job = Some(PasteJob {
                    generation,
                    target,
                    deadline: Instant::now() + Duration::from_millis(1500),
                    report: false,
                    reactivated: false,
                });
                self.paste_timer.start(
                    slint::TimerMode::Repeated,
                    Duration::from_millis(50),
                    move || dispatch(Ev::PasteTick(generation)),
                );
            }
            _ => {}
        }
        true
    }

    fn history_status(&self, text: &str) {
        if let Some(view) = &self.history_view {
            view.window.set_status(text.into());
        }
    }

    /// One step of the Wayland paste: wait until wl-copy owns the clipboard
    /// and KWin has given focus back to the target, then type the chord.
    fn paste_tick(&mut self, generation: u64) {
        let Some(job) = self
            .paste_job
            .as_ref()
            .filter(|j| j.generation == generation)
        else {
            self.paste_timer.stop();
            return;
        };
        if let Some(message) = self.publisher.poll() {
            self.notify(&message);
        }
        if job.report {
            match self.publisher.state {
                Publish::Done => self.history_status("Copied to clipboard"),
                Publish::Failed => self.history_status("Could not copy the clipping"),
                _ => {}
            }
        }
        let done = |app: &mut App| {
            app.paste_job = None;
            app.paste_timer.stop();
        };
        if Instant::now() > job.deadline {
            if job.target.is_some() && self.publisher.state == Publish::Done {
                eprintln!("Jumpkut: focus did not return to the previous window; not pasting");
                self.notify("Could not paste: focus did not return to the window you came from. The clipping was copied; paste it with Ctrl+V.");
            }
            return done(self);
        }
        // Idle: another copy replaced ours before the paste.
        if matches!(self.publisher.state, Publish::Failed | Publish::Idle) {
            return done(self);
        }
        if self.publisher.state != Publish::Done {
            return;
        }
        let Some(target) = job.target.clone() else {
            return done(self); // published; nothing to paste into
        };
        let Some(active) = self.kde.as_ref().and_then(Kde::active) else {
            return;
        };
        if active.pid == std::process::id().to_string() {
            return; // KWin returns focus asynchronously after the popup hides
        }
        if active.id != target {
            // KWin activated another window when the popup closed: ask once more.
            if let Some(job) = self.paste_job.as_mut()
                && !std::mem::replace(&mut job.reactivated, true)
                && let Some(kde) = &self.kde
            {
                if debug() {
                    eprintln!(
                        "Jumpkut: focus went to {:?}; reactivating the target",
                        active.class
                    );
                }
                kde.activate(&target);
            }
            return;
        }
        let terminal = x11::is_terminal_class(&active.class);
        if debug() {
            eprintln!(
                "Jumpkut: pasting into {:?} (terminal: {terminal})",
                active.class
            );
        }
        let chord = shortcut::parse(if terminal {
            "<Primary><Shift>v"
        } else {
            "<Primary>v"
        })
        .expect("paste chord");
        if chord.qt_key() == self.shortcut.qt_key() {
            // KWin would treat our own typed chord as the shortcut.
            if !std::mem::replace(&mut self.reported_conflict, true) {
                self.notify("Paste automatically is unavailable while the Jumpkut shortcut is the paste shortcut itself. Choose another shortcut in Preferences.");
            }
            return done(self);
        }
        let group = self.kde.as_ref().and_then(Kde::layout).unwrap_or(0);
        if let Some(injector) = self.injector.as_mut()
            && let Err(e) = injector.paste(terminal, group)
        {
            self.notify(&format!(
                "Could not paste: {e}. The clipping was copied; paste it with Ctrl+V."
            ));
        }
        done(self);
    }

    // ----- recording ------------------------------------------------------

    fn record_text(&mut self, text: Option<String>) {
        let ignored = self.ignore_text.take();
        let Some(text) = text else { return };
        if Some(&text) == ignored.as_ref() {
            return;
        }
        if !text.trim().is_empty() {
            self.last_selected = None;
        }
        if let Err(e) = self.history.add(&text) {
            self.notify(&format!(
                "History is available in memory, but could not be saved: {e}"
            ));
        }
        self.history_changed();
    }

    fn capture_current(&mut self) {
        if let Some(tx) = &self.x11_clipboard {
            let _ = tx.send(());
        }
        if let Some((state, text)) = self.last_wayland.clone()
            && !self.paused
        {
            if state == "sensitive" {
                self.ignore_text = None;
            } else if state == "data" {
                self.record_text(text);
            }
        }
    }

    fn remove_clip(&mut self, id: &str) {
        if let Err(e) = self.history.remove(id) {
            self.notify(&e);
        }
        self.history_changed();
    }

    fn history_changed(&mut self) {
        if self
            .last_selected
            .as_ref()
            .is_some_and(|id| self.history.get(id).is_none())
        {
            self.last_selected = None;
        }
        self.update_tray();
        self.refresh_history(false);
    }

    fn pause(&mut self, paused: bool) {
        self.paused = paused;
        if !paused {
            self.capture_current();
        }
        self.update_tray();
    }

    fn tray_action(&mut self, action: tray::Action) {
        if self.picker.is_open() && !matches!(action, tray::Action::Pause(_)) {
            self.cancel_popup(true);
        }
        match action {
            tray::Action::Clip(id) => {
                // Deleted since the menu was built: select nothing else.
                let Some(clip) = self.history.get(&id).cloned() else {
                    return;
                };
                let target = match self.session {
                    Session::KdeWayland => self
                        .kde
                        .as_ref()
                        .and_then(Kde::active)
                        .filter(|w| !self.is_own(&w.id))
                        .map(|w| Target::Kde(w.id)),
                    Session::X11 => self.x11.as_ref().and_then(|x| {
                        let (tx, rx) = std::sync::mpsc::channel();
                        x.send(x11::Command::Active(tx));
                        rx.recv_timeout(Duration::from_millis(500))
                            .ok()
                            .flatten()
                            .map(Target::X11)
                    }),
                };
                self.select_clip(&clip, target);
            }
            tray::Action::FullHistory => self.show_full_history(),
            tray::Action::Preferences => self.show_preferences(),
            tray::Action::About => self.show_about(),
            tray::Action::Pause(paused) => self.pause(paused),
            tray::Action::Backup => dispatch(Ev::Backup(true)),
            tray::Action::Restore => dispatch(Ev::Backup(false)),
            tray::Action::Clear => self.message(
                "Clear all clipboard history?",
                "Saved backups are kept. The current system clipboard is left as it is.",
                false,
                Some(("Clear History", Ev::ClearHistory)),
            ),
            tray::Action::Quit => {
                let _ = slint::quit_event_loop();
            }
        }
    }

    fn backup_chosen(&mut self, save: bool, result: Result<Option<PathBuf>, String>) {
        let path = match result {
            Ok(Some(path)) => path,
            Ok(None) => return,
            Err(e) => {
                return self.message("The file dialog is unavailable.", &e, true, None);
            }
        };
        if save {
            if let Err(e) = self.history.export_to(&path) {
                self.message(&format!("Could not back up history: {e}"), "", true, None);
            }
        } else {
            match self.history.import_from(&path) {
                Ok(count) => {
                    self.history_changed();
                    self.message(
                        &format!("Restored {count} new clippings into full history. Existing clippings were kept."),
                        "",
                        false,
                        None,
                    );
                }
                Err(e) => {
                    self.history_changed();
                    self.message(&format!("Could not restore history: {e}"), "", true, None);
                }
            }
        }
    }

    /// A message, or a confirmation when `confirm` names the OK button and
    /// the event to run on OK.
    fn message(&mut self, heading: &str, body: &str, error: bool, confirm: Option<(&str, Ev)>) {
        let Ok(window) = MessageWindow::new() else {
            return;
        };
        apply_theme!(window, &self.theme);
        self.message_counter += 1;
        let id = self.message_counter;
        window.set_heading(heading.into());
        window.set_body(body.into());
        window.set_error(error);
        let follow_up = confirm.map(|(label, ev)| {
            window.set_confirm(true);
            window.set_ok_text(label.into());
            ev
        });
        window.on_answered(move |ok| dispatch(Ev::MessageAnswered(id, ok)));
        window.window().on_close_requested(move || {
            dispatch(Ev::MessageAnswered(id, false));
            slint::CloseRequestResponse::HideWindow
        });
        let _ = window.show();
        self.messages.push((id, window, follow_up));
    }

    // ----- Full History ----------------------------------------------------

    fn show_full_history(&mut self) {
        if self.picker.is_open() {
            self.cancel_popup(false);
        }
        if self.history_view.is_none() {
            let Ok(window) = HistoryWindow::new() else {
                return;
            };
            apply_theme!(window, &self.theme);
            let rows = Rc::new(VecModel::<HistoryRow>::default());
            window.set_rows(rows.clone().into());
            window.set_status("↑ ↓ browse · Enter or double-click to copy · Ctrl+F search".into());
            window.on_search_changed(|q| dispatch(Ev::HistorySearch(q.into())));
            window.on_selected(|i| dispatch(Ev::HistorySelect(i)));
            window.on_copy(|| dispatch(Ev::HistoryCopy));
            window.on_delete(|| dispatch(Ev::HistoryDelete));
            window.on_backup(|| dispatch(Ev::Backup(true)));
            window.on_restore(|| dispatch(Ev::Backup(false)));
            window.on_close_window(|| dispatch(Ev::HistoryClosed));
            window.window().on_close_requested(|| {
                dispatch(Ev::HistoryClosed);
                slint::CloseRequestResponse::HideWindow
            });
            self.history_view = Some(HistoryView {
                window,
                rows,
                ids: Vec::new(),
                query: String::new(),
                previewed: None,
            });
        }
        self.refresh_history(false);
        if let Some(view) = &self.history_view {
            let _ = view.window.show();
            view.window.invoke_focus_list();
        }
    }

    fn refresh_history(&mut self, reset_selection: bool) {
        let Some(view) = self.history_view.as_mut() else {
            return;
        };
        let query = view.query.to_lowercase();
        let selected = view
            .ids
            .get(view.window.get_current().max(0) as usize)
            .cloned()
            .filter(|_| !reset_selection);
        let total = self.history.len();
        let mut rows = Vec::new();
        let mut ids = Vec::new();
        for (rank, clip) in self.history.items().iter().enumerate() {
            if !query.is_empty() && !clip.text.to_lowercase().contains(&query) {
                continue;
            }
            rows.push(HistoryRow {
                id: clip.id.as_str().into(),
                rank: rank as i32 + 1,
                preview: history::one_line(&clip.text, 240, 1000).into(),
                copied: history::local_date(clip.created_at).into(),
            });
            ids.push(clip.id.clone());
        }
        let current = selected
            .and_then(|id| ids.iter().position(|i| *i == id))
            .unwrap_or(0);
        view.rows.set_vec(rows);
        view.window
            .set_current(if ids.is_empty() { -1 } else { current as i32 });
        view.window.set_count_text(
            if query.is_empty() {
                format!("{total} clippings · newest first")
            } else {
                format!("Showing {} of {total} clippings", ids.len())
            }
            .into(),
        );
        view.ids = ids;
        self.update_history_preview();
    }

    fn history_current(&self) -> Option<Clip> {
        let view = self.history_view.as_ref()?;
        let id = view
            .ids
            .get(usize::try_from(view.window.get_current()).ok()?)?;
        self.history.get(id).cloned()
    }

    fn history_selected(&mut self, _index: i32) {
        self.update_history_preview();
    }

    fn update_history_preview(&mut self) {
        let clip = self.history_current();
        let empty = self.history.is_empty();
        let Some(view) = self.history_view.as_mut() else {
            return;
        };
        view.window.set_has_selection(clip.is_some());
        let content = clip.as_ref().map(|c| (c.id.clone(), c.text.clone()));
        if content.is_none() || content != view.previewed {
            let text = match &clip {
                Some(c) => c.text.clone(),
                None if !empty => "No clippings match your search.\n\nTry another search or clear the search field.".into(),
                None => "Your clipboard history is empty.\n\nCopy text in any application to start your history.".into(),
            };
            view.window.set_preview_text(text.into());
            view.previewed = content;
        }
        view.window
            .set_status("↑ ↓ browse · Enter or double-click to copy · Ctrl+F search".into());
    }

    // ----- Preferences -----------------------------------------------------

    fn show_preferences(&mut self) {
        if self.picker.is_open() {
            self.cancel_popup(false);
        }
        if self.prefs.is_none() {
            let Ok(window) = PreferencesWindow::new() else {
                return;
            };
            apply_theme!(window, &self.theme);
            let s = &self.config.settings;
            window.set_shortcut_label(self.shortcut.label().into());
            window.set_custom_quick(s.custom_quick_limit);
            window.set_quick_limit(s.history_limit as i32);
            window.set_custom_tray(s.custom_tray_limit);
            window.set_tray_limit(s.tray_limit as i32);
            window.set_startup(settings::autostart_enabled());
            window.set_resume(s.resume_last_selection);
            window.set_sticky(s.sticky);
            window.set_paste(s.auto_paste);
            window.set_version(VERSION.into());
            window.on_start_recording(|| dispatch(Ev::PrefsRecording(true)));
            window.on_stop_recording(|| dispatch(Ev::PrefsRecording(false)));
            window.on_record_key(|text, control, alt, shift, meta| {
                dispatch(Ev::PrefsRecord(
                    text.into(),
                    shortcut::Modifiers {
                        control,
                        alt,
                        shift,
                        super_: meta,
                    },
                ));
                true
            });
            window.on_save(|| dispatch(Ev::PrefsSave));
            window.on_cancel(|| dispatch(Ev::PrefsClosed));
            window.on_backup(|| dispatch(Ev::Backup(true)));
            window.on_restore(|| dispatch(Ev::Backup(false)));
            window.window().on_close_requested(|| {
                dispatch(Ev::PrefsClosed);
                slint::CloseRequestResponse::HideWindow
            });
            self.prefs = Some(PrefsView {
                window,
                shortcut: self.config.settings.hotkey.clone(),
                recording: false,
            });
        }
        if let Some(p) = &self.prefs {
            let _ = p.window.show();
        }
    }

    fn set_recorded_shortcut(&mut self, accel: String) {
        if let Some(p) = &mut self.prefs {
            p.recording = false;
            p.window.set_recording(false);
            p.window.set_shortcut_label(
                shortcut::parse(&accel)
                    .map(|s| s.label())
                    .unwrap_or_else(|_| accel.clone())
                    .into(),
            );
            p.shortcut = accel;
        }
    }

    fn save_preferences(&mut self) {
        let Some(p) = &self.prefs else { return };
        if p.recording {
            return self.message(
                "Press a key combination, or Escape to keep the previous shortcut.",
                "",
                true,
                None,
            );
        }
        let w = &p.window;
        let new = Settings {
            hotkey: p.shortcut.clone(),
            history_limit: w.get_quick_limit() as i64,
            tray_limit: w.get_tray_limit() as i64,
            custom_quick_limit: w.get_custom_quick(),
            custom_tray_limit: w.get_custom_tray(),
            sticky: w.get_sticky(),
            auto_paste: w.get_paste(),
            resume_last_selection: w.get_resume(),
            ..self.config.settings.clone()
        };
        let startup = w.get_startup();
        if let Err(e) = new.validate() {
            return self.message(&e, "", true, None);
        }
        let shortcut = match shortcut::parse(&new.hotkey) {
            Ok(s) => s,
            Err(e) => return self.message(&e, "", true, None),
        };
        let old = self.shortcut.clone();
        let rebinding = shortcut != old || !self.backend_ok;
        if rebinding {
            if let Err(e) = self.rebind(&shortcut) {
                let _ = self.rebind(&old);
                return self.message(&e, "", true, None);
            }
            self.backend_ok = true;
        }
        if let Err(e) = self.config.save(new) {
            if rebinding {
                let _ = self.rebind(&old);
            }
            return self.message(&e, "", true, None);
        }
        self.shortcut = shortcut;
        self.x11_sticky.store(
            self.config.settings.sticky,
            std::sync::atomic::Ordering::Relaxed,
        );
        self.prepare_paste();
        self.update_popup_texts();
        self.update_tray();
        if let Some(view) = self.prefs.take() {
            let _ = view.window.hide();
        }
        if let Err(e) = settings::set_autostart(startup) {
            self.message(
                "Preferences were saved, but some changes could not be written.",
                &format!("Could not change startup: {e}"),
                true,
                None,
            );
        }
    }

    fn show_about(&mut self) {
        if self.picker.is_open() {
            self.cancel_popup(false);
        }
        if self.about.is_none() {
            let Ok(window) = AboutWindow::new() else {
                return;
            };
            apply_theme!(window, &self.theme);
            window.set_version(VERSION.into());
            window.on_close_window(|| dispatch(Ev::AboutClosed));
            window.window().on_close_requested(|| {
                dispatch(Ev::AboutClosed);
                slint::CloseRequestResponse::HideWindow
            });
            self.about = Some(window);
        }
        if let Some(a) = &self.about {
            let _ = a.show();
        }
    }

    // ----- popup window plumbing ---------------------------------------------

    fn frame(&mut self) {
        if self.warming {
            slint::Timer::single_shot(Duration::from_millis(0), || dispatch(Ev::WarmupDone));
            return;
        }
        if self.timing.signal.is_some()
            && self.timing.frame.is_none()
            && self.timing.shown.is_some()
        {
            self.timing.frame = Some(Instant::now());
            self.report_timing();
        }
    }

    fn report_timing(&self) {
        if !debug() {
            return;
        }
        let t = &self.timing;
        let (Some(signal), Some(shown)) = (t.signal, t.shown) else {
            return;
        };
        if t.frame.is_none() || (self.session == Session::KdeWayland && t.focus.is_none()) {
            return;
        }
        let ms = |d: Duration| format!("{:.1} ms", d.as_secs_f64() * 1000.0);
        eprintln!(
            "Jumpkut: show #{}: signal→show() {}, →first frame {}{}{}",
            self.shows,
            ms(shown - signal),
            t.frame.map_or("?".into(), |f| ms(f - signal)),
            t.focus.map_or(String::new(), |f| format!(
                ", →keyboard focus {}",
                ms(f - signal)
            )),
            rss_kib().map_or(String::new(), |k| format!(
                " (RSS {:.1} MiB)",
                k as f64 / 1024.0
            )),
        );
    }

    /// X11 keeps the window between shows, but its first draw costs ~30 ms:
    /// pay it at start-up by mapping the popup far off-screen for one frame.
    fn warm_up_x11(&mut self) {
        if self.picker.is_open() {
            return;
        }
        self.popup
            .window()
            .set_position(slint::PhysicalPosition::new(-32000, -32000));
        if self.popup.show().is_ok() {
            self.warming = true;
            self.apply_extras();
            slint::Timer::single_shot(Duration::from_secs(3), || dispatch(Ev::WarmupDone));
        }
    }

    fn center_x11(&mut self) {
        if self.x11_window.is_none() {
            self.x11_window = x11::X11Window::connect().ok();
        }
        let Some((mx, my, mw, mh)) = self.x11_window.as_ref().and_then(|w| w.pointer_monitor())
        else {
            return;
        };
        let scale = self.popup.window().scale_factor();
        let (w, h) = ((POPUP_SIZE.0 * scale) as i32, (POPUP_SIZE.1 * scale) as i32);
        self.popup
            .window()
            .set_position(slint::PhysicalPosition::new(
                mx + (mw - w) / 2,
                my + (mh - h) / 2,
            ));
    }

    fn blur_rects(&self, scale: f32) -> Vec<region::Rect> {
        let p = &self.popup;
        let s = |v: f32| (v * scale).round() as i32;
        region::rounded_rect(
            s(p.get_card_x() + 1.0),
            s(p.get_card_y() + 1.0),
            s(p.get_card_width() - 2.0),
            s(p.get_card_height() - 2.0),
            s(p.get_card_radius()),
        )
    }

    /// Role + blur (Wayland) or the blur property (X11), before the first frame.
    /// Plasma lists every xdg_toplevel, even one that was never mapped. The
    /// popup is built ahead of time for an instant first show, so give it the
    /// skip-taskbar/switcher role now rather than at show time.
    fn prime_extras(&self) {
        if self.session == Session::KdeWayland {
            slint::Timer::single_shot(Duration::from_millis(0), || dispatch(Ev::PrimeExtras(0)));
        }
    }

    fn ensure_wayland_extras(&mut self, display: *mut std::ffi::c_void) {
        if self.extras.is_some() || self.extras_failed {
            return;
        }
        // SAFETY: winit's wl_display lives for the whole event loop.
        match unsafe { crate::blur::SurfaceExtras::new(display) } {
            Ok(extras) => {
                if debug() {
                    eprintln!("Jumpkut: popup surface: {}", extras.describe());
                }
                self.extras = Some(extras);
            }
            Err(e) => {
                self.extras_failed = true;
                eprintln!("Jumpkut: popup surface extras unavailable: {e}");
            }
        }
    }

    /// Skip-taskbar/switcher only, for the hidden popup (see `prime_extras`).
    fn prime_surface(&mut self) {
        use winit::raw_window_handle::{
            HasDisplayHandle, HasWindowHandle, RawDisplayHandle, RawWindowHandle,
        };
        let handles = self.popup.window().with_winit_window(|w| {
            (
                w.window_handle().ok().map(|h| h.as_raw()),
                w.display_handle().ok().map(|h| h.as_raw()),
            )
        });
        let Some((Some(RawWindowHandle::Wayland(w)), Some(RawDisplayHandle::Wayland(d)))) = handles
        else {
            return;
        };
        self.ensure_wayland_extras(d.display.as_ptr());
        if let Some(extras) = self.extras.as_mut() {
            // SAFETY: the surface belongs to the popup's live window.
            if let Err(e) = unsafe { extras.prime(w.surface.as_ptr()) } {
                eprintln!("Jumpkut: could not hide the popup from the taskbar: {e}");
            }
        }
    }

    fn apply_extras(&mut self) {
        use winit::raw_window_handle::{
            HasDisplayHandle, HasWindowHandle, RawDisplayHandle, RawWindowHandle,
        };
        if self.extras_failed {
            return;
        }
        let handles = self.popup.window().with_winit_window(|w| {
            (
                w.window_handle().ok().map(|h| h.as_raw()),
                w.display_handle().ok().map(|h| h.as_raw()),
            )
        });
        let Some((Some(window), Some(display))) = handles else {
            return;
        };
        self.extras_pending = false;
        let result = match (window, display) {
            (RawWindowHandle::Wayland(w), RawDisplayHandle::Wayland(d)) => {
                self.ensure_wayland_extras(d.display.as_ptr());
                let rects = self.blur_rects(1.0);
                match self.extras.as_mut() {
                    // SAFETY: the surface belongs to the window just shown.
                    Some(extras) => unsafe { extras.apply(w.surface.as_ptr(), &rects) },
                    None => Ok(()),
                }
            }
            (RawWindowHandle::Xlib(_) | RawWindowHandle::Xcb(_), _) => {
                let id = match window {
                    RawWindowHandle::Xlib(h) => h.window as u32,
                    RawWindowHandle::Xcb(h) => h.window.get(),
                    _ => 0,
                };
                let rects = self.blur_rects(self.popup.window().scale_factor());
                if self.x11_window.is_none() {
                    self.x11_window = x11::X11Window::connect().ok();
                }
                self.x11_window
                    .as_ref()
                    .map_or(Ok(()), |x| x.set_blur(id, &rects))
            }
            _ => Ok(()),
        };
        if let Err(e) = result {
            self.extras_failed = true;
            eprintln!("Jumpkut: blur unavailable: {e}");
        }
    }

    pub fn shutdown(&mut self) {
        self.picker.cancel();
        let _ = self.popup.hide();
        self.history_view = None;
        self.prefs = None;
        self.about = None;
        if let Some(mut kde) = self.kde.take() {
            kde.close();
        }
        if let Some(tray) = self.tray.take() {
            tray.shutdown().wait();
        }
        if let Err(e) = self.history.close() {
            eprintln!("Jumpkut: History could not be saved before exit: {e}");
        }
        if let Some(dir) = crate::paths::runtime_dir() {
            let _ = std::fs::remove_dir_all(dir);
        }
    }
}

/// The popup component, created before the event loop goes idle so Slint
/// creates its window and renderer in the background.
fn build_popup(theme: &theme::Theme, session: Session) -> Result<Popup, String> {
    let popup = Popup::new().map_err(|e| e.to_string())?;
    apply_theme!(popup, theme);
    popup.on_step(|d| dispatch(Ev::Step(d as i64)));
    popup.on_first(|| dispatch(Ev::First));
    popup.on_last(|| dispatch(Ev::Last));
    popup.on_choose(|| dispatch(Ev::Accept));
    popup.on_dismiss(|| dispatch(Ev::Cancel));
    popup.on_remove(|| dispatch(Ev::PopupDelete));
    popup.window().on_winit_window_event(move |_, event| {
        match event {
            winit::event::WindowEvent::Focused(f) => dispatch(Ev::Focus(*f)),
            winit::event::WindowEvent::ModifiersChanged(m) if session == Session::KdeWayland => {
                let s = m.state();
                let mods = HELD.with(|h| h.get());
                let down = (mods.alt && s.alt_key())
                    || (mods.control && s.control_key())
                    || (mods.shift && s.shift_key())
                    || (mods.super_ && s.super_key());
                let any = s.alt_key() || s.control_key() || s.shift_key() || s.super_key();
                dispatch(Ev::Modifiers { held: down, any });
            }
            winit::event::WindowEvent::Resized(_)
            | winit::event::WindowEvent::ScaleFactorChanged { .. } => dispatch(Ev::WindowReady),
            _ => {}
        }
        EventResult::Propagate
    });
    let _ = popup.window().set_rendering_notifier(|state, _| {
        if matches!(state, slint::RenderingState::AfterRendering) {
            dispatch(Ev::Frame);
        }
    });
    let _ = popup.get_text_warmup();
    Ok(popup)
}
