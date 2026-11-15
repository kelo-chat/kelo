import Foundation

/*
 * The live view of a browser tab (server.py "LIVE VIEW API"), the parts that need no UI (Android agent/LiveRules.kt): where the
 * picture sits on screen and how a touch becomes a point on the page, when to reconnect and when to fall back to fetching one picture
 * at a time, and the messages going both ways. The connection is LiveClient.swift; the SwiftUI page comes with Xcode.
 */

/// Where a frame of frameW x frameH is drawn inside a view (aspect fit: the whole picture, centred, letterboxed).
public struct FitBox: Equatable, Sendable { public let scale, left, top, width: Double }

/// A point on the frame, in its own pixels (what tap / long_press send).
public struct FramePoint: Equatable, Sendable {
    public let x: Int, y: Int
    public init(x: Int, y: Int) { self.x = x; self.y = y }
}

public enum LiveGeometry {
    public static let minZoom = 1.0
    public static let maxZoom = 5.0

    /// The picture's box in the view; nil while either size is unknown.
    public static func fit(viewW: Double, viewH: Double, frameW: Int, frameH: Int) -> FitBox? {
        guard viewW > 0, viewH > 0, frameW > 0, frameH > 0 else { return nil }
        let s = min(viewW / Double(frameW), viewH / Double(frameH))
        let w = Double(frameW) * s, h = Double(frameH) * s
        return FitBox(scale: s, left: (viewW - w) / 2, top: (viewH - h) / 2, width: w)
    }

    /// A touch at (x, y) in view points, as a frame pixel. The view is zoomed by `zoom` around its centre and then moved by
    /// (panX, panY) (what scaleEffect + offset do); nil when the touch is outside the picture, so a tap there sends nothing.
    public static func toFrame(x: Double, y: Double, viewW: Double, viewH: Double, frameW: Int, frameH: Int,
                               zoom: Double = 1, panX: Double = 0, panY: Double = 0) -> FramePoint? {
        guard let box = fit(viewW: viewW, viewH: viewH, frameW: frameW, frameH: frameH) else { return nil }
        let z = max(zoom, 0.01)
        let cx = viewW / 2, cy = viewH / 2
        let ux = cx + (x - panX - cx) / z
        let uy = cy + (y - panY - cy) / z
        let fx = (ux - box.left) / box.scale
        let fy = (uy - box.top) / box.scale
        if fx < 0 || fy < 0 || fx > Double(frameW) || fy > Double(frameH) { return nil }
        return FramePoint(x: min(max(Int(fx), 0), frameW - 1), y: min(max(Int(fy), 0), frameH - 1))
    }

    /// A finger drag of (dx, dy) view points as a scroll of the page, in frame pixels. Natural scrolling: the signs flip.
    public static func scrollDelta(dx: Double, dy: Double, viewW: Double, viewH: Double, frameW: Int, frameH: Int, zoom: Double = 1) -> FramePoint {
        guard let box = fit(viewW: viewW, viewH: viewH, frameW: frameW, frameH: frameH) else { return FramePoint(x: 0, y: 0) }
        let k = box.scale * max(zoom, 0.01)
        // Kotlin roundToInt: a half rounds up (-2.5 -> -2), not away from zero
        return FramePoint(x: Int((-dx / k + 0.5).rounded(.down)), y: Int((-dy / k + 0.5).rounded(.down)))
    }

    /// The pan kept inside the zoomed picture: at 1x no pan, at 2x up to half the view each way.
    public static func clampPan(panX: Double, panY: Double, zoom: Double, viewW: Double, viewH: Double) -> (Double, Double) {
        let maxX = max(zoom - 1, 0) * viewW / 2
        let maxY = max(zoom - 1, 0) * viewH / 2
        return (min(max(panX, -maxX), maxX) + 0, min(max(panY, -maxY), maxY) + 0)
    }

    public static func clampZoom(_ zoom: Double) -> Double { min(max(zoom, minZoom), maxZoom) }
}

/// How sharp and how often: 720 px / 55 / 10 fps on Wi-Fi, less on an expensive network (cellular, Low Data Mode).
public struct LiveQuality: Equatable, Sendable {
    public let maxWidth: Int, quality: Int, fps: Int
    public static let wifi = LiveQuality(maxWidth: 720, quality: 55, fps: 10)
    public static let metered = LiveQuality(maxWidth: 540, quality: 45, fps: 5)
    public static func pick(metered: Bool) -> LiveQuality { metered ? .metered : .wifi }

