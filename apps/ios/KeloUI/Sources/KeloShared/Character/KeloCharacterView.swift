import SwiftUI
import KeloCore

/// Kelo's character, drawn natively from KeloCore's art (the 11 SVGs of apps/android/design/character turned into shapes and CSS
/// animations) and played by `KeloMotion`, like Android ui/character/KeloCharacter.kt: each mood starts from its own first frame, a
/// change of mood cross-fades over `KeloMotion.fadeMs`, calm moods redraw at about 30 fps, and with Reduce Motion it holds the
/// mood's still frame.
public struct KeloCharacterView: View {
    let mood: KeloMood
    @Environment(\.accessibilityReduceMotion) private var reduce
    @State private var track = MoodTrack()

    public init(_ mood: KeloMood) { self.mood = mood }

    public var body: some View {
        Group {
            if reduce {
                Canvas { ctx, size in
                    KeloRenderer.draw(KeloMotion.stillFrame(mood), in: &ctx, size: size, alpha: 1)
                }
            } else {
                TimelineView(.animation(minimumInterval: KeloMotion.calm.contains(mood) ? Double(KeloMotion.calmFrameMs) / 1000 : nil)) { tl in
                    Canvas { ctx, size in
                        let now = tl.date.timeIntervalSinceReferenceDate
                        let state = track.state(for: mood, now: now)
                        if let before = state.before, state.fade < 1 {
                            KeloRenderer.draw(KeloMotion.frame(before.mood, at: before.ms), in: &ctx, size: size, alpha: 1 - state.fade)
                        }
                        KeloRenderer.draw(KeloMotion.frame(mood, at: state.nowMs), in: &ctx, size: size,
                                          alpha: state.before == nil ? 1 : state.fade)
                    }
                }
            }
        }
        .accessibilityHidden(true)
    }
}

/// The mood on screen, the one fading out, and when each began. A reference type mutated while drawing (no state change per frame).
final class MoodTrack: @unchecked Sendable {
    private var now: KeloMood?
    private var nowStart: Double = 0
    private var before: KeloMood?
    private var beforeStart: Double = 0

    struct State {
        var nowMs: Int
        var before: (mood: KeloMood, ms: Int)?
        var fade: Double
    }

    func state(for mood: KeloMood, now t: Double) -> State {
        if now == nil { now = mood; nowStart = t }
        if now != mood {
            before = now; beforeStart = nowStart
            now = mood; nowStart = t
        }
        let fadeS = Double(KeloMotion.fadeMs) / 1000
        let f = min(max((t - nowStart) / fadeS, 0), 1)
        if f >= 1 { before = nil }
        let ms = Int(((t - nowStart) * 1000).rounded())
        return State(nowMs: ms, before: before.map { ($0, Int(((t - beforeStart) * 1000).rounded())) }, fade: before == nil ? 1 : f)
    }
}

/// Draws a `KeloFrame` into a Canvas (the recipe at the top of KeloCore's KeloMotion.swift).
enum KeloRenderer {
    private static let cache = PathCache()

    static func draw(_ frame: KeloFrame, in ctx: inout GraphicsContext, size: CGSize, alpha: Double) {
        guard alpha > 0 else { return }
        let art = KeloArt.of(frame.mood)
        let view = KeloArt.viewBox
        let k = min(size.width, size.height) / view
        var c = ctx
        c.translateBy(x: (size.width - view * k) / 2, y: (size.height - view * k) / 2)
        c.scaleBy(x: k, y: k)
        if alpha >= 1 {
            drawItems(art.items(in: nil), art: art, frame: frame, fill: nil, ctx: c)
        } else {
            c.drawLayer { layer in
                layer.opacity = alpha
                drawItems(art.items(in: nil), art: art, frame: frame, fill: nil, ctx: layer)
            }
        }
    }

