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
/// when that is ours and not writable by others, otherwise under the state
/// directory, never a shared temporary directory. `None` when neither is
/// safe. Created on first use; every call returns the same path, so shutdown
/// removes the directory that was used.
pub fn runtime_dir() -> Option<PathBuf> {
    static DIR: std::sync::OnceLock<Option<PathBuf>> = std::sync::OnceLock::new();
    DIR.get_or_init(|| {
        let name = format!("jumpkut-{}", std::process::id());
        let result = choose_runtime_dir(
            overridden("XDG_RUNTIME_DIR").as_deref(),
            &state_dir().join("run"),
            &name,
        );
        if result.is_none() {
            eprintln!("Jumpkut: no private runtime directory is available.");
        }
        result
    })
    .clone()
}

fn choose_runtime_dir(
    base: Option<&std::path::Path>,
    fallback: &std::path::Path,
    name: &str,
) -> Option<PathBuf> {
    // Only a parent no one else can write to keeps the child from being
    // replaced after it is checked.
    if let Some(base) = base.filter(|b| owned_not_shared(b)) {
        let dir = base.join(name);
        match ensure_private_dir(&dir) {
            Ok(()) => return Some(dir),
            Err(e) => eprintln!("Jumpkut: {e}"),
        }
    }
    let dir = fallback.join(name);
    match ensure_private_dir(fallback).and_then(|()| ensure_private_dir(&dir)) {
        Ok(()) => Some(dir),
        Err(e) => {
            eprintln!("Jumpkut: {e}");
            None
        }
    }
}

/// `dir` is a directory owned by us that group and others cannot write to.
fn owned_not_shared(dir: &std::path::Path) -> bool {
    use std::os::unix::fs::MetadataExt;
    // SAFETY: geteuid has no preconditions.
    let uid = unsafe { libc::geteuid() };
    std::fs::metadata(dir).is_ok_and(|m| m.is_dir() && m.uid() == uid && m.mode() & 0o022 == 0)
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

#[cfg(test)]
mod runtime_tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn a_shared_runtime_parent_falls_back_to_the_private_state_dir() {
        let root = std::env::temp_dir().join(format!("jumpkut-rt-{}", std::process::id()));
        let shared = root.join("shared");
        std::fs::create_dir_all(&shared).unwrap();
        std::fs::set_permissions(&shared, std::fs::Permissions::from_mode(0o777)).unwrap();
        let fallback = root.join("state/run");
        let dir = choose_runtime_dir(Some(&shared), &fallback, "jumpkut-1").unwrap();
        assert_eq!(dir, fallback.join("jumpkut-1"));
        assert!(!shared.join("jumpkut-1").exists());
        // An existing directory that others can enter is refused outright.
        std::fs::set_permissions(&fallback, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert_eq!(
            choose_runtime_dir(Some(&shared), &fallback, "jumpkut-2"),
            None
        );
        std::fs::remove_dir_all(&root).unwrap();
    }
}