    public func query() -> String { "max_width=\(maxWidth)&quality=\(quality)&fps=\(fps)" }
}

/// {"type":"frame",...}: the JPEG that follows it, w x h pixels.
public struct FrameHeader: Equatable, Sendable {
    public var seq: Int64, w: Int, h: Int, url: String, title: String

    init(_ o: Lenient) {
        seq = o.int64("seq"); w = o.int("w"); h = o.int("h"); url = o.str("url"); title = o.str("title")
    }
}

public struct LiveAgent: Equatable, Sendable {
    public var busy = false, status: String? = nil, waitingApproval = false
    public init() {}
    init(_ o: Lenient) {
        busy = o.bool("busy"); status = o.optStr("status")
        waitingApproval = o.bool("waiting_approval")
    }
}

/// {"type":"status"}: who controls the tab, what the agent is doing in it, how many are watching.
public struct LiveStatus: Equatable, Sendable {
    public var controller = "agent"
    public var youControl = false
    public var label = ""
    public var agent = LiveAgent()
    public var viewers = 0

    public init() {}
    init(_ o: Lenient) {
        controller = o.str("controller", "agent"); youControl = o.bool("you_control")
        label = o.str("label"); agent = LiveAgent(o.obj("agent")); viewers = o.int("viewers")
    }

    /// Another phone (or the owner's app) holds the tab.
    public var someoneElse: Bool { controller == "user" && !youControl }
}

/// One text message from the server, sorted by type.
public enum LiveIn: Equatable, Sendable {
    case frame(FrameHeader)
    case status(LiveStatus)
    case ended(reason: String)
    case error(code: String, forType: String)
    case pong
    case other
}

/// The messages of the live view, both ways. Typed text is never part of anything but the one message that carries it.
public enum LiveMessages {
    public static let maxText = 2000

    public static func parse(_ text: String) -> LiveIn {
        guard let v = try? JSON.parse(text), v.objectValue != nil else { return .other }
        let o = Lenient(v)
        switch o.str("type") {
        case "frame": return .frame(FrameHeader(o))
        case "status": return .status(LiveStatus(o))
        case "ended": return .ended(reason: o.str("reason"))
        case "error": return .error(code: o.str("code"), forType: o.str("for"))
        case "pong": return .pong
        default: return .other
        }
    }

    public static func ping() -> String { #"{"type":"ping"}"# }
    public static func tap(_ p: FramePoint, frameW: Int, frameH: Int) -> JSON { point("tap", p, frameW, frameH) }
    public static func longPress(_ p: FramePoint, frameW: Int, frameH: Int) -> JSON { point("long_press", p, frameW, frameH) }

    public static func scroll(_ d: FramePoint, at: FramePoint?, frameW: Int, frameH: Int) -> JSON {
        var o: [String: JSON] = ["type": "scroll", "dx": n(d.x), "dy": n(d.y), "frame_w": n(frameW), "frame_h": n(frameH)]
        if let at { o["x"] = n(at.x); o["y"] = n(at.y) }
        return .object(o)
    }

    public static func text(_ text: String) -> JSON { ["type": "text", "text": .string(text)] }
    public static func key(_ name: String) -> JSON { ["type": "key", "key": .string(name)] }
    /// back | forward | reload
    public static func nav(_ kind: String) -> JSON { ["type": .string(kind)] }
    /// The body of POST .../control.
    public static func control(take: Bool) -> JSON { ["take": .bool(take)] }

    static func n(_ i: Int) -> JSON { .number(Double(i)) }
    static func point(_ type: String, _ p: FramePoint, _ w: Int, _ h: Int) -> JSON {
        ["type": .string(type), "x": n(p.x), "y": n(p.y), "frame_w": n(w), "frame_h": n(h)]
    }

    /// Text longer than the server takes in one message is sent in pieces of at most `max` characters.
    public static func chunks(_ text: String, max: Int = maxText) -> [String] {
        guard !text.isEmpty else { return [] }
        var out: [String] = []
        var rest = Substring(text)
        while !rest.isEmpty { out.append(String(rest.prefix(max))); rest = rest.dropFirst(max) }
        return out
    }
}

/// When the live view takes its tab over by itself (it was opened to solve a human check).
public enum LiveRules {
    /// Once only, when asked to, after the first picture came, while nobody holds the tab and no take-over is on its way.
    public static func autoTake(asked: Bool, done: Bool, hasFrame: Bool, controlling: Bool, someoneElse: Bool, busy: Bool) -> Bool {
        asked && !done && hasFrame && !controlling && !someoneElse && !busy
    }
}

/// Where the live view is.
public enum LiveMode: Sendable { case connecting, live, polling, waiting, ended }

/// Why there is nothing more to show.
public enum LiveEnd: Sendable { case tabClosed, noTab, noBrowser }

/// What the connection should do next.
public enum LiveStep: Equatable, Sendable {
    /// Open the WebSocket (with a new ticket when `freshTicket`) after `delayMs`.
    case openSocket(freshTicket: Bool = true, delayMs: Int64 = 0)
    /// Fetch frames one at a time (GET .../frame?after=&wait=10) after `delayMs`.
    case poll(delayMs: Int64 = 0)
    case end(LiveEnd)
}

/// The reconnect rules, apart from the network so they can be tested:
/// - the WebSocket fails to connect twice in a row (a proxy without WebSocket support) -> fetch frames one at a time from then on;
/// - 4401 (the ticket was used or expired) -> one new ticket and one more try, then frames one at a time;
/// - 4404 or "ended" -> the tab is gone; 4408 (silent for 60 s, e.g. the phone slept) -> reconnect at once;
/// - 4429 (too many failed attempts) -> wait half a minute;
/// - a dropped connection that had worked -> reconnect, waiting a little longer each time (at most 15 s).
public struct LiveMachine: Sendable {
    public static let fallbackAfter = 2
    public static let retryMs: Int64 = 1_000
    public static let tooManyMs: Int64 = 30_000
    public static let pingMs: Int64 = 20_000

