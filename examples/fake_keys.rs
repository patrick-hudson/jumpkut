//! Test helper: type key sequences into a (nested, test) session.
//!
//!   fake_keys wayland "alt+ v sleep:300 down alt-"   # org_kde_kwin_fake_input
//!   fake_keys x11     "alt+ v sleep:300 down alt-"   # XTEST
//!   fake_keys x11-clipboard                           # print the X11 CLIPBOARD
//!   fake_keys wayland-click X Y                       # move the pointer and left-click
//!   fake_keys x11-target [CLASS]                      # focusable window; pastes on Ctrl+V
//!   fake_keys x11-copy TEXT [--sensitive]             # own CLIPBOARD for 3 s
//!
//! `key+` presses, `key-` releases, `key` taps, `sleep:MS` waits. Keys: alt,
//! ctrl, shift, v, c, up, down, esc, enter. The Wayland mode needs KWin to
//! allow fake input (e.g. `KWIN_WAYLAND_NO_PERMISSION_CHECKS=1` in a nested
//! test compositor); it is meant for isolated test sessions only.

use std::time::Duration;

/// (name, evdev code, X11 keysym)
const KEYS: &[(&str, u32, u32)] = &[
    ("alt", 56, 0xffe9),
    ("ctrl", 29, 0xffe3),
    ("shift", 42, 0xffe1),
    ("v", 47, 0x76),
    ("c", 46, 0x63),
    ("up", 103, 0xff52),
    ("down", 108, 0xff54),
    ("esc", 1, 0xff1b),
    ("enter", 28, 0xff0d),
    ("delete", 111, 0xffff),
    ("f", 33, 0x66),
    ("a", 30, 0x61),
    ("b", 48, 0x62),
    ("e", 18, 0x65),
    ("t", 20, 0x74),
];

enum Step {
    Key(u32, u32, Option<bool>),
    Sleep(u64),
}

fn parse(script: &str) -> Vec<Step> {
    script
        .split_whitespace()
        .map(|token| {
            if let Some(ms) = token.strip_prefix("sleep:") {
                return Step::Sleep(ms.parse().expect("sleep:MS"));
            }
            let (name, state) = match token.as_bytes().last() {
                Some(b'+') => (&token[..token.len() - 1], Some(true)),
                Some(b'-') => (&token[..token.len() - 1], Some(false)),
                _ => (token, None),
            };
            let (_, evdev, sym) = KEYS
                .iter()
                .find(|(n, _, _)| *n == name)
                .unwrap_or_else(|| panic!("key {name}"));
            Step::Key(*evdev, *sym, state)
        })
        .collect()
}

mod wayland {
    use wayland_client::globals::{GlobalListContents, registry_queue_init};
    use wayland_client::protocol::wl_registry;
    use wayland_client::{Connection, Dispatch, QueueHandle, delegate_noop};
    use wayland_protocols_plasma::fake_input::client::org_kde_kwin_fake_input::OrgKdeKwinFakeInput;

    pub struct State;
    impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for State {
        fn event(
            _: &mut Self,
            _: &wl_registry::WlRegistry,
            _: wl_registry::Event,
            _: &GlobalListContents,
            _: &Connection,
            _: &QueueHandle<Self>,
        ) {
        }
    }
    delegate_noop!(State: ignore OrgKdeKwinFakeInput);

    /// Move the pointer to (x, y) in global coordinates and left-click.
    pub fn click(x: f64, y: f64) {
        let conn = Connection::connect_to_env().expect("wayland connection");
        let (globals, mut queue) = registry_queue_init::<State>(&conn).expect("registry");
        let fake: OrgKdeKwinFakeInput = globals
            .bind(&queue.handle(), 4..=5, ())
            .expect("fake input");
        fake.authenticate("jumpkut test".into(), "click the test tray icon".into());
        fake.pointer_motion_absolute(x, y);
        queue.roundtrip(&mut State).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(150));
        fake.button(0x110, 1);
        fake.button(0x110, 0);
        queue.roundtrip(&mut State).unwrap();
    }

    pub fn run(steps: &[super::Step]) {
        let conn = Connection::connect_to_env().expect("wayland connection");
        let (globals, mut queue) = registry_queue_init::<State>(&conn).expect("registry");
        let qh = queue.handle();
        let fake: OrgKdeKwinFakeInput = globals
            .bind(&qh, 4..=5, ())
            .expect("org_kde_kwin_fake_input v4+");
        fake.authenticate("jumpkut test".into(), "key injection for tests".into());
        queue.roundtrip(&mut State).unwrap();
        for step in steps {
            match step {
                super::Step::Sleep(ms) => std::thread::sleep(std::time::Duration::from_millis(*ms)),
                super::Step::Key(code, _, state) => {
                    let states = match state {
                        Some(true) => vec![1],
                        Some(false) => vec![0],
                        None => vec![1, 0],
                    };
                    for s in states {
                        fake.keyboard_key(*code, s);
                        queue.roundtrip(&mut State).unwrap();
                        std::thread::sleep(std::time::Duration::from_millis(15));
                    }
                }
            }
        }
        queue.roundtrip(&mut State).unwrap();
    }
}

