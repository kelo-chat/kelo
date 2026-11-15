// The plain data the generated KeloArt.swift is made of (ios/tools/gen_kelo_art.py), and the small geometry types the player
// (KeloMotion.swift) answers in. No UI framework: a SwiftUI Canvas turns these into Path / CGAffineTransform / Color.

/// A point in the SVGs' 120 × 120 viewBox.
public struct KeloPoint: Hashable, Sendable {
    public var x: Double
    public var y: Double
    public init(x: Double, y: Double) { self.x = x; self.y = y }
}

/// A colour with straight (not premultiplied) sRGB channels and alpha, each 0...1.
public struct KeloColor: Hashable, Sendable {
    public var red: Double
    public var green: Double
    public var blue: Double
    public var alpha: Double

    public init(red: Double, green: Double, blue: Double, alpha: Double = 1) {
        self.red = red; self.green = green; self.blue = blue; self.alpha = alpha
    }

    /// From 0xAARRGGBB.
    public init(argb: UInt32) {
        self.init(red: Double((argb >> 16) & 0xFF) / 255, green: Double((argb >> 8) & 0xFF) / 255,
                  blue: Double(argb & 0xFF) / 255, alpha: Double((argb >> 24) & 0xFF) / 255)
    }

    /// As 0xAARRGGBB (each channel rounded to 8 bits).
    public var argb: UInt32 {
        func ch(_ v: Double) -> UInt32 { UInt32((min(max(v, 0), 1) * 255).rounded()) }
        return ch(alpha) << 24 | ch(red) << 16 | ch(green) << 8 | ch(blue)
    }
}

public enum KeloLineCap: Sendable { case butt, round, square }
public enum KeloLineJoin: Sendable { case miter, round, bevel }

/// One shape of a drawing: its SVG path data and resolved paint. The fill is drawn first, then the stroke (miter limit 4, the SVG
/// default), both only when present.
public struct KeloShape: Sendable {
    /// SVG path data (absolute M/L/H/V/C/Q/A/Z as written in the SVG); `KeloPath.parse` turns it into moves, lines and curves.
    public let d: String
    public let fill: KeloColor?
    public let stroke: KeloColor?
    public let strokeWidth: Double
    public let cap: KeloLineCap
    public let join: KeloLineJoin
    /// The innermost animated group the shape is in (an index into `KeloMoodArt.groups`), nil when in none.
    public let group: Int?

    public init(_ d: String, fill: UInt32?, stroke: UInt32?, width: Double, cap: KeloLineCap, join: KeloLineJoin, group: Int?) {
        self.d = d
        self.fill = fill.map { KeloColor(argb: $0) }
        self.stroke = stroke.map { KeloColor(argb: $0) }
        self.strokeWidth = width
        self.cap = cap
        self.join = join
        self.group = group
    }
}

/// A CSS-animated element of a drawing (an SVG `<g>`, or a single shape): the shapes it holds and how they move.
public struct KeloGroup: Sendable {
    /// The element's CSS class in the SVG (e.g. "kelo-breathe").
    public let name: String
    /// The animated group it is in (an index into `KeloMoodArt.groups`), nil when at the top.
    public let parent: Int?
    /// The shapes it holds, nested groups included (indices into `KeloMoodArt.shapes`; always contiguous, SVG order).
    public let shapes: Range<Int>
    public let anim: KeloAnim

    public init(name: String, parent: Int?, shapes: Range<Int>, anim: KeloAnim) {
        self.name = name; self.parent = parent; self.shapes = shapes; self.anim = anim
    }
}

/// A CSS timing function.
public enum KeloEasing: Hashable, Sendable {
    case linear, ease, easeIn, easeOut, easeInOut
    /// `cubic-bezier(x1, y1, x2, y2)` (not used by the current SVGs; the generator refuses it until Android plays it too).
    case cubicBezier(Double, Double, Double, Double)
}

/// One function of a CSS transform list, after normalisation: translate(x, y), scale(x, y) or rotate(deg).
public enum KeloTransformOp: Sendable { case translate, scale, rotate }

