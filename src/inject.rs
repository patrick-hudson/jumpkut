//! Typing the paste chord on KDE Wayland, in this order:
//!
//! 1. `org_kde_kwin_fake_input` (KWin's own protocol): no prompt, no notice.
//!    KWin 6.8 offers it to every unsandboxed native client; older KWin only
//!    to executables whose desktop file lists it in `X-KDE-Wayland-Interfaces`.
//! 2. A `/dev/uinput` virtual keyboard: needs write access to `/dev/uinput`
//!    (a `uaccess` udev rule), no prompt.
//! 3. The RemoteDesktop portal: asks once, then shows KDE's "remote control"
//!    notice while the session lives. Last resort.
//!
//! Both 1 and 2 send evdev keycodes, which depend on the keyboard layout, so
//! the keys that produce Control_L / Shift_L / `v` are looked up in the
//! compositor's current XKB keymap (e.g. Dvorak's V is not KEY_V).

use std::fs::File;
use std::io::{Read, Seek, Write};
use std::os::fd::{AsRawFd, OwnedFd};

use wayland_client::globals::{GlobalListContents, registry_queue_init};
use wayland_client::protocol::{wl_keyboard, wl_registry, wl_seat};
use wayland_client::{Connection, Dispatch, EventQueue, QueueHandle, WEnum};
use wayland_protocols_plasma::fake_input::client::org_kde_kwin_fake_input::OrgKdeKwinFakeInput;

const KEY_LEFTCTRL: u32 = 29;
const KEY_LEFTSHIFT: u32 = 42;
const KEY_V: u32 = 47;
const XK_CONTROL_L: u32 = 0xffe3;
const XK_SHIFT_L: u32 = 0xffe1;
const XK_V: u32 = 0x76;

#[derive(Default)]
struct State {
    keymap: Option<String>,
}

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
impl Dispatch<wl_seat::WlSeat, ()> for State {
    fn event(
        _: &mut Self,
        _: &wl_seat::WlSeat,
        _: wl_seat::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}
impl Dispatch<wl_keyboard::WlKeyboard, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_keyboard::WlKeyboard,
        event: wl_keyboard::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_keyboard::Event::Keymap {
            format: WEnum::Value(wl_keyboard::KeymapFormat::XkbV1),
            fd,
            size,
        } = event
        {
            state.keymap = read_keymap(fd, size as usize);
        }
    }
}
wayland_client::delegate_noop!(State: ignore OrgKdeKwinFakeInput);

fn read_keymap(fd: OwnedFd, size: usize) -> Option<String> {
    let mut file = File::from(fd);
    file.rewind().ok()?;
    let mut bytes = vec![0u8; size];
    file.read_exact(&mut bytes).ok()?;
    let end = bytes.iter().position(|b| *b == 0).unwrap_or(bytes.len());
    String::from_utf8(bytes[..end].to_vec()).ok()
}

/// Evdev codes that produce Control_L, Shift_L and `v` in `keymap` (first
/// layout that has them; Latin fallback for non-Latin layouts).
pub fn chord_codes(keymap: Option<&str>) -> (u32, u32, u32) {
    let defaults = (KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V);
    let (Some(text), Some(xkb)) = (keymap, xkbcommon_dl::xkbcommon_option()) else {
        return defaults;
    };
    let Ok(source) = std::ffi::CString::new(text) else {
        return defaults;
    };
    // SAFETY: plain libxkbcommon calls on objects created and freed here.
    unsafe {
        let context = (xkb.xkb_context_new)(xkbcommon_dl::xkb_context_flags::XKB_CONTEXT_NO_FLAGS);
        if context.is_null() {
            return defaults;
        }
        let keymap = (xkb.xkb_keymap_new_from_string)(
            context,
            source.as_ptr(),
            xkbcommon_dl::xkb_keymap_format::XKB_KEYMAP_FORMAT_TEXT_V1,
            xkbcommon_dl::xkb_keymap_compile_flags::XKB_KEYMAP_COMPILE_NO_FLAGS,
        );
        let mut found = [None; 3];
        if !keymap.is_null() {
            let (min, max) = (
                (xkb.xkb_keymap_min_keycode)(keymap),
                (xkb.xkb_keymap_max_keycode)(keymap),
            );
            for (slot, wanted) in [XK_CONTROL_L, XK_SHIFT_L, XK_V].iter().enumerate() {
                'search: for layout in 0..4u32 {
                    for code in min..=max {
                        if code < 8 || layout >= (xkb.xkb_keymap_num_layouts_for_key)(keymap, code)
                        {
                            continue;
                        }
                        let mut syms: *const u32 = std::ptr::null();
                        let n = (xkb.xkb_keymap_key_get_syms_by_level)(
                            keymap, code, layout, 0, &mut syms,
                        );
                        if n > 0 && std::slice::from_raw_parts(syms, n as usize).contains(wanted) {
                            found[slot] = Some(code - 8);
                            break 'search;
                        }
                    }
                }
            }
            (xkb.xkb_keymap_unref)(keymap);
        }
        (xkb.xkb_context_unref)(context);
        (
            found[0].unwrap_or(defaults.0),
            found[1].unwrap_or(defaults.1),
            found[2].unwrap_or(defaults.2),
        )
    }
}

