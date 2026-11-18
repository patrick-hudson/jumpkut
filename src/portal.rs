//! Desktop portals and notifications over zbus:
//! * FileChooser for backups (KDE shows its native dialog),
//! * org.freedesktop.Notifications for status messages,
//! * RemoteDesktop as the last-resort paste method.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::mpsc::{Sender, channel};

use zbus::MatchRule;
use zbus::blocking::{Connection, MessageIterator};
use zbus::message::Type as MessageType;
use zbus::zvariant::{OwnedObjectPath, OwnedValue, Value};

const PORTAL: &str = "org.freedesktop.portal.Desktop";
const PATH: &str = "/org/freedesktop/portal/desktop";

type Results = HashMap<String, OwnedValue>;

/// Call a portal method that answers through a Request object's Response.
fn request(
    conn: &Connection,
    counter: &mut u32,
    call: impl FnOnce(&str) -> zbus::Result<zbus::message::Message>,
) -> Result<Option<Results>, String> {
    *counter += 1;
    let token = format!("jumpkut{}_{}", std::process::id(), counter);
    let sender = conn
        .unique_name()
        .map(|n| n.trim_start_matches(':').replace('.', "_"))
        .unwrap_or_default();
    let rule = MatchRule::builder()
        .msg_type(MessageType::Signal)
        .interface("org.freedesktop.portal.Request")
        .and_then(|b| b.member("Response"))
        .and_then(|b| b.path(format!("{PATH}/request/{sender}/{token}")))
        .map_err(|e| e.to_string())?
        .build();
    let mut responses =
        MessageIterator::for_match_rule(rule, conn, Some(4)).map_err(|e| e.to_string())?;
    call(&token).map_err(|e| e.to_string())?;
    let message = responses
        .next()
        .ok_or("the portal closed")?
        .map_err(|e| e.to_string())?;
    let (code, results): (u32, Results) =
        message.body().deserialize().map_err(|e| e.to_string())?;
    match code {
        0 => Ok(Some(results)),
        1 => Ok(None), // cancelled / denied
        _ => Err("the portal request failed".into()),
    }
}

fn session() -> Result<Connection, String> {
    Connection::session().map_err(|e| e.to_string())
}

fn uri_to_path(uri: &str) -> Option<PathBuf> {
    let rest = uri.strip_prefix("file://")?;
    let mut bytes = Vec::new();
    let raw = rest.as_bytes();
    let mut i = 0;
    while i < raw.len() {
        if raw[i] == b'%'
            && i + 2 < raw.len()
            && let Ok(b) = u8::from_str_radix(std::str::from_utf8(&raw[i + 1..i + 3]).ok()?, 16)
        {
            bytes.push(b);
            i += 3;
            continue;
        }
        bytes.push(raw[i]);
        i += 1;
    }
    use std::os::unix::ffi::OsStringExt;
    Some(PathBuf::from(std::ffi::OsString::from_vec(bytes)))
}

/// Ask for a backup file (on a thread; `done` gets the choice or an error).
pub fn choose_backup(
    save: bool,
    done: impl FnOnce(Result<Option<PathBuf>, String>) + Send + 'static,
) {
    std::thread::spawn(move || {
        let result = (|| {
            let conn = session()?;
            let mut counter = 0;
            let filter = ("Jumpkut history (*.json)", vec![(0u32, "*.json")]);
            let results = request(&conn, &mut counter, |token| {
                let mut options: HashMap<&str, Value> = HashMap::from([
                    ("handle_token", Value::from(token)),
                    ("modal", Value::from(true)),
                    ("filters", Value::from(vec![filter.clone()])),
                    ("current_filter", Value::from(filter.clone())),
                ]);
                if save {
                    let name = format!(
                        "jumpkut-history-{}.json",
                        &crate::history::local_date(crate::history::now())[..10]
                    );
                    options.insert("current_name", Value::from(name));
                    conn.call_method(
                        Some(PORTAL),
                        PATH,
                        Some("org.freedesktop.portal.FileChooser"),
                        "SaveFile",
                        &("", "Back Up History", options),
                    )
                } else {
                    conn.call_method(
                        Some(PORTAL),
                        PATH,
                        Some("org.freedesktop.portal.FileChooser"),
                        "OpenFile",
                        &("", "Restore Backup", options),
                    )
                }
            })?;
            let uris: Vec<String> = results
                .and_then(|r| r.get("uris").cloned())
                .and_then(|v| Vec::<String>::try_from(v).ok())
                .unwrap_or_default();
            Ok(uris.first().and_then(|u| uri_to_path(u)))
        })();
        done(result);
    });
}

/// A desktop notification (fire and forget).
pub fn notify(body: String) {
    std::thread::spawn(move || {
        let Ok(conn) = session() else { return };
        let hints: HashMap<&str, Value> =
            HashMap::from([("desktop-entry", Value::from("jumpkut"))]);
        let _ = conn.call_method(
            Some("org.freedesktop.Notifications"),
            "/org/freedesktop/Notifications",
            Some("org.freedesktop.Notifications"),
            "Notify",
            &(
                "Jumpkut",
                0u32,
                "jumpkut",
                "Jumpkut",
                body.as_str(),
                Vec::<&str>::new(),
                hints,
                -1i32,
            ),
        );
    });
}

