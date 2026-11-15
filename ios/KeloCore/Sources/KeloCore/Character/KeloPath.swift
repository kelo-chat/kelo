import Foundation

/// SVG path data turned into absolute moves, lines and Bézier curves (elliptical arcs become cubic curves), so a renderer only
/// needs `move`, `addLine`, `addQuadCurve`, `addCurve` and `closeSubpath`.
public enum KeloPathElement: Hashable, Sendable {
    case move(KeloPoint)
    case line(KeloPoint)
    case quad(control: KeloPoint, to: KeloPoint)
    case cubic(control1: KeloPoint, control2: KeloPoint, to: KeloPoint)
    case close
}

public enum KeloPath {
    public struct ParseError: Error, Hashable, Sendable {
        public let message: String
        public let offset: Int
    }

    /// Parses SVG path data (M L H V C S Q T A Z, absolute and relative, implicit repeats).
    public static func parse(_ d: String) throws -> [KeloPathElement] {
        var r = Reader(chars: Array(d.utf8))
        var out: [KeloPathElement] = []
        var cur = KeloPoint(x: 0, y: 0), start = cur
        var lastCubic: KeloPoint?, lastQuad: KeloPoint?
        var cmd: UInt8 = 0
        r.skipSpace()
        while !r.atEnd {
            if let c = r.peekCommand() {
                cmd = c
                r.i += 1
            } else if cmd == 0 {
                throw ParseError(message: "path data must start with a command", offset: r.i)
            } else if cmd == UInt8(ascii: "M") { cmd = UInt8(ascii: "L") } else if cmd == UInt8(ascii: "m") { cmd = UInt8(ascii: "l") }
            let rel = cmd >= UInt8(ascii: "a")
            let upper = rel ? cmd - 32 : cmd
            if out.isEmpty && upper != UInt8(ascii: "M") {
                throw ParseError(message: "path data must start with a move", offset: r.i - 1)
            }
            func pt() throws -> KeloPoint {
                let x = try r.number(), y = try r.number()
                return rel ? KeloPoint(x: cur.x + x, y: cur.y + y) : KeloPoint(x: x, y: y)
            }
            var nextCubic: KeloPoint?, nextQuad: KeloPoint?
            switch upper {
            case UInt8(ascii: "M"):
                cur = try pt(); start = cur
                out.append(.move(cur))
            case UInt8(ascii: "L"):
                cur = try pt()
                out.append(.line(cur))
            case UInt8(ascii: "H"):
                let x = try r.number()
                cur = KeloPoint(x: rel ? cur.x + x : x, y: cur.y)
                out.append(.line(cur))
            case UInt8(ascii: "V"):
                let y = try r.number()
                cur = KeloPoint(x: cur.x, y: rel ? cur.y + y : y)
                out.append(.line(cur))
            case UInt8(ascii: "C"), UInt8(ascii: "S"):
                let c1: KeloPoint
                if upper == UInt8(ascii: "C") {
                    c1 = try pt()
                } else {
                    c1 = lastCubic.map { KeloPoint(x: 2 * cur.x - $0.x, y: 2 * cur.y - $0.y) } ?? cur
                }
                let c2 = try pt(), to = try pt()
                out.append(.cubic(control1: c1, control2: c2, to: to))
                nextCubic = c2
                cur = to
            case UInt8(ascii: "Q"), UInt8(ascii: "T"):
                let c: KeloPoint
                if upper == UInt8(ascii: "Q") {
                    c = try pt()
                } else {
                    c = lastQuad.map { KeloPoint(x: 2 * cur.x - $0.x, y: 2 * cur.y - $0.y) } ?? cur
                }
                let to = try pt()
                out.append(.quad(control: c, to: to))
                nextQuad = c
                cur = to
            case UInt8(ascii: "A"):
                let rx = try r.number(), ry = try r.number(), rot = try r.number()
                let large = try r.flag(), sweep = try r.flag()
                let to = try pt()
                out += arc(from: cur, to: to, rx: rx, ry: ry, rotation: rot, large: large, sweep: sweep)
                cur = to
            case UInt8(ascii: "Z"):
                out.append(.close)
                cur = start
                cmd = 0   // a number straight after Z is an error
            default:
                throw ParseError(message: "unknown command", offset: r.i - 1)
            }
            lastCubic = nextCubic
            lastQuad = nextQuad
            r.skipSpace()
        }
        return out
    }

    /// The box around every point and control point (the curves lie inside it); nil for no points.
    public static func controlBounds(_ elements: [KeloPathElement]) -> (min: KeloPoint, max: KeloPoint)? {
        var pts: [KeloPoint] = []
        for e in elements {
            switch e {
            case let .move(p), let .line(p): pts.append(p)
            case let .quad(c, p): pts += [c, p]
            case let .cubic(c1, c2, p): pts += [c1, c2, p]
            case .close: break
            }
        }
        guard let f = pts.first else { return nil }
        var lo = f, hi = f
        for p in pts {
            lo = KeloPoint(x: min(lo.x, p.x), y: min(lo.y, p.y))
            hi = KeloPoint(x: max(hi.x, p.x), y: max(hi.y, p.y))
        }
        return (lo, hi)
    }