/// A native Wayland client connection: fake input (if offered) + keymap.
struct Wayland {
    queue: EventQueue<State>,
    state: State,
    fake: Option<OrgKdeKwinFakeInput>,
    _keyboard: Option<wl_keyboard::WlKeyboard>,
}

impl Wayland {
    fn connect() -> Result<Self, String> {
        let conn = Connection::connect_to_env().map_err(|e| e.to_string())?;
        let (globals, mut queue) =
            registry_queue_init::<State>(&conn).map_err(|e| e.to_string())?;
        let qh = queue.handle();
        let fake: Option<OrgKdeKwinFakeInput> = globals.bind(&qh, 4..=5, ()).ok();
        if let Some(fake) = &fake {
            fake.authenticate(
                "Jumpkut".into(),
                "Paste the clipping you picked from your clipboard history".into(),
            );
        }
        let keyboard = globals
            .bind::<wl_seat::WlSeat, _, _>(&qh, 1..=7, ())
            .ok()
            .map(|seat| seat.get_keyboard(&qh, ()));
        let mut state = State::default();
        queue.roundtrip(&mut state).map_err(|e| e.to_string())?;
        Ok(Wayland {
            queue,
            state,
            fake,
            _keyboard: keyboard,
        })
    }

    fn codes(&mut self) -> (u32, u32, u32) {
        let _ = self.queue.roundtrip(&mut self.state);
        chord_codes(self.state.keymap.as_deref())
    }

    fn tap(&mut self, codes: &[u32]) -> Result<(), String> {
        let fake = self.fake.as_ref().ok_or("no fake input")?;
        for code in codes {
            fake.keyboard_key(*code, 1);
        }
        for code in codes.iter().rev() {
            fake.keyboard_key(*code, 0);
        }
        self.queue
            .roundtrip(&mut self.state)
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
}

/// A minimal `/dev/uinput` virtual keyboard.
pub struct Uinput {
    file: File,
}

const UI_SET_EVBIT: libc::c_ulong = 0x4004_5564;
const UI_SET_KEYBIT: libc::c_ulong = 0x4004_5565;
const UI_DEV_SETUP: libc::c_ulong = 0x405c_5503;
const UI_DEV_CREATE: libc::c_ulong = 0x5501;
const UI_DEV_DESTROY: libc::c_ulong = 0x5502;
const EV_SYN: u16 = 0;
const EV_KEY: u16 = 1;

#[repr(C)]
struct UinputSetup {
    bustype: u16,
    vendor: u16,
    product: u16,
    version: u16,
    name: [u8; 80],
    ff_effects_max: u32,
}

#[repr(C)]
struct InputEvent {
    time: libc::timeval,
    kind: u16,
    code: u16,
    value: i32,
}

impl Uinput {
    pub fn create() -> Result<Self, String> {
        let file = std::fs::OpenOptions::new()
            .write(true)
            .custom_flags(libc::O_NONBLOCK)
            .open("/dev/uinput")
            .map_err(|e| format!("/dev/uinput: {e}"))?;
        let fd = file.as_raw_fd();
        let mut setup = UinputSetup {
            bustype: 0x06, /* BUS_VIRTUAL */
            vendor: 0x4a4b,
            product: 0x0001,
            version: 1,
            name: [0; 80],
            ff_effects_max: 0,
        };
        let name = b"Jumpkut paste keyboard";
        setup.name[..name.len()].copy_from_slice(name);
        // SAFETY: ioctls on our own uinput fd with correctly sized arguments.
        unsafe {
            if libc::ioctl(fd, UI_SET_EVBIT, EV_KEY as libc::c_int) < 0 {
                return Err(std::io::Error::last_os_error().to_string());
            }
            for code in 1..=248 {
                libc::ioctl(fd, UI_SET_KEYBIT, code as libc::c_int);
            }
            if libc::ioctl(fd, UI_DEV_SETUP, &setup as *const UinputSetup) < 0
                || libc::ioctl(fd, UI_DEV_CREATE) < 0
            {
                return Err(std::io::Error::last_os_error().to_string());
            }
        }
        Ok(Uinput { file })
    }

