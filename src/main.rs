//! Jumpkut — keyboard-first clipboard history for X11 and KDE Plasma Wayland,
//! in Rust + Slint. Same data files, settings and behaviour as the Python app.

mod app;
mod blur;
mod history;
mod inject;
mod instance;
mod kde;
mod paths;
mod picker;
mod portal;
mod pyjson;
mod region;
mod settings;
mod shortcut;
mod theme;
mod tray;
mod x11;

pub mod ui {
    slint::include_modules!();
}

use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::os::unix::process::CommandExt;
use std::time::Duration;

use app::{Ev, Session, VERSION};

const HELP: &str = "\
usage: jumpkut [-h] [--version] [--background | --daemon | --show | --history | --preferences | --about | --quit]

Jumpkut — clipboard history for Linux/X11

options:
  -h, --help     show this help message and exit
  --version      show program's version number and exit
  --background   start in the tray and detach from the terminal
  --daemon       start in the tray, attached to the terminal
  --show         show the running history popup
  --history      open the full history browser
  --preferences  open preferences
  --about        show the application version and credits
  --quit         quit the running instance

Without options, Jumpkut starts in the background system tray.
";

fn parse_command() -> Result<String, String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut command: Option<&str> = None;
    for arg in &args {
        let name = match arg.as_str() {
            "-h" | "--help" => {
                print!("{HELP}");
                std::process::exit(0);
            }
            "--version" => {
                println!("Jumpkut {VERSION}");
                std::process::exit(0);
            }
            "--background" => "background",
            "--daemon" => "daemon",
            "--show" => "show",
            "--history" => "history",
            "--preferences" => "preferences",
            "--about" => "about",
            "--quit" => "quit",
            other => return Err(format!("unrecognized arguments: {other}")),
        };
        if let Some(previous) = command
            && previous != name
        {
            return Err(format!(
                "argument --{name}: not allowed with argument --{previous}"
            ));
        }
        command = Some(name);
    }
    Ok(command.unwrap_or("background").to_owned())
}

fn detect_session() -> Result<Session, String> {
    let kind = std::env::var("XDG_SESSION_TYPE").unwrap_or_default();
    if kind == "wayland" {
        if let Some(problem) = kde::wayland_problem() {
            return Err(problem);
        }
        return Ok(Session::KdeWayland);
    }
    if std::env::var_os("DISPLAY").is_none() {
        return Err("Cannot open the X11 display. Run Jumpkut inside your desktop session.".into());
    }
    Ok(Session::X11)
}