    public private(set) var mode: LiveMode = .connecting
    public private(set) var end: LiveEnd? = nil
    /// The WebSocket attempts in a row that never opened.
    public private(set) var connectFailures = 0
    private var ticketRetried = false
    private var drops = 0
    private var everOpened = false

    public init() {}

    /// 1 s, 2 s, 4 s, 8 s, then 15 s.
    public static func backoff(_ n: Int) -> Int64 { n <= 0 ? 0 : min(15_000, Int64(1_000) << Int64(min(n - 1, 4))) }

    public mutating func start() -> LiveStep { mode = .connecting; return .openSocket(freshTicket: true) }

    public mutating func opened() {
        mode = .live; connectFailures = 0; everOpened = true; drops = 0
        ticketRetried = false   // a later 4401 (a ticket that expired while away) gets its one retry again
    }

    /// The upgrade was refused, the server could not be reached, or no ticket could be had.
    public mutating func connectFailed() -> LiveStep {
        if mode == .ended { return .end(end ?? .tabClosed) }
        connectFailures += 1
        if connectFailures >= LiveMachine.fallbackAfter { return poll() }
        mode = .waiting
        return .openSocket(freshTicket: true, delayMs: LiveMachine.retryMs)
    }

    /// The server said {"type":"ended"} before closing.
    public mutating func ended(_ reason: String) -> LiveStep { finish(reason == "no_tab" ? .noTab : .tabClosed) }

    /// The WebSocket closed (or failed after it had opened: code 1006).
    public mutating func closed(_ code: Int) -> LiveStep {
        if mode == .ended { return .end(end ?? .tabClosed) }
        let wasOpen = mode == .live
        switch code {
        case 4401:
            if !ticketRetried { ticketRetried = true; mode = .connecting; return .openSocket(freshTicket: true) }
            return poll()
        case 4404: return finish(.noTab)
        case 4408: mode = .connecting; return .openSocket(freshTicket: true)
        case 4429: mode = .waiting; return .openSocket(freshTicket: true, delayMs: LiveMachine.tooManyMs)
        default:
            if !wasOpen && !everOpened { return connectFailed() }
            drops += 1
            mode = .waiting
            return .openSocket(freshTicket: true, delayMs: LiveMachine.backoff(drops))
        }
    }

    /// One frame request answered: 200 / 204 go on, 404 is the end, anything else waits and tries again.
    public mutating func polled(status: Int, failures: Int) -> LiveStep {
        if mode == .ended { return .end(end ?? .tabClosed) }
        if status == 200 || status == 204 { mode = .polling; return .poll() }
        if status == 404 { return finish(.noTab) }
        mode = .polling
        return .poll(delayMs: LiveMachine.backoff(failures))
    }

    /// The server has no browser at all (404 on the ticket): nothing live to show.
    public mutating func noBrowser() -> LiveStep { finish(.noBrowser) }

    private mutating func poll() -> LiveStep { mode = .polling; return .poll() }
    private mutating func finish(_ reason: LiveEnd) -> LiveStep { mode = .ended; end = reason; return .end(reason) }
}
