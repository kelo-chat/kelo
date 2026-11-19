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

    /// The pan kept inside the zoomed PICTURE (not the view): along an axis the picture, zoomed, is wider than the view it may be
    /// moved by half the difference each way, otherwise it stays centred (a letterbox bar is never panned into view).
    public static func clampPan(panX: Double, panY: Double, zoom: Double, viewW: Double, viewH: Double, frameW: Int, frameH: Int) -> (Double, Double) {
        guard let box = fit(viewW: viewW, viewH: viewH, frameW: frameW, frameH: frameH) else { return (0, 0) }
        let z = max(zoom, minZoom)
        let maxX = max(box.width * z - viewW, 0) / 2
        let maxY = max(Double(frameH) * box.scale * z - viewH, 0) / 2
        return (min(max(panX, -maxX), maxX) + 0, min(max(panY, -maxY), maxY) + 0)
    }

    public static func clampZoom(_ zoom: Double) -> Double { min(max(zoom, minZoom), maxZoom) }

    /// The inverse of `toFrame`: where a frame pixel is drawn in the view (zoom and pan included), so the mark of a touch sits on the
    /// point that was sent. nil while a size is unknown.
    public static func frameToView(_ p: FramePoint, viewW: Double, viewH: Double, frameW: Int, frameH: Int,
                                   zoom: Double = 1, panX: Double = 0, panY: Double = 0) -> (x: Double, y: Double)? {
        guard let box = fit(viewW: viewW, viewH: viewH, frameW: frameW, frameH: frameH) else { return nil }
        let z = max(zoom, 0.01)
        let cx = viewW / 2, cy = viewH / 2
        let ux = box.left + (Double(p.x) + 0.5) * box.scale
        let uy = box.top + (Double(p.y) + 0.5) * box.scale
        return (cx + (ux - cx) * z + panX, cy + (uy - cy) * z + panY)
    }

    /// Maps a point in CSS pixels (as sent in pointer messages) to frame pixels.
    public static func cssToFrame(cssX: Double, cssY: Double, frameW: Int, frameH: Int, cssW: Int, cssH: Int) -> FramePoint? {
        guard frameW > 0, frameH > 0, cssW > 0, cssH > 0 else { return nil }
        let scaleX = Double(frameW) / Double(cssW)
        let scaleY = Double(frameH) / Double(cssH)
        let fx = Int((cssX * scaleX + 0.5).rounded(.down))
        let fy = Int((cssY * scaleY + 0.5).rounded(.down))
        return FramePoint(x: min(max(fx, 0), frameW - 1), y: min(max(fy, 0), frameH - 1))
    }

    /// Maps a pointer position in CSS pixels to view coordinates inside the canvas,
    /// accounting for letterboxing (FitBox), zoom, and pan offset.
    public static func pointerToView(
        cssX: Double,
        cssY: Double,
        viewW: Double,
        viewH: Double,
        frameW: Int,
        frameH: Int,
        cssW: Int,
        cssH: Int,
        zoom: Double = 1,
        panX: Double = 0,
        panY: Double = 0
    ) -> (x: Double, y: Double)? {
        guard let box = fit(viewW: viewW, viewH: viewH, frameW: frameW, frameH: frameH) else { return nil }
        let fx: Double
        let fy: Double
        if cssW > 0 && cssH > 0 {
            let sx = Double(frameW) / Double(cssW)
            let sy = Double(frameH) / Double(cssH)
            fx = cssX * sx
            fy = cssY * sy
        } else {
            fx = cssX
            fy = cssY
        }
        let unzoomedX = box.left + fx * box.scale
        let unzoomedY = box.top + fy * box.scale
        let z = max(zoom, 0.01)
        let cx = viewW / 2
        let cy = viewH / 2
        let vx = cx + (unzoomedX - cx) * z + panX
        let vy = cy + (unzoomedY - cy) * z + panY
        return (vx, vy)
    }
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
    public var cssW: Int = 0, cssH: Int = 0
    /// A secret field (password, card, one-time code) shows in the picture; nil = the server did not say (an older one).
    public var secure: Bool? = nil
    /// The live protocol the server speaks (2 = knows `view`, `frame_seq`, `editing`); 0 = an older server that says nothing.
    public var liveV: Int = 0
    /// A text field has focus on the page (the phone's screen); false from an older server.
    public var editing: Bool = false
    /// live_v 3: what keyboard the focused field fits (secret | number | tel | email | url | search | multiline | text), and an opaque id that
    /// changes when the focus moves to another field.
    public var kind = "text"
    public var field = ""

    init(_ o: Lenient) {
        seq = o.int64("seq"); w = o.int("w"); h = o.int("h"); url = o.str("url"); title = o.str("title")
        cssW = o.int("css_w"); cssH = o.int("css_h")
        secure = o.optBool("secure")
        liveV = o.int("live_v"); editing = o.bool("editing")
        kind = o.str("kind").isEmpty ? "text" : o.str("kind"); field = o.str("field")
    }
}