/// `--background`: start `--daemon` detached, logging to the state directory.
fn start_background() -> i32 {
    let path = paths::log_path();
    let result = (|| -> std::io::Result<()> {
        let dir = path.parent().expect("log directory");
        std::fs::DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(dir)?;
        std::fs::set_permissions(dir, std::os::unix::fs::PermissionsExt::from_mode(0o700))?;
        let log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .mode(0o600)
            .open(&path)?;
        let exe = std::env::var_os("JUMPKUT_LAUNCHER")
            .map(std::path::PathBuf::from)
            .unwrap_or(std::env::current_exe()?);
        let mut command = std::process::Command::new(exe);
        command
            .arg("--daemon")
            .stdin(std::process::Stdio::null())
            .stdout(log.try_clone()?)
            .stderr(log);
        // SAFETY: setsid is async-signal-safe.
        unsafe {
            command.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
        command.spawn().map(|_| ())
    })();
    match result {
        Ok(()) => {
            println!(
                "Jumpkut background start requested. Log: {}",
                path.display()
            );
            0
        }
        Err(e) => {
            eprintln!("Cannot start Jumpkut in the background: {e}");
            1
        }
    }
}

/// SIGINT/SIGTERM/SIGHUP quit the event loop, so shutdown runs.
fn install_signal_handlers() {
    static mut PIPE: [libc::c_int; 2] = [-1, -1];
    extern "C" fn on_signal(_: libc::c_int) {
        // SAFETY: write(2) is async-signal-safe; PIPE is set before handlers.
        unsafe {
            let fd = PIPE[1];
            libc::write(fd, [1u8].as_ptr().cast(), 1);
        }
    }
    // SAFETY: plain libc calls; PIPE is written once before handlers exist.
    let read_fd = unsafe {
        let mut fds = [0; 2];
        if libc::pipe(fds.as_mut_ptr()) != 0 {
            return;
        }
        PIPE = fds;
        for signal in [libc::SIGINT, libc::SIGTERM, libc::SIGHUP] {
            libc::signal(signal, on_signal as *const () as libc::sighandler_t);
        }
        fds[0]
    };
    std::thread::spawn(move || {
        let mut byte = [0u8; 1];
        // SAFETY: reading into a valid one-byte buffer.
        if unsafe { libc::read(read_fd, byte.as_mut_ptr().cast(), 1) } > 0 {
            app::send(Ev::Quit);
        }
    });
}

fn main() {
    let command = match parse_command() {
        Ok(c) => c,
        Err(e) => {
            eprint!("{}", HELP.lines().next().unwrap_or_default());
            eprintln!("\njumpkut: error: {e}");
            std::process::exit(2);
        }
    };
    std::process::exit(run(&command));
}

fn run(command: &str) -> i32 {
    let session = match detect_session() {
        Ok(s) => s,
        Err(e) => {
            eprintln!("{e}");
            return 1;
        }
    };
    let conn = match instance::connect() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("{e}");
            return 1;
        }
    };
    let forwarded = if command == "background" {
        "daemon"
    } else {
        command
    };
    match instance::forward(&conn, forwarded) {
        Ok(instance::Owner::Forwarded) => return 0,
        Ok(instance::Owner::Foreign(pid)) => {
            eprintln!("{}", instance::python_message(pid));
            return 1;
        }
        Ok(instance::Owner::None) => {}
        Err(e) => {
            eprintln!("Jumpkut: {e}");
            return 1;
        }
    }
    match command {
        "background" => return start_background(),
        "quit" => return 0,
        _ => {}
    }

    // The primary instance.
    let mut selector = slint::BackendSelector::new().backend_name("winit".into());
    if let Ok(renderer) = std::env::var("JUMPKUT_RENDERER") {
        selector = selector.renderer_name(renderer);
    }
    if session == Session::X11 {
        // Override-redirect popup: the WM never moves focus to it, so the
        // paste goes straight back to the app that had focus.
        selector = selector.with_winit_window_attributes_hook(|attributes| {
            use slint::winit_030::winit::platform::x11::{WindowAttributesExtX11, WindowType};
            if attributes.title == app::POPUP_TITLE {
                attributes
                    .with_override_redirect(true)
                    .with_x11_window_type(vec![WindowType::PopupMenu])
            } else {
                attributes
            }
        });
    }
    if let Err(e) = selector.select() {
        eprintln!("Jumpkut: cannot start the user interface: {e}");
        return 1;
    }
    let _ = slint::set_xdg_app_id(app::app_id());
    match instance::claim(&conn, |command| app::send(Ev::Command(command))) {
        Ok(true) => {}
        Ok(false) => {
            // Lost a start-up race: hand the command to the winner.
            return match instance::forward(&conn, forwarded) {
                Ok(instance::Owner::Forwarded) => 0,
                Ok(instance::Owner::Foreign(pid)) => {
                    eprintln!("{}", instance::python_message(pid));
                    1
                }
                _ => 1,
            };
        }
        Err(e) => {
            eprintln!("Jumpkut: {e}");
            return 1;
        }
    }

    let application = match app::App::new(session, theme::Theme::load()) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("Jumpkut: {e}");
            return 1;
        }
    };
    app::APP.with(|a| *a.borrow_mut() = Some(application));
    install_signal_handlers();
    if command != "daemon" {
        let command = command.to_owned();
        slint::Timer::single_shot(Duration::from_millis(0), move || {
            app::dispatch(Ev::Command(command))
        });
    }
    if std::env::var_os("JUMPKUT_DEBUG").is_some()
        && let Some(rss) = app::rss_kib()
    {
        eprintln!(
            "Jumpkut {VERSION}: ready (RSS {:.1} MiB)",
            rss as f64 / 1024.0
        );
    }
    let result = slint::run_event_loop_until_quit();
    app::APP.with(|a| {
        if let Some(mut application) = a.borrow_mut().take() {
            application.shutdown();
        }
    });
    let _ = std::io::stderr().flush();
    drop(conn);
    if let Err(e) = result {
        eprintln!("Jumpkut: {e}");
        return 1;
    }
    0
}
