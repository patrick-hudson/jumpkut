//! The same XDG locations as the Python app (0.7.x), plus test overrides:
//! `JUMPKUT_DATA_DIR`, `JUMPKUT_CONFIG_DIR`, `JUMPKUT_STATE_DIR` and
//! `JUMPKUT_AUTOSTART_DIR` replace the `.../jumpkut` (or `autostart`)
//! directories, so development never touches a user's real files.

use std::path::PathBuf;

pub fn home() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/"))
}

/// `$VARIABLE` if absolute, else `~/fallback` (the XDG rule Python follows).
pub fn xdg_root(variable: &str, fallback: &str) -> PathBuf {
    match std::env::var_os(variable).map(PathBuf::from) {
        Some(path) if path.is_absolute() => path,
        _ => home().join(fallback),
    }
}

fn overridden(variable: &str) -> Option<PathBuf> {
    std::env::var_os(variable)
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
}

pub fn data_dir() -> PathBuf {
    overridden("JUMPKUT_DATA_DIR")
        .unwrap_or_else(|| xdg_root("XDG_DATA_HOME", ".local/share").join("jumpkut"))
}

pub fn history_path() -> PathBuf {
    data_dir().join("history.json")
}

pub fn config_path() -> PathBuf {
    overridden("JUMPKUT_CONFIG_DIR")
        .unwrap_or_else(|| xdg_root("XDG_CONFIG_HOME", ".config").join("jumpkut"))
        .join("config.json")
}

pub fn state_dir() -> PathBuf {
    overridden("JUMPKUT_STATE_DIR")
        .unwrap_or_else(|| xdg_root("XDG_STATE_HOME", ".local/state").join("jumpkut"))
}

pub fn log_path() -> PathBuf {
    state_dir().join("jumpkut.log")
}

/// The RemoteDesktop portal restore token (shared with the Python app).
pub fn token_path() -> PathBuf {
    state_dir().join("remote-desktop-token")
}

pub fn autostart_path() -> PathBuf {
    overridden("JUMPKUT_AUTOSTART_DIR")
        .unwrap_or_else(|| xdg_root("XDG_CONFIG_HOME", ".config").join("autostart"))
        .join("jumpkut.desktop")
}

/// A private (mode 0700, owned by us) per-process directory for files other
/// processes read by path, such as KWin scripts. Under `$XDG_RUNTIME_DIR`
/// when that is set, otherwise under the state directory, never a shared
/// temporary directory. Created on first use; every call returns the same
/// path, so shutdown removes the directory that was used.
pub fn runtime_dir() -> PathBuf {
    static DIR: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
    DIR.get_or_init(|| {
        let name = format!("jumpkut-{}", std::process::id());
        let fallback = state_dir().join("run");
        if let Some(base) = overridden("XDG_RUNTIME_DIR") {
            let dir = base.join(&name);
            match ensure_private_dir(&dir) {
                Ok(()) => return dir,
                Err(e) => eprintln!("Jumpkut: {e}"),
            }
        }
        let dir = fallback.join(&name);
        if let Err(e) = ensure_private_dir(&fallback).and_then(|()| ensure_private_dir(&dir)) {
            eprintln!("Jumpkut: {e}");
        }
        dir
    })
    .clone()
}

/// Create `dir` with mode 0700, or accept an existing one only if it is a
/// real directory owned by us that no one else can access.
pub fn ensure_private_dir(dir: &std::path::Path) -> Result<(), String> {
    use std::os::unix::fs::{DirBuilderExt, MetadataExt};
    if let Some(parent) = dir.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Cannot create {}: {e}", parent.display()))?;
    }
    match std::fs::DirBuilder::new().mode(0o700).create(dir) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(format!("Cannot create {}: {e}", dir.display())),
    }
    let meta = std::fs::symlink_metadata(dir)
        .map_err(|e| format!("Cannot inspect {}: {e}", dir.display()))?;
    // SAFETY: geteuid has no preconditions.
    let uid = unsafe { libc::geteuid() };
    if !meta.file_type().is_dir() || meta.uid() != uid || meta.mode() & 0o077 != 0 {
        return Err(format!(
            "Refusing {}: it is not a private directory owned by this user.",
            dir.display()
        ));
    }
    Ok(())
}