/// The server's answer to one input: where it landed, whether the page was too busy, which frame it was mapped with.
public struct InputReply: Equatable, Sendable {
    public var ok = true, busy = false
    public var x: Double? = nil, y: Double? = nil
    /// "frame" (the frame the owner saw) or "latest" (that frame was unknown); nil from an older server.
    public var mappedWith: String? = nil
    /// Dropped: the tap was aimed at a picture from before the agent's stopped step's last move (tap again on the new picture).
    public var stale = false
    /// Which input it answers ("tap", "edit"...); "" when the server did not say.
    public var input = ""

    public init(ok: Bool = true, busy: Bool = false, x: Double? = nil, y: Double? = nil, mappedWith: String? = nil, stale: Bool = false, input: String = "") {
        self.ok = ok; self.busy = busy; self.x = x; self.y = y; self.mappedWith = mappedWith; self.stale = stale; self.input = input
    }
    init(_ o: Lenient) {
        ok = o.has("ok") ? o.bool("ok") : true; busy = o.bool("busy")
        x = o.has("x") ? o.double("x") : nil; y = o.has("y") ? o.double("y") : nil
        mappedWith = o.optStr("mapped_with")
        stale = o.bool("stale"); input = o.str("input")
    }
    public init(json: JSON) { self.init(Lenient(json)) }
}

/// An agent pointer position on the page in CSS pixels.
public struct LivePointer: Equatable, Sendable {
    public var x: Double = 0
    public var y: Double = 0
    public var down: Bool = false

    public init(x: Double = 0, y: Double = 0, down: Bool = false) {
        self.x = x; self.y = y; self.down = down
    }

    init(_ o: Lenient) {
        x = o.double("x")
        y = o.double("y")
        down = o.bool("down")
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
    public var pointer: LivePointer? = nil
    public var pageBusy = false
    public var stale = false
    /// "button": the owner's own 직접 조작 (only hand back ends it); "handover": the app took it because Kelo asked; "": nobody / an older server.
    public var hold = ""
    /// The holder has no live connection, so another device may take the tab (the take moves it).
    public var holderAway = false
    /// "work" on a tab of Kelo's work browser; "main" or "" (an older server) for the owner's own browser.
    public var browser = ""

    public init() {}
    init(_ o: Lenient) {
        controller = o.str("controller", "agent"); youControl = o.bool("you_control")
        label = o.str("label"); agent = LiveAgent(o.obj("agent")); viewers = o.int("viewers")
        pointer = o.has("pointer") ? LivePointer(o.obj("pointer")) : nil
        pageBusy = o.bool("page_busy")
        stale = o.bool("stale")
        hold = o.str("hold"); holderAway = o.bool("holder_away")
        browser = o.str("browser")
    }

    /// Another phone (or the owner's app) holds the tab.
    public var someoneElse: Bool { controller == "user" && !youControl }

    /// The owner holds the tab for a hand-over Kelo asked for: leaving the page gives it back (owner decision 2026-11-18).
    public var handoverHold: Bool { youControl && hold == "handover" }

    /// The tab is in Kelo's work browser, apart from the owner's own tabs.
    public var inWorkBrowser: Bool { browser == "work" }
}

/// `{"type":"meta","url","title","editing","kind","field","live_v"}`: the tab's address and title and whether a text field has focus (and which
/// kind, which field), sent while this viewer's frames are off (the VNC screen shows, so the frame headers that normally carry them do not
/// come). A frame arriving again takes over. A server from before `editing` leaves it out: false; before live typing, no kind / field / live_v.
public struct LiveMeta: Equatable, Sendable {
    public var url: String, title: String
    public var editing: Bool
    public var kind: String, field: String
    public var liveV: Int
    public init(url: String = "", title: String = "", editing: Bool = false, kind: String = "text", field: String = "", liveV: Int = 0) {
        self.url = url; self.title = title; self.editing = editing; self.kind = kind; self.field = field; self.liveV = liveV
    }
}

/// The focused field as live typing needs it: `kind` picks the keyboard, `field` tells a move to another field.
public struct Typing: Equatable, Sendable {
    public var kind: String, field: String
    public init(kind: String, field: String) { self.kind = kind; self.field = field }
}

/// What the live page's header shows of the page: a meta line while frames are off, else the newest frame's header.
public enum LiveHeader {
    public static func title(meta: LiveMeta?, frame: String?) -> String? {
        let t = meta?.title ?? frame
        return t?.isEmpty == false ? t : nil
    }
    public static func url(meta: LiveMeta?, frame: String?) -> String? {
        let u = meta?.url ?? frame
        return u?.isEmpty == false ? u : nil
    }

