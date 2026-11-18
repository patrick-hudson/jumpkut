//! Extras on the popup's own `wl_surface` (shared with winit through its
//! foreign `wl_display`, on a private event queue):
//!
//! * blur-behind: `ext_background_effect_v1` (KWin 6.8), else
//!   `org_kde_kwin_blur`, limited to the card's rounded shape;
//! * `org_kde_plasma_shell`: skip-taskbar/switcher from the moment the hidden
//!   popup exists, and the Notification role with "takes focus" only while it
//!   is shown. KWin's open/close effects (scale, fade, fadingpopups) animate
//!   only normal windows, dialogs and popups, so the popup appears instantly,
//!   and it still receives the keyboard.
//!
//!   Never give the role to the prebuilt, unmapped popup: KWin 6.8 then
//!   activates that invisible window (taking focus from the user's app) and
//!   crashes when the client disconnects.

use std::ffi::c_void;

use wayland_client::backend::{Backend, ObjectId};
use wayland_client::globals::{GlobalListContents, registry_queue_init};
use wayland_client::protocol::{
    wl_compositor::WlCompositor, wl_region::WlRegion, wl_registry, wl_surface::WlSurface,
};
use wayland_client::{Connection, Dispatch, EventQueue, Proxy, QueueHandle, delegate_noop};
use wayland_protocols::ext::background_effect::v1::client::{
    ext_background_effect_manager_v1::ExtBackgroundEffectManagerV1,
    ext_background_effect_surface_v1::ExtBackgroundEffectSurfaceV1,
};
use wayland_protocols_plasma::blur::client::{
    org_kde_kwin_blur::OrgKdeKwinBlur, org_kde_kwin_blur_manager::OrgKdeKwinBlurManager,
};
use wayland_protocols_plasma::plasma_shell::client::{
    org_kde_plasma_shell::OrgKdePlasmaShell,
    org_kde_plasma_surface::{OrgKdePlasmaSurface, Role},
};

use crate::region::Rect;

struct State;

impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for State {
    fn event(
        _: &mut Self,
        _: &wl_registry::WlRegistry,
        _: wl_registry::Event,
        _: &GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}
delegate_noop!(State: ignore WlCompositor);
delegate_noop!(State: ignore WlRegion);
delegate_noop!(State: ignore WlSurface);
delegate_noop!(State: ignore OrgKdeKwinBlurManager);
delegate_noop!(State: ignore OrgKdeKwinBlur);
delegate_noop!(State: ignore ExtBackgroundEffectManagerV1);
delegate_noop!(State: ignore ExtBackgroundEffectSurfaceV1);
delegate_noop!(State: ignore OrgKdePlasmaShell);
delegate_noop!(State: ignore OrgKdePlasmaSurface);

enum Manager {
    Ext(ExtBackgroundEffectManagerV1),
    Kde(OrgKdeKwinBlurManager),
    None,
}

enum Effect {
    Ext(ExtBackgroundEffectSurfaceV1),
    Kde(OrgKdeKwinBlur),
}

pub struct SurfaceExtras {
    conn: Connection,
    queue: EventQueue<State>,
    compositor: WlCompositor,
    blur: Manager,
    shell: Option<OrgKdePlasmaShell>,
    effect: Option<Effect>,
    /// The plasma surface and the `wl_surface` it belongs to.
    plasma: Option<(OrgKdePlasmaSurface, ObjectId)>,
}

impl SurfaceExtras {
    /// # Safety
    /// `display` must be the live `wl_display*` that winit uses.
    pub unsafe fn new(display: *mut c_void) -> Result<Self, String> {
        // SAFETY: guaranteed by the caller; winit keeps the display alive.
        let backend = unsafe { Backend::from_foreign_display(display.cast()) };
        let conn = Connection::from_backend(backend);
        let (globals, queue) =
            registry_queue_init::<State>(&conn).map_err(|e| format!("registry: {e}"))?;
        let qh = queue.handle();
        let blur = if let Ok(m) = globals.bind::<ExtBackgroundEffectManagerV1, _, _>(&qh, 1..=1, ())
        {
            Manager::Ext(m)
        } else if let Ok(m) = globals.bind::<OrgKdeKwinBlurManager, _, _>(&qh, 1..=1, ()) {
            Manager::Kde(m)
        } else {
            Manager::None
        };
        let shell = globals.bind::<OrgKdePlasmaShell, _, _>(&qh, 6..=8, ()).ok();
        let compositor = globals
            .bind::<WlCompositor, _, _>(&qh, 1..=4, ())
            .map_err(|e| format!("wl_compositor: {e}"))?;
        Ok(SurfaceExtras {
            conn,
            queue,
            compositor,
            blur,
            shell,
            effect: None,
            plasma: None,
        })
    }

    pub fn describe(&self) -> String {
        let blur = match self.blur {
            Manager::Ext(_) => "ext_background_effect_v1",
            Manager::Kde(_) => "org_kde_kwin_blur",
            Manager::None => "no blur protocol",
        };
        let shell = if self.shell.is_some() {
            "plasma-shell notification role"
        } else {
            "no plasma-shell"
        };
        format!("{blur}, {shell}")
    }

    /// Keep the hidden, not yet shown `surface` out of the taskbar and the
    /// window switcher (Plasma lists a toplevel before it is mapped).
    ///
    /// # Safety
    /// `surface` must be the live `wl_surface*` of a window on this display.
    pub unsafe fn prime(&mut self, surface: *mut c_void) -> Result<(), String> {
        // SAFETY: guaranteed by the caller.
        let surface = unsafe { self.surface(surface) }?;
        self.plasma_for(&surface);
        self.flush()
    }

    /// Apply the role and blur to `surface` before its first buffer.
    ///
    /// # Safety
    /// `surface` must be the live `wl_surface*` of a window on this display.
    pub unsafe fn apply(&mut self, surface: *mut c_void, rects: &[Rect]) -> Result<(), String> {
        // SAFETY: guaranteed by the caller.
        let surface = unsafe { self.surface(surface) }?;
        self.release_effect();
        let qh = self.queue.handle();
        if let Some(plasma) = self.plasma_for(&surface) {
            plasma.set_role(Role::Notification as u32);
            plasma.set_panel_takes_focus(1);
        }
        let region = self.compositor.create_region(&qh, ());
        for r in rects {
            region.add(r.x, r.y, r.w, r.h);
        }
        self.effect = match &self.blur {
            Manager::Ext(m) => {
                let effect = m.get_background_effect(&surface, &qh, ());
                effect.set_blur_region(Some(&region));
                Some(Effect::Ext(effect))
            }
            Manager::Kde(m) => {
                let blur = m.create(&surface, &qh, ());
                blur.set_region(Some(&region));
                blur.commit();
                Some(Effect::Kde(blur))
            }
            Manager::None => None,
        };
        region.destroy();
        self.flush()
    }

    /// # Safety
    /// `surface` must be the live `wl_surface*` of a window on this display.
    unsafe fn surface(&self, surface: *mut c_void) -> Result<WlSurface, String> {
        // SAFETY: guaranteed by the caller.
        let id = unsafe { ObjectId::from_ptr(WlSurface::interface(), surface.cast()) }
            .map_err(|_| "invalid wl_surface")?;
        WlSurface::from_id(&self.conn, id).map_err(|_| "invalid wl_surface".into())
    }

    /// The skip-taskbar/switcher plasma surface of `surface`, created once.
    fn plasma_for(&mut self, surface: &WlSurface) -> Option<&OrgKdePlasmaSurface> {
        if self
            .plasma
            .as_ref()
            .is_some_and(|(_, id)| *id != surface.id())
        {
            self.release();
        }
        if self.plasma.is_none() {
            let shell = self.shell.as_ref()?;
            let plasma = shell.get_surface(surface, &self.queue.handle(), ());
            plasma.set_skip_taskbar(1);
            plasma.set_skip_switcher(1);
            self.plasma = Some((plasma, surface.id()));
        }
        self.plasma.as_ref().map(|(p, _)| p)
    }

    fn release_effect(&mut self) {
        match self.effect.take() {
            Some(Effect::Ext(e)) => e.destroy(),
            Some(Effect::Kde(b)) => b.release(),
            None => {}
        }
    }

    /// Drop the per-surface objects (before the surface is destroyed).
    pub fn release(&mut self) {
        self.release_effect();
        if let Some((plasma, _)) = self.plasma.take() {
            plasma.destroy();
        }
        let _ = self.flush();
    }

    fn flush(&mut self) -> Result<(), String> {
        self.queue
            .dispatch_pending(&mut State)
            .map_err(|e| e.to_string())?;
        self.conn.flush().map_err(|e| e.to_string())
    }
}
