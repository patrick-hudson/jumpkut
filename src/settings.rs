//! Preferences compatible with the Python app (0.7.x): the same JSON file,
//! keys, validation, legacy migration and autostart entry.

use std::path::PathBuf;

use crate::{history, paths, pyjson};

#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    /// A GTK accelerator, e.g. `<Alt>c`.
    pub hotkey: String,
    pub history_limit: i64,
    pub sticky: bool,
    pub auto_paste: bool,
    pub persist: bool,
    pub tray_limit: i64,
    pub custom_quick_limit: bool,
    pub custom_tray_limit: bool,
    pub resume_last_selection: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            hotkey: "<Alt>c".into(),
            history_limit: 30,
            sticky: false,
            auto_paste: true,
            persist: true,
            tray_limit: 10,
            custom_quick_limit: false,
            custom_tray_limit: false,
            resume_last_selection: true,
        }
    }
}

const KEYS: [&str; 9] = [
    "hotkey",
    "history_limit",
    "sticky",
    "auto_paste",
    "persist",
    "tray_limit",
    "custom_quick_limit",
    "custom_tray_limit",
    "resume_last_selection",
];

impl Settings {
    pub fn quick_history_limit(&self) -> usize {
        if self.custom_quick_limit {
            self.history_limit as usize
        } else {
            30
        }
    }

    pub fn tray_history_limit(&self) -> usize {
        if self.custom_tray_limit {
            self.tray_limit as usize
        } else {
            10
        }
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.hotkey.trim().is_empty() {
            return Err("The keyboard shortcut cannot be empty".into());
        }
        if !(1..=10000).contains(&self.history_limit) {
            return Err("Quick history length must be an integer between 1 and 10000".into());
        }
        if !(1..=10000).contains(&self.tray_limit) {
            return Err("Tray history length must be an integer between 1 and 10000".into());
        }
        Ok(())
    }

    /// `json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n"`.
    pub fn encode(&self) -> String {
        let fields = [
            pyjson::string(&self.hotkey),
            self.history_limit.to_string(),
            self.sticky.to_string(),
            self.auto_paste.to_string(),
            self.persist.to_string(),
            self.tray_limit.to_string(),
            self.custom_quick_limit.to_string(),
            self.custom_tray_limit.to_string(),
            self.resume_last_selection.to_string(),
        ];
        let body: Vec<String> = KEYS
            .iter()
            .zip(fields)
            .map(|(k, v)| format!("  \"{k}\": {v}"))
            .collect();
        format!("{{\n{}\n}}\n", body.join(",\n"))
    }

    /// Parsing, validation and legacy migration as the Python app did them.
    pub fn decode(content: &str) -> Result<Settings, String> {
        let data: serde_json::Value = serde_json::from_str(content).map_err(|e| e.to_string())?;
        let object = data
            .as_object()
            .ok_or("Preferences must be a JSON object")?;
        let mut unknown: Vec<&str> = object
            .keys()
            .map(String::as_str)
            .filter(|k| !KEYS.contains(k))
            .collect();
        if !unknown.is_empty() {
            unknown.sort_unstable();
            return Err(format!("Unrecognized preference: {}", unknown.join(", ")));
        }
        let mut s = Settings::default();
        for (key, value) in object {
            let as_bool = || {
                value
                    .as_bool()
                    .ok_or(format!("{key} must be true or false"))
            };
            // `type(x) is not int`: JSON integers only (bool is separate in JSON).
            let as_int = || {
                value
                    .as_i64()
                    .filter(|_| value.is_i64() || value.is_u64())
                    .ok_or(format!("{key} must be an integer"))
            };
            match key.as_str() {
                "hotkey" => {
                    s.hotkey = value
                        .as_str()
                        .ok_or("The keyboard shortcut cannot be empty")?
                        .to_owned()
                }
                "history_limit" => {
                    s.history_limit = as_int().map_err(
                        |_| "Quick history length must be an integer between 1 and 10000",
                    )?
                }
                "tray_limit" => {
                    s.tray_limit = as_int()
                        .map_err(|_| "Tray history length must be an integer between 1 and 10000")?
                }
                "sticky" => s.sticky = as_bool()?,
                "auto_paste" => s.auto_paste = as_bool()?,
                "persist" => s.persist = as_bool()?,
                "custom_quick_limit" => s.custom_quick_limit = as_bool()?,
                "custom_tray_limit" => s.custom_tray_limit = as_bool()?,
                "resume_last_selection" => s.resume_last_selection = as_bool()?,
                _ => unreachable!(),
            }
        }
        s.validate()?;
        if !object.contains_key("custom_quick_limit") && object.contains_key("history_limit") {
            // Legacy files used 200 for both menus: map it (or 30) to the new
            // default and keep other chosen lengths as a custom value.
            if s.history_limit == 200 || s.history_limit == 30 {
                s.history_limit = 30;
            } else {
                s.custom_quick_limit = true;
            }
        }
        Ok(s)
    }
}

/// Loads defaults on invalid data without modifying the original file.
pub struct Config {
    pub path: PathBuf,
    pub settings: Settings,
    pub load_error: Option<String>,
}

