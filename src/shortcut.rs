//! Shortcuts in the settings file's GTK accelerator format (`<Alt>c`,
//! `<Primary><Shift>F5`), plus `Alt+V`-style input, and the encodings that
//! KGlobalAccel (Qt key codes) and X11 (keysyms) need.

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Modifiers {
    pub control: bool,
    pub alt: bool,
    pub shift: bool,
    pub super_: bool,
}

impl Modifiers {
    pub fn any(self) -> bool {
        self.control || self.alt || self.shift || self.super_
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Shortcut {
    pub modifiers: Modifiers,
    /// GDK keyval name; letters are lowercase (`c`, `F5`, `space`, `Page_Up`).
    pub key: String,
}

/// (GDK name, Qt key, X11 keysym, Slint key char, label)
const NAMED: &[(&str, u32, u32, char, &str)] = &[
    ("space", 0x20, 0x20, ' ', "Space"),
    ("Tab", 0x0100_0001, 0xff09, '\t', "Tab"),
    ("Return", 0x0100_0004, 0xff0d, '\n', "Return"),
    ("BackSpace", 0x0100_0003, 0xff08, '\u{8}', "Backspace"),
    ("Insert", 0x0100_0006, 0xff63, '\u{F727}', "Insert"),
    ("Delete", 0x0100_0007, 0xffff, '\u{7f}', "Delete"),
    ("Home", 0x0100_0010, 0xff50, '\u{F729}', "Home"),
    ("End", 0x0100_0011, 0xff57, '\u{F72B}', "End"),
    ("Left", 0x0100_0012, 0xff51, '\u{F702}', "Left"),
    ("Up", 0x0100_0013, 0xff52, '\u{F700}', "Up"),
    ("Right", 0x0100_0014, 0xff53, '\u{F703}', "Right"),
    ("Down", 0x0100_0015, 0xff54, '\u{F701}', "Down"),
    ("Page_Up", 0x0100_0016, 0xff55, '\u{F72C}', "Page Up"),
    ("Page_Down", 0x0100_0017, 0xff56, '\u{F72D}', "Page Down"),
    ("Menu", 0x0100_0055, 0xff67, '\u{F735}', "Menu"),
];

/// GDK names of punctuation keys and their characters.
const PUNCTUATION: &[(&str, char)] = &[
    ("comma", ','),
    ("period", '.'),
    ("slash", '/'),
    ("semicolon", ';'),
    ("apostrophe", '\''),
    ("bracketleft", '['),
    ("bracketright", ']'),
    ("backslash", '\\'),
    ("minus", '-'),
    ("equal", '='),
    ("grave", '`'),
];

fn function_key(key: &str) -> Option<u32> {
    let n: u32 = key
        .strip_prefix('F')
        .or_else(|| key.strip_prefix('f'))?
        .parse()
        .ok()?;
    (1..=35).contains(&n).then_some(n)
}

/// The character a key name produces, for letters, digits and punctuation.
fn key_char(key: &str) -> Option<char> {
    if let Some((_, c)) = PUNCTUATION.iter().find(|(n, _)| *n == key) {
        return Some(*c);
    }
    let mut chars = key.chars();
    let c = chars.next()?;
    chars.next().is_none().then_some(c)
}

fn canonical_key(name: &str) -> Option<String> {
    if let Some(n) = function_key(name) {
        return Some(format!("F{n}"));
    }
    if let Some((n, ..)) = NAMED.iter().find(|(n, ..)| n.eq_ignore_ascii_case(name)) {
        return Some((*n).to_owned());
    }
    if let Some((n, _)) = PUNCTUATION
        .iter()
        .find(|(n, c)| n.eq_ignore_ascii_case(name) || name == c.to_string())
    {
        return Some((*n).to_owned());
    }
    let mut chars = name.chars();
    let c = chars.next()?;
    (chars.next().is_none() && c.is_alphanumeric()).then(|| c.to_lowercase().collect())
}

/// Parse `<Alt>c`, `<Primary><Shift>F5` or `Alt+V`.
pub fn parse(text: &str) -> Result<Shortcut, String> {
    let mut modifiers = Modifiers::default();
    let mut rest = text.trim();
    let mut names: Vec<String> = Vec::new();
    while let Some(after) = rest.strip_prefix('<') {
        let (name, tail) = after
            .split_once('>')
            .ok_or_else(|| format!("invalid shortcut {text:?}"))?;
        names.push(name.to_owned());
        rest = tail;
    }
    let mut parts: Vec<&str> = if rest.len() > 1 && rest.contains('+') {
        rest.split('+').collect()
    } else {
        vec![rest]
    };
    let key = parts.pop().unwrap_or("").trim();
    names.extend(parts.iter().map(|p| p.trim().to_owned()));
    for name in names {
        match name.to_ascii_lowercase().as_str() {
            "alt" | "mod1" => modifiers.alt = true,
            "ctrl" | "control" | "primary" => modifiers.control = true,
            "shift" => modifiers.shift = true,
            "super" | "meta" | "win" | "mod4" => modifiers.super_ = true,
            other => return Err(format!("unsupported modifier {other:?} in {text:?}")),
        }
    }
    if !modifiers.any() {
        return Err(
            "Use a shortcut with Alt, Control, Shift, or Super, for example <Alt>c.".into(),
        );
    }
    let key = canonical_key(key).ok_or_else(|| format!("unsupported key in {text:?}"))?;
    Ok(Shortcut { modifiers, key })
}

impl Shortcut {
    /// The GTK accelerator stored in `config.json`.
    pub fn accel(&self) -> String {
        let m = self.modifiers;
        let mut out = String::new();
        for (on, name) in [
            (m.shift, "<Shift>"),
            (m.control, "<Primary>"),
            (m.alt, "<Alt>"),
            (m.super_, "<Super>"),
        ] {
            if on {
                out.push_str(name);
            }
        }
        out + &self.key
    }

    pub fn label(&self) -> String {
        let m = self.modifiers;
        let mut parts: Vec<String> = Vec::new();
        for (on, name) in [
            (m.shift, "Shift"),
            (m.control, "Ctrl"),
            (m.alt, "Alt"),
            (m.super_, "Super"),
        ] {
            if on {
                parts.push(name.into());
            }
        }
        let key = if let Some((.., label)) = NAMED.iter().find(|(n, ..)| *n == self.key) {
            (*label).to_owned()
        } else if let Some(c) = key_char(&self.key) {
            c.to_uppercase().collect()
        } else {
            self.key.clone()
        };
        parts.push(key);
        parts.join("+")
    }

    pub fn modifier_label(&self) -> String {
        let label = self.label();
        label
            .rsplit_once('+')
            .map_or(label.clone(), |(mods, _)| mods.to_owned())
    }

    /// Qt key combination for KGlobalAccel (same codes the Python app used).
    pub fn qt_key(&self) -> u32 {
        let key = if let Some(n) = function_key(&self.key) {
            0x0100_002f + n
        } else if let Some((_, qt, ..)) = NAMED.iter().find(|(n, ..)| *n == self.key) {
            *qt
        } else {
            let c = key_char(&self.key).unwrap_or(' ');
            c.to_uppercase().next().unwrap_or(c) as u32
        };
        let m = self.modifiers;
        key | if m.shift { 0x0200_0000 } else { 0 }
            | if m.control { 0x0400_0000 } else { 0 }
            | if m.alt { 0x0800_0000 } else { 0 }
            | if m.super_ { 0x1000_0000 } else { 0 }
    }

    /// X11 keysym of the key.
    pub fn keysym(&self) -> u32 {
        if let Some(n) = function_key(&self.key) {
            return 0xffbd + n;
        }
        if let Some((_, _, sym, ..)) = NAMED.iter().find(|(n, ..)| *n == self.key) {
            return *sym;
        }
        let c = key_char(&self.key).unwrap_or(' ') as u32;
        if c < 0x100 { c } else { 0x0100_0000 + c }
    }

    /// The text Slint reports for the key (lowercase), for in-popup cycling.
    pub fn slint_char(&self) -> String {
        if let Some((.., c, _)) = NAMED.iter().find(|(n, ..)| *n == self.key) {
            return c.to_string();
        }
        if let Some(n) = function_key(&self.key) {
            return char::from_u32(0xF703 + n)
                .map(String::from)
                .unwrap_or_default();
        }
        key_char(&self.key).map(String::from).unwrap_or_default()
    }

    /// A shortcut from a Slint key event, for the Preferences recorder.
    pub fn from_slint(text: &str, modifiers: Modifiers) -> Option<Shortcut> {
        if !modifiers.any() {
            return None;
        }
        let mut chars = text.chars();
        let c = chars.next()?;
        if chars.next().is_some() {
            return None;
        }
        let key = if let Some((n, ..)) = NAMED.iter().find(|(.., k, _)| *k == c) {
            (*n).to_owned()
        } else if (0xF704..=0xF71B).contains(&(c as u32)) {
            format!("F{}", c as u32 - 0xF703)
        } else if c.is_control() || (0xF700..=0xF8FF).contains(&(c as u32)) {
            return None; // modifier keys, Escape, ...
        } else {
            canonical_key(&c.to_lowercase().collect::<String>())?
        };
        Some(Shortcut { modifiers, key })
    }
}