/// The RemoteDesktop portal keyboard. The first session shows KDE's
/// permission dialog; its restore token (shared with the Python app, in
/// `~/.local/state/jumpkut/remote-desktop-token`) makes later ones silent.
type KeyRequest = (Vec<i32>, Sender<Result<(), String>>);

pub struct RemoteKeyboard {
    tx: Sender<KeyRequest>,
}

struct RemoteSession {
    conn: Connection,
    handle: OwnedObjectPath,
}

impl RemoteSession {
    fn start() -> Result<Self, String> {
        const REMOTE: &str = "org.freedesktop.portal.RemoteDesktop";
        let conn = session()?;
        let mut counter = 0;
        let denied = || {
            "Auto-paste needs permission to type the paste shortcut. The clipping was copied; paste it with Ctrl+V (Ctrl+Shift+V in terminals).".to_string()
        };
        let created = request(&conn, &mut counter, |token| {
            let options: HashMap<&str, Value> = HashMap::from([
                ("handle_token", Value::from(token)),
                (
                    "session_handle_token",
                    Value::from(format!("{token}_session")),
                ),
            ]);
            conn.call_method(
                Some(PORTAL),
                PATH,
                Some(REMOTE),
                "CreateSession",
                &(options,),
            )
        })?
        .ok_or_else(denied)?;
        let handle: String = created
            .get("session_handle")
            .and_then(|v| String::try_from(v.clone()).ok())
            .ok_or("no session handle")?;
        let handle = OwnedObjectPath::try_from(handle).map_err(|e| e.to_string())?;
        let token_path = crate::paths::token_path();
        let restore = std::fs::read_to_string(&token_path)
            .ok()
            .map(|t| t.trim().to_owned())
            .filter(|t| !t.is_empty());
        request(&conn, &mut counter, |token| {
            let mut options: HashMap<&str, Value> = HashMap::from([
                ("handle_token", Value::from(token)),
                ("types", Value::from(1u32)),
                ("persist_mode", Value::from(2u32)),
            ]);
            if let Some(t) = &restore {
                options.insert("restore_token", Value::from(t.as_str()));
            }
            conn.call_method(
                Some(PORTAL),
                PATH,
                Some(REMOTE),
                "SelectDevices",
                &(&handle, options),
            )
        })?
        .ok_or_else(denied)?;
        let started = request(&conn, &mut counter, |token| {
            let options: HashMap<&str, Value> =
                HashMap::from([("handle_token", Value::from(token))]);
            conn.call_method(
                Some(PORTAL),
                PATH,
                Some(REMOTE),
                "Start",
                &(&handle, "", options),
            )
        })?
        .ok_or_else(denied)?;
        if started
            .get("devices")
            .and_then(|v| u32::try_from(v.clone()).ok())
            .unwrap_or(0)
            & 1
            == 0
        {
            return Err("The remote input portal did not grant keyboard access.".into());
        }
        if let Some(token) = started
            .get("restore_token")
            .and_then(|v| String::try_from(v.clone()).ok())
            && let Err(e) = crate::history::atomic_write(&token_path, token.as_bytes(), true)
        {
            eprintln!("Jumpkut: could not save the remote input permission: {e}");
        }
        Ok(RemoteSession { conn, handle })
    }

    fn key(&self, keysym: i32, state: u32) -> zbus::Result<()> {
        let options: HashMap<&str, Value> = HashMap::new();
        self.conn
            .call_method(
                Some(PORTAL),
                PATH,
                Some("org.freedesktop.portal.RemoteDesktop"),
                "NotifyKeyboardKeysym",
                &(&self.handle, options, keysym, state),
            )
            .map(|_| ())
    }
}

impl RemoteKeyboard {
    pub fn start() -> Self {
        let (tx, rx) = channel::<KeyRequest>();
        std::thread::spawn(move || {
            let session = RemoteSession::start();
            if let Err(e) = &session {
                eprintln!("Jumpkut: {e}");
            }
            for (keys, reply) in rx {
                let result = match &session {
                    Ok(s) => (|| {
                        for k in &keys {
                            s.key(*k, 1)?;
                        }
                        for k in keys.iter().rev() {
                            s.key(*k, 0)?;
                        }
                        Ok(())
                    })()
                    .map_err(|e: zbus::Error| e.to_string()),
                    Err(e) => Err(e.clone()),
                };
                let _ = reply.send(result);
            }
            if let Ok(s) = session {
                let _ = s.conn.call_method(
                    Some(PORTAL),
                    s.handle.as_str(),
                    Some("org.freedesktop.portal.Session"),
                    "Close",
                    &(),
                );
            }
        });
        RemoteKeyboard { tx }
    }

    pub fn tap(&self, keysyms: Vec<i32>) -> Result<(), String> {
        let (reply, result) = channel();
        self.tx.send((keysyms, reply)).map_err(|e| e.to_string())?;
        result
            .recv_timeout(std::time::Duration::from_secs(3))
            .map_err(|e| e.to_string())?
    }
}
