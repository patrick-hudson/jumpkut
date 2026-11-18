//! Single instance over D-Bus, like the Python app's GApplication: the first
//! process owns `io.github.jumpkut.Clipboard` and later ones forward their
//! command (`show`, `history`, ...) to it. If the owner is the *Python*
//! Jumpkut (it does not answer our interface), this app refuses to start, so
//! the two never write the same history.

use zbus::blocking::Connection;
use zbus::fdo::{RequestNameFlags, RequestNameReply};

const PATH: &str = "/io/github/jumpkut/Remote";
const INTERFACE: &str = "io.github.jumpkut.Remote";

pub fn bus_name() -> String {
    std::env::var("JUMPKUT_BUS_NAME")
        .ok()
        .filter(|n| !n.is_empty())
        .unwrap_or_else(|| "io.github.jumpkut.Clipboard".into())
}

struct Remote(Box<dyn Fn(String) + Send + Sync>);

#[zbus::interface(name = "io.github.jumpkut.Remote")]
impl Remote {
    fn command(&self, name: String) {
        (self.0)(name);
    }
}

pub enum Owner {
    /// Nobody runs Jumpkut.
    None,
    /// Another Rust instance took the command.
    Forwarded,
    /// The Python app (or something else) owns the name.
    Foreign(Option<u32>),
}

pub fn connect() -> Result<Connection, String> {
    Connection::session().map_err(|e| format!("Cannot reach the D-Bus session bus: {e}"))
}

/// Send `command` to a running instance, if there is one.
pub fn forward(conn: &Connection, command: &str) -> Result<Owner, String> {
    let name = bus_name();
    let dbus = zbus::blocking::fdo::DBusProxy::new(conn).map_err(|e| e.to_string())?;
    let bus_name = zbus::names::BusName::try_from(name.as_str()).map_err(|e| e.to_string())?;
    if !dbus
        .name_has_owner(bus_name.clone())
        .map_err(|e| e.to_string())?
    {
        return Ok(Owner::None);
    }
    match conn.call_method(
        Some(name.as_str()),
        PATH,
        Some(INTERFACE),
        "Command",
        &(command,),
    ) {
        Ok(_) => Ok(Owner::Forwarded),
        Err(_) => {
            let pid = dbus.get_connection_unix_process_id(bus_name).ok();
            Ok(Owner::Foreign(pid))
        }
    }
}

/// Become the primary instance: serve commands and own the name.
pub fn claim(
    conn: &Connection,
    on_command: impl Fn(String) + Send + Sync + 'static,
) -> Result<bool, String> {
    conn.object_server()
        .at(PATH, Remote(Box::new(on_command)))
        .map_err(|e| e.to_string())?;
    let name = bus_name();
    let well_known =
        zbus::names::WellKnownName::try_from(name.as_str()).map_err(|e| e.to_string())?;
    let reply = zbus::blocking::fdo::DBusProxy::new(conn)
        .map_err(|e| e.to_string())?
        .request_name(well_known, RequestNameFlags::DoNotQueue.into())
        .map_err(|e| e.to_string())?;
    Ok(matches!(
        reply,
        RequestNameReply::PrimaryOwner | RequestNameReply::AlreadyOwner
    ))
}

pub fn python_message(pid: Option<u32>) -> String {
    let who = pid
        .and_then(|p| {
            std::fs::read(format!("/proc/{p}/cmdline"))
                .ok()
                .map(|c| (p, c))
        })
        .map(|(p, c)| {
            format!(
                " (PID {p}: {})",
                String::from_utf8_lossy(&c).replace('\0', " ").trim()
            )
        })
        .unwrap_or_default();
    format!(
        "Another Jumpkut is already running{who}, probably the Python version.\n\
         Both would record into the same history, so this one will not start.\n\
         Quit it first (tray menu → Quit, or `jumpkut --quit` from that installation), then start this one again."
    )
}
