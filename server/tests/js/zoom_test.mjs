// Run by server/tests/test_vnc_zoom.py with node: the VNC page's zoom (apps/android/.../assets/vnc/zoom.js). Exits 1 on the first failure.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';

const { FIT, MAX_ZOOM, ZoomGesture, clampView, fittedPoint, measure, pinchView } = await import(pathToFileURL(process.argv[2]).href);

const W = 400, H = 800;
const two = (a, b) => [{ id: 1, x: a[0], y: a[1] }, { id: 2, x: b[0], y: b[1] }];
const near = (got, want, eps = 1e-6) => assert.ok(Math.abs(got - want) < eps, `${got} != ${want}`);

// the point under the fingers stays under the fingers (that is where the tap of the next touch lands)
{
    const from = measure(two([150, 400], [250, 400]));
    const to = measure(two([100, 400], [300, 400]));            // fingers twice as far apart
    const v = pinchView(FIT, from, to, W, H);
    near(v.zoom, 2);
    const before = fittedPoint(FIT, from.cx, from.cy), after = fittedPoint(v, to.cx, to.cy);
    near(before.x, after.x); near(before.y, after.y);
}

// zoom and view stay inside the screen: never smaller than the fitted picture, never bigger than MAX_ZOOM, always covering the screen
{
    const out = pinchView(FIT, measure(two([100, 100], [300, 100])), measure(two([190, 100], [210, 100])), W, H);
    assert.deepEqual(out, FIT);
    const big = pinchView(FIT, measure(two([190, 400], [210, 400])), measure(two([0, 400], [400, 400])), W, H);
    near(big.zoom, MAX_ZOOM);
    for (const v of [big, clampView({ zoom: 3, x: 50, y: 50 }, W, H), clampView({ zoom: 3, x: -5000, y: -5000 }, W, H)]) {
        assert.ok(v.x <= 0 && v.x >= W - W * v.zoom && v.y <= 0 && v.y >= H - H * v.zoom, JSON.stringify(v));
    }
    assert.deepEqual(clampView({ zoom: NaN, x: NaN, y: NaN }, W, H), FIT);
}

// a pan while zoomed moves the picture with the fingers; a pan at zoom 1 moves nothing
{
    const z = { zoom: 2, x: -200, y: -400 };
    const moved = pinchView(z, measure(two([150, 400], [250, 400])), measure(two([190, 450], [290, 450])), W, H);
    near(moved.zoom, 2); near(moved.x, -160); near(moved.y, -350);
    assert.deepEqual(pinchView(FIT, measure(two([150, 400], [250, 400])), measure(two([190, 450], [290, 450])), W, H), FIT);
}

// a tap on a zoomed picture maps back to the fitted picture (what noVNC then maps to the X screen by its own scale)
{
    const v = { zoom: 2, x: -200, y: -400 };
    const p = fittedPoint(v, 100, 100);
    near(p.x, 150); near(p.y, 250);
    const q = fittedPoint(FIT, 100, 100);
    near(q.x, 100); near(q.y, 100);
}

function gesture(view = FIT) {
    const box = { v: view };
    return { box, g: new ZoomGesture(() => ({ w: W, h: H }), { get: () => box.v, set: (x) => { box.v = x; } }) };
}

// pinch: the two fingers land together, move apart: 'claim' once, then 'swallow' until they lift
{
    const { box, g } = gesture();
    assert.equal(g.touch('start', two([150, 400], [150, 400]).slice(0, 1), 1000), 'pass');
    assert.equal(g.touch('start', two([150, 400], [250, 400]), 1050), 'pass');
    assert.equal(g.touch('move', two([145, 400], [255, 400]), 1060), 'pass');       // 10 px: not decided yet
    assert.equal(g.touch('move', two([100, 400], [300, 400]), 1070), 'claim');
    assert.equal(g.touch('move', two([50, 400], [350, 400]), 1080), 'swallow');
    assert.ok(box.v.zoom > 2);
    assert.equal(g.touch('end', two([50, 400], [350, 400]).slice(0, 1), 1090), 'swallow');
    assert.equal(g.touch('end', [], 1100), 'swallow');
    assert.equal(g.touch('start', [{ id: 1, x: 5, y: 5 }], 2000), 'pass');          // the next touch is noVNC's again
}

// a two-finger move on the fitted picture is noVNC's page scroll; a two-finger tap is noVNC's right click
{
    const { box, g } = gesture();
    g.touch('start', [{ id: 1, x: 150, y: 400 }], 1000);
    g.touch('start', two([150, 400], [250, 400]), 1020);
    assert.equal(g.touch('move', two([150, 450], [250, 450]), 1040), 'pass');
    assert.equal(g.touch('move', two([150, 500], [250, 500]), 1060), 'pass');
    assert.deepEqual(box.v, FIT);
    g.touch('end', [], 1100);
    g.touch('start', [{ id: 1, x: 150, y: 400 }], 3000);
    g.touch('start', two([150, 400], [250, 400]), 3020);
    assert.equal(g.touch('end', [{ id: 1, x: 150, y: 400 }], 3050), 'pass');
    assert.equal(g.touch('end', [], 3060), 'pass');
}

// zoomed: two fingers moving together pan the picture (ours)
{
    const { box, g } = gesture({ zoom: 2, x: -200, y: -400 });
    g.touch('start', [{ id: 1, x: 150, y: 400 }], 1000);
    g.touch('start', two([150, 400], [250, 400]), 1020);
    assert.equal(g.touch('move', two([190, 450], [290, 450]), 1040), 'claim');
    near(box.v.x, -160); near(box.v.y, -350);
}

// a second finger that comes late is not a two-finger gesture (noVNC ignores it too)
{
    const { box, g } = gesture();
    g.touch('start', [{ id: 1, x: 150, y: 400 }], 1000);
    g.touch('start', two([150, 400], [250, 400]), 1600);
    assert.equal(g.touch('move', two([50, 400], [350, 400]), 1620), 'pass');
    assert.deepEqual(box.v, FIT);
}

// a third finger ends it for us before it was decided
{
    const { box, g } = gesture();
    g.touch('start', [{ id: 1, x: 150, y: 400 }], 1000);
    g.touch('start', two([150, 400], [250, 400]), 1020);
    g.touch('start', [...two([150, 400], [250, 400]), { id: 3, x: 200, y: 300 }], 1030);
    assert.equal(g.touch('move', [...two([50, 400], [350, 400]), { id: 3, x: 200, y: 300 }], 1040), 'pass');
    assert.deepEqual(box.v, FIT);
}
console.log('zoom ok');
