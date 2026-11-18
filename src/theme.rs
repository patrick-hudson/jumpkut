//! KDE colours and font from `~/.config/kdeglobals`, with Breeze fallbacks.

use std::collections::HashMap;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rgb(pub u8, pub u8, pub u8);

impl Rgb {
    /// Relative luminance (sRGB, WCAG).
    pub fn luminance(self) -> f64 {
        fn channel(c: u8) -> f64 {
            let c = c as f64 / 255.0;
            if c <= 0.04045 {
                c / 12.92
            } else {
                ((c + 0.055) / 1.055).powf(2.4)
            }
        }
        0.2126 * channel(self.0) + 0.7152 * channel(self.1) + 0.0722 * channel(self.2)
    }

    pub fn to_slint(self, alpha: f32) -> slint::Color {
        slint::Color::from_argb_u8(
            (alpha.clamp(0.0, 1.0) * 255.0).round() as u8,
            self.0,
            self.1,
            self.2,
        )
    }
}

/// Breeze's highlight blue.
pub const DEFAULT_ACCENT: Rgb = Rgb(61, 174, 233);

#[derive(Clone, Debug, PartialEq)]
pub struct Theme {
    pub dark: bool,
    pub accent: Rgb,
    pub window_bg: Rgb,
    pub view_bg: Rgb,
    pub fg: Rgb,
    pub muted: Rgb,
    /// On-accent text colour (Selection ForegroundNormal).
    pub on_accent: Rgb,
    pub font_family: Option<String>,
    /// Logical pixels.
    pub font_px: f32,
    /// Whether kdeglobals provided the colours (otherwise follow the portal).
    pub from_kde: bool,
}

impl Theme {
    pub fn breeze(dark: bool) -> Self {
        if dark {
            Theme {
                dark,
                accent: DEFAULT_ACCENT,
                window_bg: Rgb(32, 35, 38),
                view_bg: Rgb(20, 22, 24),
                fg: Rgb(252, 252, 252),
                muted: Rgb(161, 169, 177),
                on_accent: Rgb(252, 252, 252),
                font_family: None,
                font_px: 10.0 * 4.0 / 3.0,
                from_kde: false,
            }
        } else {
            Theme {
                dark,
                accent: DEFAULT_ACCENT,
                window_bg: Rgb(239, 240, 241),
                view_bg: Rgb(255, 255, 255),
                fg: Rgb(35, 38, 41),
                muted: Rgb(112, 125, 138),
                on_accent: Rgb(255, 255, 255),
                font_family: None,
                font_px: 10.0 * 4.0 / 3.0,
                from_kde: false,
            }
        }
    }

    pub fn load() -> Self {
        let path = crate::paths::xdg_root("XDG_CONFIG_HOME", ".config").join("kdeglobals");
        match std::fs::read_to_string(path) {
            Ok(text) => Self::from_kdeglobals(&text),
            Err(_) => Self::breeze(true),
        }
    }

    pub fn from_kdeglobals(text: &str) -> Self {
        let ini = parse_ini(text);
        let get =
            |group: &str, key: &str| ini.get(group).and_then(|g| g.get(key)).map(String::as_str);
        let color = |group: &str, key: &str| get(group, key).and_then(parse_rgb);

        let window_bg = color("Colors:Window", "BackgroundNormal");
        let dark = match window_bg {
            Some(bg) => bg.luminance() < 0.3,
            None => get("General", "ColorScheme")
                .is_none_or(|s| s.to_ascii_lowercase().contains("dark")),
        };
        let mut theme = Theme::breeze(dark);
        theme.from_kde = window_bg.is_some();
        theme.accent = accent_color(&ini).unwrap_or(DEFAULT_ACCENT);
        if let Some(bg) = window_bg {
            theme.window_bg = bg;
        }
        if let Some(c) = color("Colors:View", "BackgroundNormal") {
            theme.view_bg = c;
        }
        if let Some(c) = color("Colors:Window", "ForegroundNormal") {
            theme.fg = c;
        }
        if let Some(c) = color("Colors:Window", "ForegroundInactive") {
            theme.muted = c;
        }
        if let Some(c) = color("Colors:Selection", "ForegroundNormal") {
            theme.on_accent = c;
        }
        if let Some((family, pt)) = get("General", "font").and_then(parse_qfont) {
            theme.font_family = Some(family);
            theme.font_px = pt * 4.0 / 3.0;
        }
        theme
    }
}

/// `[General] AccentColor`, then the selection colour Plasma derives from it.
pub fn accent_color(ini: &HashMap<String, HashMap<String, String>>) -> Option<Rgb> {
    let get = |group: &str, key: &str| {
        ini.get(group)
            .and_then(|g| g.get(key))
            .and_then(|v| parse_rgb(v))
    };
    get("General", "AccentColor").or_else(|| get("Colors:Selection", "BackgroundNormal"))
}

pub fn parse_ini(text: &str) -> HashMap<String, HashMap<String, String>> {
    let mut groups: HashMap<String, HashMap<String, String>> = HashMap::new();
    let mut current = String::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') || line.starts_with(';') {
            continue;
        }
        if let Some(name) = line.strip_prefix('[').and_then(|l| l.strip_suffix(']')) {
            // KConfig nests groups as [A][B]; keep the full name.
            current = name.replace("][", "/");
            continue;
        }
        if let Some((key, value)) = line.split_once('=') {
            // Strip KConfig flags like `Key[$e]` and localized `Key[de]`.
            let key = key.trim();
            let key = key.split_once('[').map_or(key, |(k, _)| k);
            groups
                .entry(current.clone())
                .or_default()
                .insert(key.to_owned(), value.trim().to_owned());
        }
    }
    groups
}

/// `r,g,b`, `r,g,b,a`, `#rrggbb` or `#aarrggbb`.
pub fn parse_rgb(value: &str) -> Option<Rgb> {
    let value = value.trim();
    if let Some(hex) = value.strip_prefix('#') {
        let hex = match hex.len() {
            6 => hex,
            8 => &hex[2..],
            _ => return None,
        };
        let n = u32::from_str_radix(hex, 16).ok()?;
        return Some(Rgb((n >> 16) as u8, (n >> 8) as u8, n as u8));
    }
    let parts: Vec<u8> = value
        .split(',')
        .map(|p| p.trim().parse().ok())
        .collect::<Option<_>>()?;
    match parts.as_slice() {
        [r, g, b] | [r, g, b, _] => Some(Rgb(*r, *g, *b)),
        _ => None,
    }
}

/// `Noto Sans,10,-1,5,400,0,0,0,0,0,0,0,0,0,0,1` -> ("Noto Sans", 10.0).
pub fn parse_qfont(value: &str) -> Option<(String, f32)> {
    let mut parts = value.split(',');
    let family = parts.next()?.trim();
    let size: f32 = parts.next()?.trim().parse().ok()?;
    (!family.is_empty() && size > 0.0).then(|| (family.to_owned(), size))
}
