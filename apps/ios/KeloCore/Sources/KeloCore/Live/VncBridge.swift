import Foundation

/// The messages between the app and the VNC page (assets vnc/view.js), always strings: JSON, the binary as base64. The same
/// messages as Android's `VncBridge`; on the iPhone the page posts them to `window.webkit.messageHandlers.keloVnc` and the app
/// hands them to `window.keloVncReceive(text)`.
public enum VncBridge {
    public static func encode(_ bytes: Data) -> String { bytes.base64EncodedString() }
    public static func decode(_ text: String) -> Data? { Data(base64Encoded: text) }

    /// Server bytes for the page.
    public static func data(_ bytes: Data) -> String { #"{"t":"d","b":"\#(encode(bytes))"}"# }
    /// Whether the owner holds the tab (input from the page is sent only then).
    public static func hold(_ on: Bool) -> String { #"{"t":"hold","on":\#(on ? "true" : "false")}"# }
    /// The page may create its connection now.
    public static func open() -> String { #"{"t":"open"}"# }
    /// A new socket for a new screen size follows: the page starts a new session and swaps it in once it connects, the old picture stays till then.
    public static func reset() -> String { #"{"t":"reset"}"# }

    public enum FromPage: Equatable, Sendable {
        case bytes(Data)
        case ready
        case unknown
    }

    /// What the page sent: `{"t":"d","b":…}` (RFB bytes), `{"t":"ready"}`; anything else is `.unknown`.
    public static func fromPage(_ text: String) -> FromPage {
        guard let v = try? JSON.parse(text), v.objectValue != nil else { return .unknown }
        switch v["t"]?.stringValue {
        case "d":
            guard let b = v["b"]?.stringValue, let bytes = decode(b) else { return .unknown }
            return .bytes(bytes)
        case "ready": return .ready
        default: return .unknown
        }
    }

    /// The script that hands one message to the page: `window.keloVncReceive("…")` with the message as a JavaScript string.
    public static func script(for message: String) -> String { "window.keloVncReceive(\(literal(message)))" }

    /// A JavaScript string literal for `text` (quotes, backslashes, control characters and the two line separators escaped).
    static func literal(_ text: String) -> String {
        var out = "\""
        for u in text.unicodeScalars {
            switch u {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\u{2028}": out += "\\u2028"
            case "\u{2029}": out += "\\u2029"
            default:
                if u.value < 0x20 { out += String(format: "\\u%04x", u.value) } else { out.unicodeScalars.append(u) }
            }
        }
        return out + "\""
    }
}

/// One web view's side of the bridge, with no web view in it (`evaluate` runs a script in the page): the page says "ready", then the
/// bytes flow. Messages for the page before it is ready (bytes, hold) wait and go in order.
@MainActor
public final class VncPageBridge {
    private let relay: VncRelay
    private let evaluate: (String) -> Void
    private var token: Int?
    private var ready = false
    private var holding = false
    private var pending: [String] = []
    private var released = false

    public init(relay: VncRelay, evaluate: @escaping (String) -> Void) {
        self.relay = relay; self.evaluate = evaluate
    }

    /// Attaches to the relay: from now on its bytes reach the page.
    public func start() {
        token = relay.attach({ [weak self] bytes in
            // the relay hands bytes over on the main actor
            MainActor.assumeIsolated { self?.deliver(VncBridge.data(bytes)) }
        }, reset: { [weak self] in
            MainActor.assumeIsolated { self?.deliver(VncBridge.reset()) }
        })
    }

    /// A string the page posted.
    public func pageMessage(_ text: String) {
        switch VncBridge.fromPage(text) {
        case .bytes(let bytes): relay.send(bytes)
        case .ready:
            guard !released else { return }
            ready = true
            post(VncBridge.open())
            post(VncBridge.hold(holding))
            let queued = pending
            pending = []
            queued.forEach(post)
        case .unknown: break
        }
    }

    /// Whether the owner holds the tab: the page lets input through only then.
    public func hold(_ on: Bool) {
        holding = on
        if ready { post(VncBridge.hold(on)) }
    }

    /// The page is going away: nothing more is delivered.
    public func release() {
        released = true
        if let token { relay.detach(token) }
        token = nil
        ready = false
        pending = []
    }

    private func deliver(_ message: String) {
        if released { return }
        if ready { post(message) } else { pending.append(message) }
    }

    private func post(_ message: String) { evaluate(VncBridge.script(for: message)) }
}
