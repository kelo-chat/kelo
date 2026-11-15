/*
 Kelo's character player: the CSS animations of android/design/character/kelo_<mood>.svg, played the way a browser (and the Android
 app's KeloMotion in KeloCharacter.kt) does. Pure math; the art is in the generated KeloArt.swift.

 How a SwiftUI renderer uses it (one Canvas, redrawn from a TimelineView):
   - Scale the 120 × 120 viewBox (`KeloArt.viewBox`) to fit the canvas, centred.
   - t = ms since this mood started showing (each mood starts from its own first frame). With reduced motion draw only
     `KeloMotion.stillAt(mood)`. Moods in `KeloMotion.calm` need only redraw every `KeloMotion.calmFrameMs` ms (about 30 fps).
   - `let art = KeloArt.of(mood)`, `let frame = KeloMotion.frame(mood, at: t)`, then draw `art.items(in: nil)` in order:
       .shape(i): parse `art.shapes[i].d` once (`KeloPath.parse`, cache the Path), fill it with the shape's fill — or with the
                  enclosing group's `frame.groups[g].fill` when that is non-nil (only the shape's own group; never inherited by
                  nested groups) — when the shape has a fill, then stroke it (width, cap, join, miter limit 4) when it has a stroke.
       .group(g): let s = frame.groups[g]; skip it (and all inside) when s.opacity <= 0; otherwise in a copy of the context
                  (`var c = context`) concatenate `CGAffineTransform(a: s.transform.a, b: s.transform.b, c: s.transform.c,
                  d: s.transform.d, tx: s.transform.tx, ty: s.transform.ty)`, and when s.opacity < 1 draw the group's items inside
                  `c.drawLayer { … }` after setting `c.opacity = s.opacity` (a group fades as one layer, as in CSS, not shape by shape);
                  then draw `art.items(in: g)` the same way.
   - A change of mood cross-fades over `KeloMotion.fadeMs` ms (linear): the old mood drawn at 1 − f opacity, the new at f, each in
     its own layer and on its own clock.
*/

import Foundation

/// A 2-D affine transform: x' = a·x + c·y + tx, y' = b·x + d·y + ty (the layout of CGAffineTransform).
public struct KeloTransform: Hashable, Sendable {
    public var a: Double, b: Double, c: Double, d: Double, tx: Double, ty: Double

    public init(a: Double, b: Double, c: Double, d: Double, tx: Double, ty: Double) {
        self.a = a; self.b = b; self.c = c; self.d = d; self.tx = tx; self.ty = ty
    }

    public static let identity = KeloTransform(a: 1, b: 0, c: 0, d: 1, tx: 0, ty: 0)

    public static func translation(_ x: Double, _ y: Double) -> KeloTransform {
        KeloTransform(a: 1, b: 0, c: 0, d: 1, tx: x, ty: y)
    }

    public static func scale(_ x: Double, _ y: Double) -> KeloTransform {
        KeloTransform(a: x, b: 0, c: 0, d: y, tx: 0, ty: 0)
    }

    /// A rotation by `degrees`, clockwise on screen (y down), as CSS rotate().
    public static func rotation(degrees: Double) -> KeloTransform {
        let r = degrees * Double.pi / 180
        let cs = cos(r), sn = sin(r)
        return KeloTransform(a: cs, b: sn, c: -sn, d: cs, tx: 0, ty: 0)
    }

    /// `l * r` applies `r` first, then `l` (matrix product; a CSS transform list `f g` is `f * g`).
    public static func * (l: KeloTransform, r: KeloTransform) -> KeloTransform {
        KeloTransform(a: l.a * r.a + l.c * r.b, b: l.b * r.a + l.d * r.b,
                      c: l.a * r.c + l.c * r.d, d: l.b * r.c + l.d * r.d,
                      tx: l.a * r.tx + l.c * r.ty + l.tx, ty: l.b * r.tx + l.d * r.ty + l.ty)
    }