    private static func drawItems(_ items: [KeloItem], art: KeloMoodArt, frame: KeloFrame, fill: KeloColor?, ctx: GraphicsContext) {
        for item in items {
            switch item {
            case .shape(let i):
                let s = art.shapes[i]
                let path = cache.path(mood: art.mood, index: i, d: s.d)
                if let f = s.fill { ctx.fill(path, with: .color(color(fill ?? f))) }
                if let st = s.stroke {
                    ctx.stroke(path, with: .color(color(st)), style: StrokeStyle(
                        lineWidth: s.strokeWidth, lineCap: cap(s.cap), lineJoin: join(s.join), miterLimit: 4))
                }
            case .group(let g):
                let st = g < frame.groups.count ? frame.groups[g] : .rest
                guard st.opacity > 0 else { continue }
                var c = ctx
                let t = st.transform
                if !t.isIdentity { c.concatenate(CGAffineTransform(a: t.a, b: t.b, c: t.c, d: t.d, tx: t.tx, ty: t.ty)) }
                let inner = art.items(in: g)
                if st.opacity < 1 {
                    c.drawLayer { layer in
                        layer.opacity = st.opacity
                        drawItems(inner, art: art, frame: frame, fill: st.fill, ctx: layer)
                    }
                } else {
                    drawItems(inner, art: art, frame: frame, fill: st.fill, ctx: c)
                }
            }
        }
    }

    private static func color(_ k: KeloColor) -> Color { Color(.sRGB, red: k.red, green: k.green, blue: k.blue, opacity: k.alpha) }
    private static func cap(_ c: KeloLineCap) -> CGLineCap { switch c { case .butt: .butt; case .round: .round; case .square: .square } }
    private static func join(_ j: KeloLineJoin) -> CGLineJoin { switch j { case .miter: .miter; case .round: .round; case .bevel: .bevel } }
}

/// Parsed paths, built once per shape on first draw.
final class PathCache: @unchecked Sendable {
    private var paths: [KeloMood: [Int: Path]] = [:]
    private let lock = NSLock()

    func path(mood: KeloMood, index: Int, d: String) -> Path {
        lock.lock(); defer { lock.unlock() }
        if let p = paths[mood]?[index] { return p }
        var p = Path()
        for el in (try? KeloPath.parse(d)) ?? [] {
            switch el {
            case .move(let a): p.move(to: CGPoint(x: a.x, y: a.y))
            case .line(let a): p.addLine(to: CGPoint(x: a.x, y: a.y))
            case .quad(let c, let to): p.addQuadCurve(to: CGPoint(x: to.x, y: to.y), control: CGPoint(x: c.x, y: c.y))
            case .cubic(let c1, let c2, let to):
                p.addCurve(to: CGPoint(x: to.x, y: to.y), control1: CGPoint(x: c1.x, y: c1.y), control2: CGPoint(x: c2.x, y: c2.y))
            case .close: p.closeSubpath()
            }
        }
        paths[mood, default: [:]][index] = p
        return p
    }
}

public extension KeloMood {
    /// How far (in the art's 120-unit view box) to move the drawing right so the body sits in the middle of a round badge
    /// (Android `centerShift`).
    var centerShift: Double {
        switch self {
        case .idle, .done, .error, .reading: 0
        case .working, .browser, .offline: 8
        case .approval, .listening: 6
        case .sleeping: 4
        case .thinking: 2
        }
    }
}

/// The character at `timeMs` into a mood's animation, still (the app icon, previews; Android KeloCharacterAt).
public struct KeloCharacterStill: View {
    let mood: KeloMood
    let timeMs: Int
    public init(_ mood: KeloMood, timeMs: Int = 0) { self.mood = mood; self.timeMs = timeMs }
    public var body: some View {
        Canvas { ctx, size in KeloRenderer.draw(KeloMotion.frame(mood, at: timeMs), in: &ctx, size: size, alpha: 1) }
            .accessibilityHidden(true)
    }
}