impl Config {
    pub fn load(path: PathBuf) -> Self {
        let mut config = Config {
            path,
            settings: Settings::default(),
            load_error: None,
        };
        match std::fs::read(&config.path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => config.load_error = Some(format!("Could not read preferences: {e}")),
            Ok(raw) => match String::from_utf8(raw)
                .map_err(|e| e.to_string())
                .and_then(|c| Settings::decode(&c))
            {
                Ok(settings) => config.settings = settings,
                Err(e) => {
                    config.load_error = Some(format!(
                        "Invalid preferences: {e}; original file left unchanged"
                    ))
                }
            },
        }
        config
    }

    pub fn save(&mut self, settings: Settings) -> Result<(), String> {
        settings.validate()?;
        history::atomic_write(&self.path, settings.encode().as_bytes(), true)
            .map_err(|e| e.to_string())?;
        self.settings = settings;
        self.load_error = None;
        Ok(())
    }
}

/// Quote one desktop-entry Exec argument (both escaping layers, `%` doubled).
pub fn quote_exec_argument(argument: &str) -> String {
    let argument = argument.replace('%', "%%");
    let escaped: String = argument
        .chars()
        .flat_map(|c| {
            if "\\\"`$".contains(c) {
                vec!['\\', c]
            } else {
                vec![c]
            }
        })
        .collect();
    let escaped = escaped
        .replace('\\', "\\\\")
        .replace('\n', "\\n")
        .replace('\t', "\\t")
        .replace('\r', "\\r");
    format!("\"{escaped}\"")
}

pub fn autostart_enabled() -> bool {
    let Ok(text) = std::fs::read_to_string(paths::autostart_path()) else {
        return false;
    };
    let mut in_entry = false;
    let mut found = false;
    let mut hidden = false;
    for line in text.lines().map(str::trim) {
        if line.starts_with('[') {
            in_entry = line == "[Desktop Entry]";
            found |= in_entry;
        } else if in_entry
            && let Some((key, value)) = line.split_once('=')
            && key.trim() == "Hidden"
        {
            hidden = matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "true" | "1" | "yes" | "on"
            );
        }
    }
    found && !hidden
}

/// The command that starts this installation: the AppImage launcher when
/// `JUMPKUT_LAUNCHER` is set (like Python), else this executable.
fn launch_command() -> String {
    let launcher = std::env::var_os("JUMPKUT_LAUNCHER")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute());
    match launcher {
        Some(path) => {
            let extraction = if std::env::var("APPIMAGE_EXTRACT_AND_RUN").as_deref() == Ok("1") {
                "APPIMAGE_EXTRACT_AND_RUN=1 "
            } else {
                ""
            };
            format!(
                "/usr/bin/env {extraction}{}",
                quote_exec_argument(&path.to_string_lossy())
            )
        }
        None => {
            let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("jumpkut"));
            quote_exec_argument(&exe.to_string_lossy())
        }
    }
}

pub fn set_autostart(enabled: bool) -> Result<(), String> {
    let path = paths::autostart_path();
    if !enabled {
        return match std::fs::remove_file(&path) {
            Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(e.to_string()),
            _ => Ok(()),
        };
    }
    let content = format!(
        "[Desktop Entry]\nType=Application\nName=Jumpkut\nComment=Clipboard history and quick selection\n\
         Exec={} --daemon\nIcon=edit-paste\nTerminal=false\nX-GNOME-Autostart-enabled=true\n",
        launch_command()
    );
    history::atomic_write(&path, content.as_bytes(), false).map_err(|e| e.to_string())
}

/// A startup entry written by the Python app runs `…/jumpkut/app/run.py`,
/// which is removed when the Rust app replaces it: point it at this app.
/// Returns whether the entry was rewritten.
pub fn migrate_autostart() -> Result<bool, String> {
    let Ok(text) = std::fs::read_to_string(paths::autostart_path()) else {
        return Ok(false);
    };
    let python = text
        .lines()
        .any(|l| l.trim_start().starts_with("Exec=") && l.contains("run.py"));
    if python && autostart_enabled() {
        set_autostart(true)?;
        return Ok(true);
    }
    Ok(false)
}

pub fn load_default() -> Config {
    Config::load(paths::config_path())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(name: &str) -> String {
        std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures")
                .join(name),
        )
        .unwrap()
    }

    #[test]
    fn python_settings_round_trip_byte_for_byte() {
        assert_eq!(Settings::default().encode(), fixture("config-default.json"));
        let custom = fixture("config-custom.json");
        let settings = Settings::decode(&custom).unwrap();
        assert_eq!(settings.hotkey, "<Primary><Alt>h");
        assert_eq!(settings.quick_history_limit(), 55);
        assert_eq!(settings.tray_history_limit(), 10);
        assert_eq!(settings.encode(), custom);
    }

    #[test]
    fn legacy_migration_and_validation_match_python() {
        let legacy = Settings::decode(r#"{"hotkey": "<Alt>c", "history_limit": 200}"#).unwrap();
        assert_eq!(
            (legacy.history_limit, legacy.custom_quick_limit),
            (30, false)
        );
        let chosen = Settings::decode(r#"{"history_limit": 75}"#).unwrap();
        assert_eq!(
            (chosen.history_limit, chosen.custom_quick_limit),
            (75, true)
        );
        for bad in [
            r#"{"history_limit": 0}"#,
            r#"{"sticky": 1}"#,
            r#"{"history_limit": 5.0}"#,
            r#"{"nope": 1}"#,
            "[]",
        ] {
            assert!(Settings::decode(bad).is_err(), "{bad}");
        }
    }
}
