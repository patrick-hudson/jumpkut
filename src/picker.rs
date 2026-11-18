//! The popup's cycle/selection state, independent of any toolkit.
//!
//! The shortcut opens at #1 (or the resumed clipping); repeating it, Tab or
//! the arrows move (wrapping); releasing the held modifiers selects (unless
//! sticky); Enter selects; Esc cancels.

#[derive(Debug, Default)]
pub struct Picker {
    open: bool,
    len: usize,
    index: usize,
    release_selects: bool,
    /// The held modifiers were seen down while the popup had focus.
    held_seen: bool,
}

impl Picker {
    pub fn is_open(&self) -> bool {
        self.open
    }

    pub fn index(&self) -> usize {
        self.index
    }

    /// Open on `len` clippings at `start` (0 = newest).
    pub fn open(&mut self, len: usize, start: usize, release_selects: bool) {
        *self = Picker {
            open: true,
            len,
            index: start.min(len.saturating_sub(1)),
            release_selects,
            held_seen: false,
        };
    }

    /// Move by `delta`, wrapping around. Returns whether anything changed.
    pub fn step(&mut self, delta: i64) -> bool {
        if !self.open || self.len == 0 {
            return false;
        }
        self.index = (self.index as i64 + delta).rem_euclid(self.len as i64) as usize;
        true
    }

    pub fn first(&mut self) -> bool {
        self.jump(0)
    }

    pub fn last(&mut self) -> bool {
        self.jump(self.len.saturating_sub(1))
    }

    fn jump(&mut self, index: usize) -> bool {
        if !self.open || self.len == 0 {
            return false;
        }
        self.index = index;
        true
    }

    /// The current clipping was deleted from the list.
    pub fn remove_current(&mut self) {
        if self.open && self.len > 0 {
            self.len -= 1;
            self.index = self.index.min(self.len.saturating_sub(1));
        }
    }

    /// Close; `Some(index)` if a clipping was selected.
    pub fn accept(&mut self) -> Option<usize> {
        if !self.open {
            return None;
        }
        self.open = false;
        (self.len > 0).then_some(self.index)
    }

    /// Close without selecting; returns whether it was open.
    pub fn cancel(&mut self) -> bool {
        std::mem::replace(&mut self.open, false)
    }

    /// The held modifiers changed; true when their release should select.
    pub fn release(&mut self, held: bool) -> bool {
        if !self.open || !self.release_selects {
            return false;
        }
        if held {
            self.held_seen = true;
            false
        } else {
            self.held_seen
        }
    }

    /// Focus arrived and the modifiers were never reported held: they were
    /// released before the popup could see it, so select right away.
    pub fn focus_settled(&self) -> bool {
        self.open && self.release_selects && !self.held_seen
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opens_cycles_and_wraps() {
        let mut p = Picker::default();
        p.open(3, 0, true);
        assert_eq!(p.index(), 0);
        p.step(1);
        p.step(1);
        p.step(1);
        assert_eq!(p.index(), 0, "wraps after the oldest");
        p.step(-1);
        assert_eq!(p.index(), 2, "up from #1 wraps to the oldest");
        assert!(p.first() && p.index() == 0);
        assert!(p.last() && p.index() == 2);
    }

    #[test]
    fn release_after_hold_selects_and_early_release_waits_for_focus() {
        let mut p = Picker::default();
        p.open(5, 0, true);
        assert!(
            !p.release(false),
            "a release before the hold was seen does not select"
        );
        assert!(
            p.focus_settled(),
            "…but focus arriving without the hold does"
        );
        assert!(!p.release(true));
        p.step(2);
        assert!(p.release(false));
        assert_eq!(p.accept(), Some(2));
        assert!(!p.release(false), "closed");
    }

    #[test]
    fn resume_start_sticky_cancel_and_delete() {
        let mut p = Picker::default();
        p.open(4, 2, false);
        assert_eq!(p.index(), 2, "resumes at the last selection");
        p.release(true);
        assert!(
            !p.release(false) && !p.focus_settled(),
            "sticky ignores the release"
        );
        p.last();
        p.remove_current();
        assert_eq!(p.index(), 2, "deleting the oldest moves to the new oldest");
        assert!(p.cancel());
        assert!(!p.cancel());
        p.open(9, 99, true);
        assert_eq!(p.index(), 8, "start is clamped");
        p.open(0, 0, true);
        assert!(!p.step(1));
        assert_eq!(p.accept(), None, "empty history never selects");
    }
}