fn x11(steps: &[Step]) {
    use x11rb::connection::Connection;
    use x11rb::protocol::xproto::ConnectionExt;
    use x11rb::protocol::xtest::ConnectionExt as _;
    let (conn, screen) = x11rb::connect(None).expect("X11 connection");
    let root = conn.setup().roots[screen].root;
    let (min, max) = (conn.setup().min_keycode, conn.setup().max_keycode);
    let map = conn
        .get_keyboard_mapping(min, max - min + 1)
        .unwrap()
        .reply()
        .unwrap();
    let per = map.keysyms_per_keycode as usize;
    let code_for = |sym: u32| -> u8 {
        map.keysyms
            .chunks(per)
            .position(|syms| syms.contains(&sym))
            .map(|i| min + i as u8)
            .expect("keycode")
    };
    for step in steps {
        match step {
            Step::Sleep(ms) => std::thread::sleep(Duration::from_millis(*ms)),
            Step::Key(_, sym, state) => {
                let code = code_for(*sym);
                let events = match state {
                    Some(true) => vec![2u8],
                    Some(false) => vec![3u8],
                    None => vec![2u8, 3u8],
                };
                for t in events {
                    conn.xtest_fake_input(t, code, 0, root, 0, 0, 0).unwrap();
                    conn.flush().unwrap();
                    std::thread::sleep(Duration::from_millis(15));
                }
            }
        }
    }
    x11rb::wrapper::ConnectionExt::sync(&conn).unwrap();
}

fn atom(conn: &impl x11rb::connection::Connection, name: &str) -> u32 {
    use x11rb::protocol::xproto::ConnectionExt;
    conn.intern_atom(false, name.as_bytes())
        .unwrap()
        .reply()
        .unwrap()
        .atom
}

/// A focusable X11 window (WM_CLASS from `class`) that prints its keys and,
/// on Ctrl+V / Ctrl+Shift+V, pastes CLIPBOARD and prints the text.
fn x11_target(class: &str) {
    use x11rb::connection::Connection;
    use x11rb::protocol::Event;
    use x11rb::protocol::xproto::*;
    use x11rb::wrapper::ConnectionExt as _;
    let (conn, screen) = x11rb::connect(None).expect("X11 connection");
    let screen = &conn.setup().roots[screen];
    let window = conn.generate_id().unwrap();
    conn.create_window(
        0,
        window,
        screen.root,
        100,
        100,
        500,
        300,
        0,
        WindowClass::INPUT_OUTPUT,
        0,
        &CreateWindowAux::new()
            .background_pixel(0x3daee9)
            .event_mask(
                EventMask::KEY_PRESS
                    | EventMask::STRUCTURE_NOTIFY
                    | EventMask::FOCUS_CHANGE
                    | EventMask::PROPERTY_CHANGE,
            ),
    )
    .unwrap();
    let wm_class = format!("{class}\0{class}\0");
    conn.change_property8(
        PropMode::REPLACE,
        window,
        AtomEnum::WM_CLASS,
        AtomEnum::STRING,
        wm_class.as_bytes(),
    )
    .unwrap();
    conn.map_window(window).unwrap();
    conn.flush().unwrap();
    let (clipboard, utf8, prop) = (
        atom(&conn, "CLIPBOARD"),
        atom(&conn, "UTF8_STRING"),
        atom(&conn, "TARGET_PASTE"),
    );
    let net_active = atom(&conn, "_NET_ACTIVE_WINDOW");
    let (min, max) = (conn.setup().min_keycode, conn.setup().max_keycode);
    let map = conn
        .get_keyboard_mapping(min, max - min + 1)
        .unwrap()
        .reply()
        .unwrap();
    let per = map.keysyms_per_keycode as usize;
    let v_code = map
        .keysyms
        .chunks(per)
        .position(|s| s.contains(&0x76))
        .map(|i| min + i as u8)
        .unwrap();
    use std::io::Write;
    loop {
        match conn.wait_for_event() {
            Ok(Event::MapNotify(_)) => {
                // No window manager: take focus and publish ourselves as active.
                conn.set_input_focus(InputFocus::PARENT, window, x11rb::CURRENT_TIME)
                    .unwrap();
                conn.change_property32(
                    PropMode::REPLACE,
                    screen.root,
                    net_active,
                    AtomEnum::WINDOW,
                    &[window],
                )
                .unwrap();
                conn.flush().unwrap();
                println!("mapped and focused");
            }
            Ok(Event::KeyPress(e)) => {
                let state = u16::from(e.state);
                println!("press {} state {state:#x}", e.detail);
                if e.detail == v_code && state & 4 != 0 {
                    conn.convert_selection(window, clipboard, utf8, prop, e.time)
                        .unwrap();
                    conn.flush().unwrap();
                    println!("paste requested (shift={})", state & 1 != 0);
                }
            }
            Ok(Event::SelectionNotify(n)) if n.property != 0 => {
                let reply = conn
                    .get_property(true, window, prop, AtomEnum::ANY, 0, u32::MAX / 4)
                    .unwrap()
                    .reply()
                    .unwrap();
                println!("pasted {:?}", String::from_utf8_lossy(&reply.value));
            }
            Ok(_) => {}
            Err(_) => break,
        }
        let _ = std::io::stdout().flush();
    }
}