    fn emit(&mut self, kind: u16, code: u16, value: i32) -> std::io::Result<()> {
        let event = InputEvent {
            time: libc::timeval {
                tv_sec: 0,
                tv_usec: 0,
            },
            kind,
            code,
            value,
        };
        // SAFETY: InputEvent is a plain repr(C) struct.
        let bytes = unsafe {
            std::slice::from_raw_parts(
                &event as *const InputEvent as *const u8,
                std::mem::size_of::<InputEvent>(),
            )
        };
        self.file.write_all(bytes)
    }

    fn tap(&mut self, codes: &[u32]) -> Result<(), String> {
        let press = |this: &mut Self, code: u32, value: i32| -> std::io::Result<()> {
            this.emit(EV_KEY, code as u16, value)?;
            this.emit(EV_SYN, 0, 0)
        };
        for code in codes {
            press(self, *code, 1).map_err(|e| e.to_string())?;
        }
        for code in codes.iter().rev() {
            press(self, *code, 0).map_err(|e| e.to_string())?;
        }
        Ok(())
    }
}

impl Drop for Uinput {
    fn drop(&mut self) {
        // SAFETY: destroying the device we created.
        unsafe { libc::ioctl(self.file.as_raw_fd(), UI_DEV_DESTROY) };
    }
}

use std::os::unix::fs::OpenOptionsExt;

pub struct Injector {
    wayland: Option<Wayland>,
    uinput: Option<Uinput>,
    portal: Option<crate::portal::RemoteKeyboard>,
    allow_portal: bool,
    reported: Option<&'static str>,
}

impl Injector {
    /// Prepare the first method that is available. The uinput device and
    /// the portal session are created now, so the first paste is not late.
    pub fn new(allow_portal: bool) -> Self {
        let wayland = Wayland::connect()
            .map_err(|e| eprintln!("Jumpkut: Wayland connection for paste: {e}"))
            .ok();
        let mut injector = Injector {
            wayland,
            uinput: None,
            portal: None,
            allow_portal,
            reported: None,
        };
        // JUMPKUT_PASTE_METHOD=uinput|portal skips the earlier methods (testing).
        let forced = std::env::var("JUMPKUT_PASTE_METHOD").unwrap_or_default();
        if (forced == "uinput" || forced == "portal")
            && let Some(w) = injector.wayland.as_mut()
        {
            w.fake = None;
        }
        if forced == "portal" {
            eprintln!("Jumpkut: paste via the RemoteDesktop portal (forced)");
            injector.portal = Some(crate::portal::RemoteKeyboard::start());
            return injector;
        }
        if injector.wayland.as_ref().is_some_and(|w| w.fake.is_some()) {
            eprintln!("Jumpkut: paste via KWin fake input (org_kde_kwin_fake_input)");
            return injector;
        }
        match Uinput::create() {
            Ok(device) => {
                eprintln!(
                    "Jumpkut: KWin fake input is unavailable; paste via a /dev/uinput keyboard"
                );
                injector.uinput = Some(device);
            }
            Err(e) if allow_portal => {
                eprintln!(
                    "Jumpkut: no fake input and no uinput ({e}); paste via the RemoteDesktop portal"
                );
                injector.portal = Some(crate::portal::RemoteKeyboard::start());
            }
            Err(e) => eprintln!("Jumpkut: no paste method available ({e})"),
        }
        injector
    }

    pub fn available(&self) -> bool {
        self.wayland.as_ref().is_some_and(|w| w.fake.is_some())
            || self.uinput.is_some()
            || self.portal.is_some()
    }

    /// Type Ctrl+V, or Ctrl+Shift+V when `terminal`.
    pub fn paste(&mut self, terminal: bool) -> Result<&'static str, String> {
        let (control, shift, v) = self
            .wayland
            .as_mut()
            .map_or((KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V), Wayland::codes);
        let codes: Vec<u32> = if terminal {
            vec![control, shift, v]
        } else {
            vec![control, v]
        };
        let method = if let Some(w) = self.wayland.as_mut().filter(|w| w.fake.is_some()) {
            w.tap(&codes).map(|()| "KWin fake input")
        } else if let Some(device) = self.uinput.as_mut() {
            device.tap(&codes).map(|()| "uinput")
        } else if let Some(portal) = &self.portal {
            let syms: Vec<i32> = if terminal {
                vec![XK_CONTROL_L as i32, XK_SHIFT_L as i32, XK_V as i32]
            } else {
                vec![XK_CONTROL_L as i32, XK_V as i32]
            };
            portal.tap(syms).map(|()| "RemoteDesktop portal")
        } else {
            Err("no paste method is available".into())
        }?;
        if self.reported != Some(method) {
            self.reported = Some(method);
            eprintln!("Jumpkut: pasted via {method}");
        }
        let _ = self.allow_portal;
        Ok(method)
    }
}
