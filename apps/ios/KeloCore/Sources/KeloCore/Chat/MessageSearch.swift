import Foundation

// Search across the chat history: GET /api/messages/search (hits, newest first) and GET /api/messages/around (the messages around a
// hit). The shared rules (query trimming, paging by `before`, whether a hit is loaded, the parts of a snippet) live here, so the
// same decisions are made on every app.

/// One hit of GET /api/messages/search. `snippet` has the matched words wrapped in « and » (see `MessageSearchRules.parts`).
public struct MessageHit: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64
    public var role: String
    public var snippet: String
    public var createdAt: Double

    public init(id: Int64, role: String = "", snippet: String = "", createdAt: Double = 0) {
        self.id = id; self.role = role; self.snippet = snippet; self.createdAt = createdAt
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optInt64("id"))
        role = j.str("role")
        snippet = j.str("snippet")
        createdAt = j.double("created_at")
    }

    /// The server sends the list bare, newest first; a row without an id is left out.
    public static func parseList(_ e: JSON) -> [MessageHit] {
        (e.arrayValue ?? []).compactMap { try? KeloApi.decode(MessageHit.self, $0) }
    }
}

public enum MessageSearchRules {
    /// The page size the app asks for (the server's default), and the largest the server takes.
    public static let defaultLimit = 20
    public static let maxLimit = 50
    /// Messages on each side of a hit when its window is fetched (GET /api/messages/around, n).
    public static let windowSide = 10
    /// Messages fetched after the newest one shown, when the window is followed down to the newest message.
    public static let newerPage = 50

    /// What the search field sends: the typed text trimmed, inner whitespace collapsed to single spaces (Android ChatSearchRules.query);
    /// nil when nothing is left (no request).
    public static func query(_ typed: String) -> String? {
        let t = typed.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
        return t.isEmpty ? nil : t
    }

    /// A result from the user's own message (its row says "You") rather than the agent's.
    public static func isMine(_ role: String) -> Bool { role == "user" }

    public static func clampLimit(_ n: Int) -> Int { min(max(n, 1), maxLimit) }

    /// GET /api/messages/search?q=&limit=&before= (before: the smallest id of the page shown; nil for the first page).
    public static func searchPath(query: String, limit: Int = defaultLimit, before: Int64? = nil) -> String {
        var path = "/api/messages/search?q=\(KeloApi.query(Self.query(query) ?? query))&limit=\(clampLimit(limit))"
        if let before { path += "&before=\(before)" }
        return path
    }

    /// GET /api/messages/around?id=&n= (the hit in the middle, n messages on each side).
    public static func aroundPath(id: Int64, n: Int = windowSide) -> String {
        "/api/messages/around?id=\(id)&n=\(clampLimit(n))"
    }

    /// Where the next page starts: the smallest id of a full page. nil when the page was short (nothing more).
    public static func nextBefore(_ page: [MessageHit], limit: Int = defaultLimit) -> Int64? {
        guard !page.isEmpty, page.count >= clampLimit(limit) else { return nil }
        return page.map(\.id).min()
    }

    /// The hits shown plus a page fetched later; a hit already shown is not added twice (a message can move between two pages).
    public static func appendPage(_ shown: [MessageHit], _ page: [MessageHit]) -> [MessageHit] {
        var seen = Set(shown.map(\.id))
        var out = shown
        for hit in page where seen.insert(hit.id).inserted { out.append(hit) }
        return out
    }

    /// Opening a hit: a message already in the chat is scrolled to; any other is fetched with its window first.
    public enum Opening: Equatable, Sendable { case scroll, fetchWindow }

    public static func opening(_ id: Int64, loaded: Set<Int64>) -> Opening {
        loaded.contains(id) ? .scroll : .fetchWindow
    }

    /// One piece of a snippet: plain text, or the matched words (between « and »).
    public struct Part: Equatable, Sendable {
        public var text: String
        public var matched: Bool
        public init(text: String, matched: Bool) { self.text = text; self.matched = matched }
    }

    /// A snippet split into plain and matched pieces (Android ChatSearchRules.snippet): « starts a match and » ends it, the marks
    /// themselves are never shown, a stray mark only splits the text, and a match the snippet never closes runs to its end.
    public static func parts(_ snippet: String) -> [Part] {
        var out: [Part] = []
        var buf = ""
        var inMatch = false
        func flush() {
            if !buf.isEmpty { out.append(Part(text: buf, matched: inMatch)) }
            buf = ""
        }
        for ch in snippet {
            switch ch {
            case "«": flush(); inMatch = true
            case "»": flush(); inMatch = false
            default: buf.append(ch)
            }
        }
        flush()
        return out
    }
}