/// One CSS animation: `animation: <duration> <timing> infinite [<delay>] [alternate]` with its `transform-origin` and @keyframes as
/// up to three tracks. Transform: `ops` (applied in order, as in CSS) with two numbers per op per keyframe in `tVals` (rotate's
/// second is unused; a missing function is padded with its identity). Opacity: `oVals`. Fill: `fVals` (ARGB). Each track's
/// `tKeys`/`oKeys`/`fKeys` are its keyframe offsets (0...1, always from 0 to 1; empty = the track is not animated). Before its delay
/// the element is at rest: no transform, `baseOpacity`, its own fill.
public struct KeloAnim: Sendable {
    public let durationMs: Int
    public let delayMs: Int
    public let easing: KeloEasing
    public let alternate: Bool
    /// The transform-origin, in viewBox units (`center` is the middle of the viewBox, as with transform-box: view-box).
    public let origin: KeloPoint
    public let ops: [KeloTransformOp]
    public let tKeys: [Double]
    public let tVals: [Double]
    public let oKeys: [Double]
    public let oVals: [Double]
    public let baseOpacity: Double
    public let fKeys: [Double]
    public let fVals: [KeloColor]

    public init(durationMs: Int, delayMs: Int = 0, easing: KeloEasing = .ease, alternate: Bool = false,
                origin: KeloPoint = KeloPoint(x: 0, y: 0), ops: [KeloTransformOp] = [], tKeys: [Double] = [], tVals: [Double] = [],
                oKeys: [Double] = [], oVals: [Double] = [], baseOpacity: Double = 1, fKeys: [Double] = [], fVals: [UInt32] = []) {
        self.durationMs = durationMs; self.delayMs = delayMs; self.easing = easing; self.alternate = alternate
        self.origin = origin; self.ops = ops; self.tKeys = tKeys; self.tVals = tVals
        self.oKeys = oKeys; self.oVals = oVals; self.baseOpacity = baseOpacity
        self.fKeys = fKeys; self.fVals = fVals.map { KeloColor(argb: $0) }
    }

    public var hasTransform: Bool { !ops.isEmpty && tKeys.count >= 2 }
    public var hasOpacity: Bool { oKeys.count >= 2 }
    public var hasFill: Bool { fKeys.count >= 2 }
}

/// What a group or the drawing's top level holds, in drawing order.
public enum KeloItem: Hashable, Sendable {
    case shape(Int)
    case group(Int)
}

/// A mood's drawing: its shapes in SVG (drawing) order and its animated groups (parents before their children).
public struct KeloMoodArt: Sendable {
    public let mood: KeloMood
    public let shapes: [KeloShape]
    public let groups: [KeloGroup]
    /// The top level's items (index 0) and each group's (index g + 1), in drawing order.
    private let itemLists: [[KeloItem]]

    public init(mood: KeloMood, shapes: [KeloShape], groups: [KeloGroup]) {
        self.mood = mood
        self.shapes = shapes
        self.groups = groups
        var lists: [[KeloItem]] = []
        for g in [Int?.none] + groups.indices.map(Int?.some) {
            let range = g.map { groups[$0].shapes } ?? 0..<shapes.count
            var items: [KeloItem] = []
            var i = range.lowerBound
            while i < range.upperBound {
                // the shape's own group, or the outermost group inside g that holds it
                var child = shapes[i].group
                while let c = child, groups[c].parent != g { child = groups[c].parent }
                if let c = child, c != g, groups[c].shapes.lowerBound == i {
                    items.append(.group(c))
                    i = max(groups[c].shapes.upperBound, i + 1)
                } else {
                    items.append(.shape(i))
                    i += 1
                }
            }
            lists.append(items)
        }
        itemLists = lists
    }

    /// What a group holds (nil: the top level), in drawing order: draw them in turn, recursing into groups.
    public func items(in group: Int?) -> [KeloItem] {
        itemLists[group.map { $0 + 1 } ?? 0]
    }

    /// The animations of the drawing, parents before children (the order of the Android app's KeloMotionTest).
    public var anims: [KeloAnim] { groups.map(\.anim) }
}
