//! Rounded-rectangle regions as stacks of rectangles, for KWin blur-behind
//! (neither `org_kde_kwin_blur` nor `_KDE_NET_WM_BLUR_BEHIND_REGION` takes a
//! corner radius, so corners are approximated scanline by scanline, the way
//! Plasma's own `QRegion(..., QRegion::Ellipse)` approach does).

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

pub fn rounded_rect(x: i32, y: i32, w: i32, h: i32, radius: i32) -> Vec<Rect> {
    let r = radius.clamp(0, w.min(h) / 2);
    if r == 0 {
        return vec![Rect { x, y, w, h }];
    }
    // Horizontal inset for each scanline of the top corner band.
    let inset = |row: i32| -> i32 {
        let dy = r as f64 - row as f64 - 0.5;
        (r as f64 - (r as f64 * r as f64 - dy * dy).max(0.0).sqrt()).round() as i32
    };
    let mut rows: Vec<Rect> = Vec::new();
    let push = |rect: Rect, rows: &mut Vec<Rect>| {
        // Merge with the previous rect when it has the same horizontal extent.
        if let Some(last) = rows.last_mut()
            && last.x == rect.x
            && last.w == rect.w
            && last.y + last.h == rect.y
        {
            last.h += rect.h;
            return;
        }
        rows.push(rect);
    };
    for row in 0..r {
        let i = inset(row);
        push(
            Rect {
                x: x + i,
                y: y + row,
                w: w - 2 * i,
                h: 1,
            },
            &mut rows,
        );
    }
    push(
        Rect {
            x,
            y: y + r,
            w,
            h: h - 2 * r,
        },
        &mut rows,
    );
    for row in (0..r).rev() {
        let i = inset(row);
        push(
            Rect {
                x: x + i,
                y: y + h - 1 - row,
                w: w - 2 * i,
                h: 1,
            },
            &mut rows,
        );
    }
    rows
}