    /// An SVG elliptical arc as cubic curves of at most 90° each (SVG 1.1 implementation notes, F.6).
    static func arc(from p0: KeloPoint, to p1: KeloPoint, rx rx0: Double, ry ry0: Double, rotation: Double,
                    large: Bool, sweep: Bool) -> [KeloPathElement] {
        if p0 == p1 { return [] }
        var rx = abs(rx0), ry = abs(ry0)
        if rx == 0 || ry == 0 { return [.line(p1)] }
        let phi = rotation * Double.pi / 180
        let cphi = cos(phi), sphi = sin(phi)
        let dx = (p0.x - p1.x) / 2, dy = (p0.y - p1.y) / 2
        let x1 = cphi * dx + sphi * dy, y1 = -sphi * dx + cphi * dy
        let lambda = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
        if lambda > 1 { rx *= lambda.squareRoot(); ry *= lambda.squareRoot() }
        let num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
        let den = rx * rx * y1 * y1 + ry * ry * x1 * x1
        var k = den == 0 ? 0 : max(num / den, 0).squareRoot()
        if large == sweep { k = -k }
        let cx1 = k * rx * y1 / ry, cy1 = -k * ry * x1 / rx
        let cx = cphi * cx1 - sphi * cy1 + (p0.x + p1.x) / 2
        let cy = sphi * cx1 + cphi * cy1 + (p0.y + p1.y) / 2
        func angle(_ ux: Double, _ uy: Double, _ vx: Double, _ vy: Double) -> Double {
            atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        }
        let theta1 = angle(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
        var dtheta = angle((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
        if !sweep && dtheta > 0 { dtheta -= 2 * Double.pi }
        if sweep && dtheta < 0 { dtheta += 2 * Double.pi }
        let n = max(1, Int((abs(dtheta) / (Double.pi / 2) - 1e-9).rounded(.up)))
        let step = dtheta / Double(n)
        let t = 4.0 / 3.0 * tan(step / 4)
        func onEllipse(_ a: Double) -> (KeloPoint, KeloPoint) {   // the point and its derivative direction
            let ex = rx * cos(a), ey = ry * sin(a)
            let tx = -rx * sin(a), ty = ry * cos(a)
            return (KeloPoint(x: cx + cphi * ex - sphi * ey, y: cy + sphi * ex + cphi * ey),
                    KeloPoint(x: cphi * tx - sphi * ty, y: sphi * tx + cphi * ty))
        }
        var out: [KeloPathElement] = []
        var a = theta1
        var (pa, da) = onEllipse(a)
        for s in 0..<n {
            let b = a + step
            var (pb, db) = onEllipse(b)
            if s == n - 1 { pb = p1 }
            out.append(.cubic(control1: KeloPoint(x: pa.x + t * da.x, y: pa.y + t * da.y),
                              control2: KeloPoint(x: pb.x - t * db.x, y: pb.y - t * db.y), to: pb))
            (a, pa, da) = (b, pb, db)
        }
        return out
    }

    private struct Reader {
        let chars: [UInt8]
        var i = 0
        var atEnd: Bool { i >= chars.count }

        mutating func skipSpace() {
            while i < chars.count, chars[i] == 0x20 || chars[i] == 0x09 || chars[i] == 0x0A || chars[i] == 0x0D || chars[i] == 0x2C {
                i += 1
            }
        }

        func peekCommand() -> UInt8? {
            let c = chars[i]
            return "MmLlHhVvCcSsQqTtAaZz".utf8.contains(c) ? c : nil
        }

        mutating func number() throws -> Double {
            skipSpace()
            let s = i
            if i < chars.count, chars[i] == UInt8(ascii: "-") || chars[i] == UInt8(ascii: "+") { i += 1 }
            var digits = false, dot = false
            while i < chars.count {
                let c = chars[i]
                if c >= 0x30 && c <= 0x39 { digits = true } else if c == UInt8(ascii: "."), !dot { dot = true } else { break }
                i += 1
            }
            if digits, i < chars.count, chars[i] == UInt8(ascii: "e") || chars[i] == UInt8(ascii: "E") {
                var j = i + 1
                if j < chars.count, chars[j] == UInt8(ascii: "-") || chars[j] == UInt8(ascii: "+") { j += 1 }
                if j < chars.count, chars[j] >= 0x30 && chars[j] <= 0x39 {
                    i = j
                    while i < chars.count, chars[i] >= 0x30 && chars[i] <= 0x39 { i += 1 }
                }
            }
            guard digits, let v = Double(String(decoding: chars[s..<i], as: UTF8.self)) else {
                throw ParseError(message: "number expected", offset: s)
            }
            return v
        }

        /// An arc flag: a single 0 or 1 (may be written without a separator, e.g. "a1 1 0 01 2 2").
        mutating func flag() throws -> Bool {
            skipSpace()
            guard i < chars.count, chars[i] == UInt8(ascii: "0") || chars[i] == UInt8(ascii: "1") else {
                throw ParseError(message: "arc flag expected", offset: i)
            }
            i += 1
            return chars[i - 1] == UInt8(ascii: "1")
        }
    }
}
