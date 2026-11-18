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

pub fn runtime_dir() -> PathBuf {
    let base = overridden("XDG_RUNTIME_DIR").unwrap_or_else(std::env::temp_dir);
    base.join(format!("jumpkut-{}", std::process::id()))
}
