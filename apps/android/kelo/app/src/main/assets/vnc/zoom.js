// Zoom for Kelo's VNC page: pinch to zoom in on the picture, two fingers to move it while it is zoomed. No DOM in here (view.js wires it), so
// it can be tested on its own (server/tests/js/zoom_test.mjs).
//
// How taps stay right: the picture is never transformed. view.js makes the box noVNC draws into `zoom` times as big as the screen and moves
// the box by (x, y) (both <= 0). noVNC fits the picture into that box and maps a touch with the canvas's real position and its own scale,
// so a tap lands where the finger is, zoomed or not.
//
// The two-finger gestures noVNC knows (scroll the page, pinch = ctrl+wheel) are left alone until they are Kelo's: a gesture is ours when
// the two fingers change their distance (zoom), or move together while the picture is zoomed (pan). A two-finger move on the fitted
// picture is still noVNC's page scroll; a two-finger tap is still noVNC's right click.

export const MAX_ZOOM = 5;
export const FIT = Object.freeze({ zoom: 1, x: 0, y: 0 });
export const DECIDE_PX = 10;        // how far the fingers must move (distance, or together) before the gesture is decided
export const TOGETHER_MS = 250;     // the second finger must follow the first this fast (noVNC's own limit for a two-finger gesture)

/** The view kept inside the screen of `w` x `h`: zoom 1..MAX_ZOOM and the box always covering the screen. */
export function clampView(v, w, h) {
    const zoom = Math.min(MAX_ZOOM, Math.max(1, Number.isFinite(v.zoom) ? v.zoom : 1));
    const x = Math.min(0, Math.max(w - w * zoom, Number.isFinite(v.x) ? v.x : 0));
    const y = Math.min(0, Math.max(h - h * zoom, Number.isFinite(v.y) ? v.y : 0));
    return { zoom, x, y };
}

/** The centre and distance of the first two touches ({x, y} each). */
export function measure(touches) {
    const [a, b] = touches;
    return { cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2, dist: Math.hypot(a.x - b.x, a.y - b.y) };
}

/** The point of the fitted picture (zoom 1, the whole screen) that lies under the screen point (sx, sy) in `view`. */
export function fittedPoint(view, sx, sy) {
    return { x: (sx - view.x) / view.zoom, y: (sy - view.y) / view.zoom };
}

/** The view after two fingers went from `from` to `to` (both from measure()) with the picture in `start` at the beginning: the point
 *  that was under the fingers stays under them, whatever zoom and move they made. */
export function pinchView(start, from, to, w, h) {
    const zoom = Math.min(MAX_ZOOM, Math.max(1, start.zoom * (from.dist > 0 ? to.dist / from.dist : 1)));
    const under = fittedPoint(start, from.cx, from.cy);
    return clampView({ zoom, x: to.cx - under.x * zoom, y: to.cy - under.y * zoom }, w, h);
}

/** Decides which touches are Kelo's. `size()` -> {w, h} of the screen; `view` has get() and set(view).
 *  `touch(type, touches, time)` ('start' | 'move' | 'end', the touches on the screen after the event as {id, x, y}, ms) answers:
 *  'pass' (noVNC gets the event), 'claim' (the gesture just became ours: noVNC must forget the fingers; the event is not passed),
 *  'swallow' (ours: not passed). */
export class ZoomGesture {
    constructor(size, view) {
        this.size = size;
        this.view = view;
        this.state = 'idle';          // idle | watch (two fingers down, undecided) | remote (noVNC's) | ours
        this.firstAt = -Infinity;
        this.base = null;
        this.startView = FIT;
    }

    rebase(touches) {
        this.base = measure(touches);
        this.startView = this.view.get();
    }

    touch(type, touches, time) {
        const n = touches.length;
        if (type === 'start') {
            if (this.state === 'ours') {
                if (n >= 2) this.rebase(touches);
                return 'swallow';
            }
            if (n === 1) {
                this.state = 'idle';
                this.firstAt = time;
            } else if (n === 2 && this.state === 'idle' && time - this.firstAt <= TOGETHER_MS) {
                this.state = 'watch';
                this.rebase(touches);
            } else {
                this.state = 'remote';
            }
            return 'pass';
        }
        if (type === 'move') {
            if (this.state === 'ours') {
                if (n >= 2) {
                    const { w, h } = this.size();
                    this.view.set(pinchView(this.startView, this.base, measure(touches), w, h));
                }
                return 'swallow';
            }
            if (this.state !== 'watch' || n !== 2) return 'pass';
            const now = measure(touches);
            const spread = Math.abs(now.dist - this.base.dist);
            const moved = Math.hypot(now.cx - this.base.cx, now.cy - this.base.cy);
            if (spread > DECIDE_PX || (moved > DECIDE_PX && this.view.get().zoom > 1)) {
                this.state = 'ours';
                const { w, h } = this.size();
                this.view.set(pinchView(this.startView, this.base, now, w, h));
                return 'claim';
            }
            if (moved > DECIDE_PX) this.state = 'remote';
            return 'pass';
        }
        // end / cancel
        if (this.state === 'ours') {
            if (n >= 2) this.rebase(touches);
            else if (n === 0) this.state = 'idle';
            return 'swallow';
        }
        if (n === 0) this.state = 'idle';
        else if (this.state === 'watch') this.state = 'remote';
        return 'pass';
    }
}