    public func apply(_ p: KeloPoint) -> KeloPoint {
        KeloPoint(x: a * p.x + c * p.y + tx, y: b * p.x + d * p.y + ty)
    }

    public var isIdentity: Bool { self == .identity }
}

/// How an animated group looks at one moment.
public struct KeloGroupState: Hashable, Sendable {
    /// The group's own transform, about its transform-origin (identity at rest); compose with its parents' for the whole of it.
    public var transform: KeloTransform
    /// The group's opacity as a layer (0 = draw nothing of it).
    public var opacity: Double
    /// The fill of the group's own shapes (those whose `group` is this one), nil = their own fill.
    public var fill: KeloColor?

    public static let rest = KeloGroupState(transform: .identity, opacity: 1, fill: nil)
}

/// A mood's drawing at one moment: one state per group of `KeloArt.of(mood).groups`.
public struct KeloFrame: Sendable {
    public let mood: KeloMood
    public let timeMs: Int
    public let groups: [KeloGroupState]

    /// The whole transform of a group: its parents' and its own (identity for nil, the top level).
    public func worldTransform(of group: Int?) -> KeloTransform {
        let art = KeloArt.of(mood)
        var m = KeloTransform.identity
        var g = group
        while let i = g {
            m = groups[i].transform * m
            g = art.groups[i].parent
        }
        return m
    }

    /// The whole transform of a shape (its groups').
    public func transform(ofShape shape: Int) -> KeloTransform {
        worldTransform(of: KeloArt.of(mood).shapes[shape].group)
    }

    /// The shape's fill this frame (its group's animated fill, or its own; nil when it has none).
    public func fill(ofShape shape: Int) -> KeloColor? {
        let s = KeloArt.of(mood).shapes[shape]
        guard let own = s.fill else { return nil }
        return s.group.flatMap { groups[$0].fill } ?? own
    }

    /// The product of the opacities of the shape's groups (0 = not drawn this frame). For hit tests and checks only: a renderer
    /// composites each group as a layer instead of multiplying.
    public func opacity(ofShape shape: Int) -> Double {
        let art = KeloArt.of(mood)
        var o = 1.0
        var g = art.shapes[shape].group
        while let i = g {
            o *= groups[i].opacity
            g = art.groups[i].parent
        }
        return o
    }
}

/// Plays a `KeloAnim` the way a browser does: the timing function applies between each pair of keyframes of a track, "alternate"
/// runs play back on odd iterations, every animation repeats for ever, and before its delay the element is at rest. Mirrors the
/// Android app's `KeloMotion` (android/kelo/.../ui/character/KeloCharacter.kt).
public enum KeloMotion {
    /// A change of mood cross-fades this long (ms), linearly.
    public static let fadeMs: Int = 150

    /// Moods whose motion is slow (≥ 1.4 s breaths, sways, pulses): drawn at about 30 frames a second.
    public static let calm: Set<KeloMood> = [.idle, .sleeping, .offline, .reading]

    /// How often a calm mood needs a new frame (ms).
    public static let calmFrameMs: Int = 32

    /// The least time between two frames of a mood (ms): `calmFrameMs` for calm moods, 0 (every display frame) for the others.
    public static func frameStepMs(for mood: KeloMood) -> Int { calm.contains(mood) ? calmFrameMs : 0 }

    /// The still picture with reduced motion (ms into the mood): one that says what the mood is.
    public static func stillAt(_ mood: KeloMood) -> Int {
        switch mood {
        case .working, .browser: return 1_800   // at the desk (their first frame faces front)
        case .thinking: return 750              // the first dot up
        case .done: return 360                  // at the top of the jump
        case .sleeping: return 1_900            // both z's in the air
        case .listening: return 600             // the first wave bright
        default: return 0
        }
    }