    /// A text field has focus on the page: the meta line while frames are off (the last frame header is old then and would never say
    /// it), else the frame's own flag. This is what raises the keyboard into the type line.
    public static func editing(meta: LiveMeta?, frame: Bool?) -> Bool { meta?.editing ?? (frame == true) }

    /// The focused field's kind and id (the same source as `editing`), nil when no field has focus.
    public static func typing(meta: LiveMeta?, frame: FrameHeader?) -> Typing? {
        if let meta { return meta.editing ? Typing(kind: meta.kind, field: meta.field) : nil }
        if let frame, frame.editing { return Typing(kind: frame.kind, field: frame.field) }
        return nil
    }

    /// The server takes live typing (the `edit` input): live_v 3 or more, in the meta line or the newest frame header. Else the visible type line stays.
    public static func liveTyping(meta: LiveMeta?, frameLiveV: Int?) -> Bool { (meta?.liveV ?? 0) >= 3 || (frameLiveV ?? 0) >= 3 }
}

/// Text going between the phone's clipboard and the page's: only when the owner taps, never on its own, never kept past use.
public enum ClipboardRules {
    /// The most text taken from the browser's clipboard for the owner to copy.
    public static let maxOffer = 10_000

    /// The phone's clipboard text to type into the page: nil when there is none (the paste key stays off), cut at what one entry takes.
    public static func pasteText(_ clip: String?) -> String? {
        guard let clip, !clip.isEmpty else { return nil }
        return String(clip.prefix(LiveMessages.maxText))
    }

