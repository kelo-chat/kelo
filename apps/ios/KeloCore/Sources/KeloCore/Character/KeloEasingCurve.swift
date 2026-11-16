/// CSS timing functions as cubic Bézier curves from (0, 0) to (1, 1): `ease` = (0.25, 0.1, 0.25, 1), `ease-in` = (0.42, 0, 1, 1),
/// `ease-out` = (0, 0, 0.58, 1), `ease-in-out` = (0.42, 0, 0.58, 1) (the values of Compose's CubicBezierEasing on Android).
public struct KeloCubicBezier: Hashable, Sendable {
    public let x1: Double, y1: Double, x2: Double, y2: Double

    public init(_ x1: Double, _ y1: Double, _ x2: Double, _ y2: Double) {
        self.x1 = x1; self.y1 = y1; self.x2 = x2; self.y2 = y2
    }

    public static let ease = KeloCubicBezier(0.25, 0.1, 0.25, 1)
    public static let easeIn = KeloCubicBezier(0.42, 0, 1, 1)
    public static let easeOut = KeloCubicBezier(0, 0, 0.58, 1)
    public static let easeInOut = KeloCubicBezier(0.42, 0, 0.58, 1)

    /// One coordinate of the curve at parameter `t` (the end points are 0 and 1).
    private static func at(_ p1: Double, _ p2: Double, _ t: Double) -> Double {
        let u = 1 - t
        return 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t
    }

    private static func slope(_ p1: Double, _ p2: Double, _ t: Double) -> Double {
        let u = 1 - t
        return 3 * u * u * p1 + 6 * u * t * (p2 - p1) + 3 * t * t * (1 - p2)
    }

    /// The eased value at progress `x` (0...1): the curve's y where its x is `x`. Outside 0...1 it returns `x` unchanged, as
    /// Compose does.
    public func transform(_ x: Double) -> Double {
        guard x > 0, x < 1 else { return x }
        // Newton's method from t = x, falling back to bisection; x(t) is monotonic for 0 ≤ x1, x2 ≤ 1
        var t = x
        for _ in 0..<8 {
            let err = Self.at(x1, x2, t) - x
            if abs(err) < 1e-9 { return Self.at(y1, y2, t) }
            let d = Self.slope(x1, x2, t)
            if abs(d) < 1e-9 { break }
            t -= err / d
            if t < 0 || t > 1 { break }
        }
        var lo = 0.0, hi = 1.0
        t = x
        for _ in 0..<64 {
            let v = Self.at(x1, x2, t)
            if abs(v - x) < 1e-9 { break }
            if v < x { lo = t } else { hi = t }
            t = (lo + hi) / 2
        }
        return Self.at(y1, y2, t)
    }
}

extension KeloEasing {
    /// The eased value at progress `x` (0...1) of one keyframe interval.
    public func transform(_ x: Double) -> Double {
        switch self {
        case .linear: return x
        case .ease: return KeloCubicBezier.ease.transform(x)
        case .easeIn: return KeloCubicBezier.easeIn.transform(x)
        case .easeOut: return KeloCubicBezier.easeOut.transform(x)
        case .easeInOut: return KeloCubicBezier.easeInOut.transform(x)
        case let .cubicBezier(x1, y1, x2, y2): return KeloCubicBezier(x1, y1, x2, y2).transform(x)
        }
    }
}