    /// How far into the current iteration (0..<1) at `t` ms, or -1 before the delay.
    public static func progress(_ a: KeloAnim, _ t: Int) -> Double {
        let local = t - a.delayMs
        if local < 0 { return -1 }
        let d = a.durationMs
        let p = Double(local % d) / Double(d)
        return a.alternate && (local / d) % 2 == 1 ? 1 - p : p
    }

    /// The keyframe interval of a track that holds `p`, and the eased progress through it.
    private static func segment(_ keys: [Double], _ p: Double, _ easing: KeloEasing) -> (Int, Double) {
        var i = 0
        while i < keys.count - 2 && p >= keys[i + 1] { i += 1 }
        let span = keys[i + 1] - keys[i]
        let x = span <= 0 ? 1 : min(max((p - keys[i]) / span, 0), 1)
        return (i, easing.transform(x))
    }

    /// The transform functions' numbers at progress `p` (two per op, as in `KeloAnim.tVals`); nil when at rest (before the delay)
    /// or when the animation does not move anything.
    public static func transformValues(_ a: KeloAnim, _ p: Double) -> [Double]? {
        if p < 0 || !a.hasTransform { return nil }
        let n = a.ops.count * 2
        let (i, e) = segment(a.tKeys, p, a.easing)
        return (0..<n).map { j in
            let from = a.tVals[i * n + j]
            let to = a.tVals[(i + 1) * n + j]
            return from + (to - from) * e
        }
    }

    /// The transform at progress `p` about the animation's transform-origin: translate(origin) · ops… · translate(−origin), as CSS
    /// does. Identity at rest.
    public static func transform(_ a: KeloAnim, _ p: Double) -> KeloTransform {
        guard let v = transformValues(a, p) else { return .identity }
        var m = KeloTransform.translation(a.origin.x, a.origin.y)
        for (k, op) in a.ops.enumerated() {
            let x = v[k * 2], y = v[k * 2 + 1]
            switch op {
            case .translate: m = m * .translation(x, y)
            case .scale: m = m * .scale(x, y)
            case .rotate: m = m * .rotation(degrees: x)
            }
        }
        return m * .translation(-a.origin.x, -a.origin.y)
    }

    /// The opacity at progress `p`; `baseOpacity` at rest or when opacity is not animated.
    public static func opacity(_ a: KeloAnim, _ p: Double) -> Double {
        if p < 0 || !a.hasOpacity { return a.baseOpacity }
        let (i, e) = segment(a.oKeys, p, a.easing)
        return a.oVals[i] + (a.oVals[i + 1] - a.oVals[i]) * e
    }

    /// The animated fill, mixed channel by channel in sRGB (alpha too) as CSS does; nil (the shape's own fill) before the delay or
    /// when fill is not animated.
    public static func fill(_ a: KeloAnim, _ p: Double) -> KeloColor? {
        if p < 0 || !a.hasFill { return nil }
        let (i, e) = segment(a.fKeys, p, a.easing)
        let from = a.fVals[i], to = a.fVals[i + 1]
        func mix(_ f: Double, _ t: Double) -> Double { f + (t - f) * e }
        return KeloColor(red: mix(from.red, to.red), green: mix(from.green, to.green), blue: mix(from.blue, to.blue),
                         alpha: mix(from.alpha, to.alpha))
    }

    /// One animated group at `t` ms.
    public static func state(_ a: KeloAnim, at t: Int) -> KeloGroupState {
        let p = progress(a, t)
        return KeloGroupState(transform: transform(a, p), opacity: opacity(a, p), fill: fill(a, p))
    }

    /// A mood's drawing at `t` ms into it.
    public static func frame(_ mood: KeloMood, at t: Int) -> KeloFrame {
        KeloFrame(mood: mood, timeMs: t, groups: KeloArt.of(mood).groups.map { state($0.anim, at: t) })
    }

    /// The still picture shown with reduced motion.
    public static func stillFrame(_ mood: KeloMood) -> KeloFrame { frame(mood, at: stillAt(mood)) }
}