    /// The browser's clipboard text offered to the owner: nil for nothing worth offering, cut at `maxOffer`.
    public static func offer(_ text: String?) -> String? {
        guard let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return String(text.prefix(maxOffer))
    }
}

/// One text message from the server, sorted by type.
public enum LiveIn: Equatable, Sendable {
    case frame(FrameHeader)
    case status(LiveStatus)
    case pointer(LivePointer)
    case ended(reason: String)
    case error(code: String, forType: String)
    case pong
    case meta(LiveMeta)
    /// The answer to an input sent with `ack` (the WebSocket's input_ok).
    case inputReply(InputReply)
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
        case "pointer": return .pointer(LivePointer(o))
        case "ended": return .ended(reason: o.str("reason"))
        case "error": return .error(code: o.str("code"), forType: o.str("for"))
        case "pong": return .pong
        case "meta":
            let kind = o.str("kind")
            return .meta(LiveMeta(url: o.str("url"), title: o.str("title"), editing: o.bool("editing"), kind: kind.isEmpty ? "text" : kind,
                                  field: o.str("field"), liveV: o.int("live_v")))
        case "input_ok": return .inputReply(InputReply(o))
        default: return .other
        }
    }

    public static func ping() -> String { #"{"type":"ping"}"# }

    /// What the owner sees of the live area, in physical pixels and the screen density: sent when controlling starts, on rotation and when
    /// the soft keyboard opens or closes. Only for a server that speaks live protocol 2.
    public static func view(width: Int, height: Int, density: Double) -> JSON {
        ["type": "view", "width": n(width), "height": n(height), "density": .number(density)]
    }

    /// The protocol version from which `view`, `frame_seq` and `editing` exist.
    public static let protocolV = 2
    public static func speaksV2(_ liveV: Int) -> Bool { liveV >= protocolV }

    /// `frame_seq` (the frame the owner was looking at) on an input; nothing for an older server or when no frame has been seen.
    public static func stamp(_ event: JSON, frameSeq: Int64?, liveV: Int) -> JSON {
        guard speaksV2(liveV), let frameSeq, frameSeq > 0, case .object(var o) = event, o["frame_seq"] == nil else { return event }
        o["frame_seq"] = .number(Double(frameSeq))
        return .object(o)
    }

    /// Ask the server to answer an input (input_ok) so the touch mark can tell landed from not-reached.
    public static func acked(_ event: JSON) -> JSON {
        guard case .object(var o) = event else { return event }
        o["ack"] = .bool(true)
        return .object(o)
    }
    public static func tap(_ p: FramePoint, frameW: Int, frameH: Int) -> JSON { point("tap", p, frameW, frameH) }
    public static func longPress(_ p: FramePoint, frameW: Int, frameH: Int) -> JSON { point("long_press", p, frameW, frameH) }

    public static func scroll(_ d: FramePoint, at: FramePoint?, frameW: Int, frameH: Int) -> JSON {
        var o: [String: JSON] = ["type": "scroll", "dx": n(d.x), "dy": n(d.y), "frame_w": n(frameW), "frame_h": n(frameH)]
        if let at { o["x"] = n(at.x); o["y"] = n(at.y) }
        return .object(o)
    }

    public static func text(_ text: String) -> JSON { ["type": "text", "text": .string(text)] }
    public static func key(_ name: String) -> JSON { ["type": "key", "key": .string(name)] }
    /// The owner's keyboard changes as steps for the page's focused field (live_v 3); see `RemoteTyping`.
    public static func edit(_ steps: [EditStep]) -> JSON { RemoteTyping.json(steps) }
    /// The Android device's keys: back | home | enter | recents.
    public static func deviceKey(_ name: String) -> JSON { key(name) }
    /// A finger drag on the device's screen, from `from` to `to` in frame pixels, taking `ms` milliseconds.
    public static func swipe(from: FramePoint, to: FramePoint, frameW: Int, frameH: Int, ms: Int) -> JSON {
        ["type": "swipe", "x1": n(from.x), "y1": n(from.y), "x2": n(to.x), "y2": n(to.y), "ms": n(ms), "frame_w": n(frameW), "frame_h": n(frameH)]
    }
    /// back | forward | reload
    public static func nav(_ kind: String) -> JSON { ["type": .string(kind)] }
    /// The body of POST .../control.
    /// Taking the tab: `by` "handover" when the app took it because Kelo asked (a hand-over hold); the owner's own press sends none.
    public static func control(take: Bool, handover: Bool = false) -> JSON {
        take && handover ? ["take": .bool(take), "by": .string("handover")] : ["take": .bool(take)]
    }

    /// Whether the server sends JPEG frames over this live socket (`frames`): off while the VNC screen shows, on again when the
    /// picture is the fallback. Input, control and status stay on the socket either way.
    public static func frames(on: Bool) -> JSON { ["type": "frames", "on": .bool(on)] }

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

/// What a live view shows: a browser tab, or the Android device (the key "android": a portrait screen, Back / Home / Recents).
public enum LiveKinds {
    public static let deviceKey = "android"
    public static func isDevice(_ tabId: String) -> Bool { tabId == deviceKey }
    /// The device's navigation keys, left to right (the server's names).
    public static let phoneKeys = ["back", "home", "recents"]
    /// A swipe lasts as long as the finger moved, between these.
    public static func swipeMs(_ dragMs: Int) -> Int { min(800, max(120, dragMs)) }
}

/// When the live view takes its tab over by itself (it was opened to solve a human check).
public enum LiveRules {
    /// Once only, when asked to, after the first picture came, while nobody holds the tab and no take-over is on its way.
    /// Leaving the live page gives the tab back only for a hand-over hold; the owner's own button hold stays until they hand it back.
    /// An older server says no hold, so nothing is given back on leaving (its own 30 s grace still applies).
    public static func giveBackOnLeave(_ s: LiveStatus) -> Bool { s.handoverHold }

    /// Leaving the page while the owner holds the tab by their own press: the page says it stays theirs until they hand it back.
    public static func leaveNotice(_ s: LiveStatus) -> Bool { s.youControl && !s.handoverHold }

    /// The chips show only when the window has more than one tab.
    public static func showTabs(_ tabs: [LiveTab]) -> Bool { tabs.count > 1 }

    /// Tapping a chip (and closing one) works only while this device holds the tab; otherwise the chips only show where the agent is.
    public static func tabsTappable(_ s: LiveStatus) -> Bool { s.youControl }

    /// Only a tab that is not the active one has a close mark.
    public static func tabClosable(_ t: LiveTab, tappable: Bool) -> Bool { tappable && !t.active }

    /// The body of POST /api/browser/sessions/{key}/tabs/{tab}.
    public static func tabAction(close: Bool) -> JSON { ["action": .string(close ? "close" : "activate")] }

    /// The owner holds a tab of the work browser: the line under the paused banner says where the sign-in goes.
    public static func showWorkNote(_ s: LiveStatus) -> Bool { s.youControl && s.inWorkBrowser }

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