/// Own CLIPBOARD with `text` for `seconds` (optionally flagged as a
/// password-manager copy, like KeePassXC/KDE do).
fn x11_copy(text: &str, sensitive: bool, seconds: u64) {
    use x11rb::connection::Connection;
    use x11rb::protocol::Event;
    use x11rb::protocol::xproto::*;
    let (conn, screen) = x11rb::connect(None).expect("X11 connection");
    let root = conn.setup().roots[screen].root;
    let window = conn.generate_id().unwrap();
    conn.create_window(
        0,
        window,
        root,
        0,
        0,
        1,
        1,
        0,
        WindowClass::INPUT_ONLY,
        0,
        &CreateWindowAux::new(),
    )
    .unwrap();
    let (clipboard, targets, utf8, hint) = (
        atom(&conn, "CLIPBOARD"),
        atom(&conn, "TARGETS"),
        atom(&conn, "UTF8_STRING"),
        atom(&conn, "x-kde-passwordManagerHint"),
    );
    conn.set_selection_owner(window, clipboard, x11rb::CURRENT_TIME)
        .unwrap();
    conn.flush().unwrap();
    let deadline = std::time::Instant::now() + Duration::from_secs(seconds);
    while std::time::Instant::now() < deadline {
        let Ok(Some(event)) = conn.poll_for_event() else {
            std::thread::sleep(Duration::from_millis(10));
            continue;
        };
        if let Event::SelectionRequest(r) = event {
            let property = if r.property == 0 {
                r.target
            } else {
                r.property
            };
            if r.target == targets {
                let mut list = vec![targets, utf8];
                if sensitive {
                    list.push(hint);
                }
                x11rb::wrapper::ConnectionExt::change_property32(
                    &conn,
                    PropMode::REPLACE,
                    r.requestor,
                    property,
                    AtomEnum::ATOM,
                    &list,
                )
                .unwrap();
            } else if r.target == utf8 {
                x11rb::wrapper::ConnectionExt::change_property8(
                    &conn,
                    PropMode::REPLACE,
                    r.requestor,
                    property,
                    utf8,
                    text.as_bytes(),
                )
                .unwrap();
            } else if r.target == hint {
                x11rb::wrapper::ConnectionExt::change_property8(
                    &conn,
                    PropMode::REPLACE,
                    r.requestor,
                    property,
                    hint,
                    b"secret",
                )
                .unwrap();
            }
            let notify = SelectionNotifyEvent {
                response_type: SELECTION_NOTIFY_EVENT,
                sequence: 0,
                time: r.time,
                requestor: r.requestor,
                selection: r.selection,
                target: r.target,
                property,
            };
            conn.send_event(false, r.requestor, EventMask::NO_EVENT, notify)
                .unwrap();
            conn.flush().unwrap();
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("wayland") => wayland::run(&parse(args.get(1).map_or("", String::as_str))),
        Some("wayland-click") => wayland::click(args[1].parse().unwrap(), args[2].parse().unwrap()),
        Some("x11") => x11(&parse(args.get(1).map_or("", String::as_str))),
        Some("x11-clipboard") => {
            let mut clipboard = arboard::Clipboard::new().expect("clipboard");
            println!(
                "{}",
                clipboard
                    .get_text()
                    .unwrap_or_else(|e| format!("<error: {e}>"))
            );
        }
        Some("x11-target") => x11_target(args.get(1).map_or("jktarget", String::as_str)),
        Some("x11-copy") => x11_copy(
            args.get(1).map_or("", String::as_str),
            args.iter().any(|a| a == "--sensitive"),
            3,
        ),
        _ => eprintln!("usage: fake_keys wayland|x11 SCRIPT | x11-clipboard | x11-target"),
    }
}
