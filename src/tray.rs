//! The StatusNotifierItem tray icon and menu (ksni), like the Python app's
//! tray: newest clippings, Full History, Preferences, About, Pause Recording,
//! backup/restore, Clear History, Quit. A click opens the menu.

use std::sync::Arc;

use ksni::menu::{CheckmarkItem, StandardItem};
use ksni::{MenuItem, ToolTip, Tray};

#[derive(Clone, Copy, Debug)]
pub enum Action {
    Clip(usize),
    FullHistory,
    Preferences,
    About,
    Pause(bool),
    Backup,
    Restore,
    Clear,
    Quit,
}

pub struct JumpkutTray {
    pub tooltip: String,
    pub count: usize,
    pub clips: Vec<String>,
    pub paused: bool,
    pub icons: Vec<ksni::Icon>,
    pub on_action: Arc<dyn Fn(Action) + Send + Sync>,
}

/// DBusMenu treats `_` as a mnemonic marker.
fn label(text: &str) -> String {
    text.replace('_', "__")
}

impl JumpkutTray {
    fn item(&self, text: &str, action: Action) -> MenuItem<Self> {
        StandardItem {
            label: label(text),
            activate: Box::new(move |tray: &mut Self| (tray.on_action)(action)),
            ..Default::default()
        }
        .into()
    }

    fn disabled(text: &str) -> MenuItem<Self> {
        StandardItem {
            label: label(text),
            enabled: false,
            ..Default::default()
        }
        .into()
    }
}

impl Tray for JumpkutTray {
    const MENU_ON_ACTIVATE: bool = true;

    fn id(&self) -> String {
        crate::app::app_id()
    }

    fn title(&self) -> String {
        "Jumpkut".into()
    }

    fn icon_pixmap(&self) -> Vec<ksni::Icon> {
        self.icons.clone()
    }

    fn tool_tip(&self) -> ToolTip {
        ToolTip {
            title: "Jumpkut".into(),
            description: self.tooltip.clone(),
            ..Default::default()
        }
    }

    fn menu(&self) -> Vec<MenuItem<Self>> {
        let mut items = vec![Self::disabled(&format!(
            "Clipboard History · {} clips",
            self.count
        ))];
        for (index, preview) in self.clips.iter().enumerate() {
            items.push(self.item(&format!("{}.  {preview}", index + 1), Action::Clip(index)));
        }
        if self.clips.is_empty() {
            items.push(Self::disabled("Copy some text to start your history"));
        }
        items.push(MenuItem::Separator);
        items.push(self.item("Show Full History", Action::FullHistory));
        items.push(self.item("Preferences…", Action::Preferences));
        items.push(self.item("About Jumpkut", Action::About));
        items.push(
            CheckmarkItem {
                label: "Pause Recording".into(),
                checked: self.paused,
                activate: Box::new(|tray: &mut Self| {
                    tray.paused = !tray.paused;
                    (tray.on_action)(Action::Pause(tray.paused));
                }),
                ..Default::default()
            }
            .into(),
        );
        items.push(MenuItem::Separator);
        items.push(self.item("Back Up History…", Action::Backup));
        items.push(self.item("Restore Backup…", Action::Restore));
        items.push(self.item("Clear History…", Action::Clear));
        items.push(self.item("Quit", Action::Quit));
        items
    }
}

/// Rasterise the SVG icon at the sizes panels use (ARGB32, big-endian).
pub fn icon_pixmaps(svg: &[u8]) -> Vec<ksni::Icon> {
    use resvg::{tiny_skia, usvg};
    let Ok(tree) = usvg::Tree::from_data(svg, &usvg::Options::default()) else {
        return Vec::new();
    };
    [16u32, 22, 24, 32, 48, 64, 128]
        .iter()
        .filter_map(|&size| {
            let mut pixmap = tiny_skia::Pixmap::new(size, size)?;
            let scale = size as f32 / tree.size().width().max(tree.size().height());
            resvg::render(
                &tree,
                tiny_skia::Transform::from_scale(scale, scale),
                &mut pixmap.as_mut(),
            );
            let mut data = Vec::with_capacity((size * size * 4) as usize);
            for px in pixmap.pixels() {
                let c = px.demultiply();
                data.extend_from_slice(&[c.alpha(), c.red(), c.green(), c.blue()]);
            }
            Some(ksni::Icon {
                width: size as i32,
                height: size as i32,
                data,
            })
        })
        .collect()
}
