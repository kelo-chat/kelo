import Foundation

/// One server-sent event.
public struct SSEMessage: Equatable, Sendable {
    public var event: String
    public var data: String
    public var id: String?

    public init(event: String = "message", data: String, id: String? = nil) {
        self.event = event; self.data = data; self.id = id
    }
}

/// A server-sent-events reader (the text/event-stream format): feed it bytes as they come, it hands back each complete event.
/// Lines end in LF, CR or CRLF; "data:" lines of one event are joined with "\n"; ":" lines are comments (keep-alives); a blank line
/// ends the event. The server's turn streams send one `data: {json}` line per event (server.py `_sse`).
public struct SSEParser: Sendable {
    private var line: [UInt8] = []
    private var dataLines: [String] = []
    private var event = ""
    private var lastId: String?
    private var sawCR = false

    public init() {}

    /// Adds bytes; returns the events they completed.
    public mutating func feed<S: Sequence>(_ bytes: S) -> [SSEMessage] where S.Element == UInt8 {
        var out: [SSEMessage] = []
        for b in bytes {
            if sawCR {
                sawCR = false
                if b == 0x0A { continue }   // the LF of a CRLF
            }
            if b == 0x0A || b == 0x0D {
                sawCR = b == 0x0D
                if let m = endLine() { out.append(m) }
            } else {
                line.append(b)
            }
        }
        return out
    }

    /// The stream ended: an event without its closing blank line is dropped, as the format says.
    public mutating func finish() -> [SSEMessage] {
        line.removeAll(); dataLines.removeAll(); event = ""
        return []
    }

    private mutating func endLine() -> SSEMessage? {
        let text = String(decoding: line, as: UTF8.self)
        line.removeAll(keepingCapacity: true)
        if text.isEmpty {
            defer { dataLines.removeAll(); event = "" }
            guard !dataLines.isEmpty else { return nil }
            return SSEMessage(event: event.isEmpty ? "message" : event, data: dataLines.joined(separator: "\n"), id: lastId)
        }
        if text.hasPrefix(":") { return nil }
        let field: String
        var value: String
        if let colon = text.firstIndex(of: ":") {
            field = String(text[..<colon])
            value = String(text[text.index(after: colon)...])
            if value.hasPrefix(" ") { value.removeFirst() }
        } else {
            field = text
            value = ""
        }
        switch field {
        case "data": dataLines.append(value)
        case "event": event = value
        case "id": if !value.contains("\0") { lastId = value }
        default: break   // "retry" and unknown fields are ignored
        }
        return nil
    }
}
