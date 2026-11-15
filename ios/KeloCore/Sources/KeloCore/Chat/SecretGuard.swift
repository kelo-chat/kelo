import Foundation

// A message the server held back because it holds a key, token, password or card number (Android chat/SecretGuard.kt).
// The chat send endpoints refuse it with 409 `{"detail": {"code": "secret_in_message", "spans": [...], "placeholder": "...",
// "target": {...}, "label": "..."}}`. The app then asks what to do: keep it in the Keychain (the value already filled in, saved
// straight from the app to the vault), send it as is (the same text again with `"secrets": "allow"`), or go back and edit it.

/// Where a detected secret sits in the sent text: UTF-16 code-unit offsets (`String.utf16`, the server's and Kotlin's indices), `start`
/// inclusive. Never Character offsets: an emoji or a Korean syllable before the span counts as its UTF-16 units.
public struct SecretSpan: Equatable, Hashable, Sendable {
    public static let password = "password"
    public static let card = "card"
    public static let privateKey = "private_key"

    public var start: Int
    public var end: Int
    public var kind: String
    public init(_ start: Int, _ end: Int, _ kind: String) { self.start = start; self.end = end; self.kind = kind }
}

/// The form that can keep it: "provider" (an AI service's key), "connector", "wallet" (a card) or "vault" (the Keychain).
public struct SecretTarget: Equatable, Hashable, Sendable {
    public static let provider = "provider"
    public static let connector = "connector"
    public static let wallet = "wallet"
    public static let vault = "vault"
    public static let keychain = SecretTarget(vault, "")

    public var type: String
    public var id: String
    public init(_ type: String, _ id: String) { self.type = type; self.id = id }
}

/// The server's 409 `secret_in_message` answer: the message was neither sent to the model nor stored. Never carries the value.
/// `label`: the server's guess of what it belongs to ("Anthropic", "GitHub", a connector's name), "" when it has none.
public struct SecretFound: Equatable, Sendable {
    public var spans: [SecretSpan]
    public var placeholder: String
    public var target: SecretTarget
    public var label: String
    public init(spans: [SecretSpan], placeholder: String, target: SecretTarget, label: String = "") {
        self.spans = spans; self.placeholder = placeholder; self.target = target; self.label = label
    }
}

/// What the owner keeps it as, on the save step of the sheet.
public enum SecretUse: String, CaseIterable, Sendable {
    case aiKey, connectorKey, card, login, serviceKey
}

public enum SecretGuard {
    public static let code = "secret_in_message"
    /// The request field value that sends a refused message anyway, after the owner said so
    public static let allow = "allow"

    /// The 409 answer of a chat send, or nil for any other failure.
    public static func of(_ error: Error) -> SecretFound? {
        guard let e = error as? ApiError, e.status == 409 else { return nil }
        return parse(e.detailJson)
    }

    public static func parse(_ detail: [String: JSON]?) -> SecretFound? {
        guard let detail, str(detail["code"]) == code else { return nil }
        let spans: [SecretSpan] = (detail["spans"]?.arrayValue ?? []).compactMap { e in
            guard let o = e.objectValue, let start = int(o["start"]), let end = int(o["end"]), start >= 0, end > start else { return nil }
            let kind = str(o["kind"])
            return SecretSpan(start, end, kind.isEmpty ? "token" : kind)
        }.sorted { $0.start < $1.start }
        let t = detail["target"]?.objectValue
        let type = str(t?["type"])
        return SecretFound(spans: spans, placeholder: str(detail["placeholder"]),
                           target: SecretTarget(types.contains(type) ? type : SecretTarget.vault, str(t?["id"])),
                           label: str(detail["label"]).trimmingCharacters(in: .whitespacesAndNewlines))
    }

    /// The detected values, cut from `text` (the exact text that was sent) by the spans; spans outside it are skipped.
    public static func values(_ text: String, _ spans: [SecretSpan]) -> [String] {
        let u = Array(text.utf16)
        var out: [String] = []
        for s in spans where s.start >= 0 && s.end <= u.count && s.start < s.end {
            let v = String(decoding: u[s.start..<s.end], as: UTF16.self).trimmingCharacters(in: .whitespacesAndNewlines)
            if !v.isEmpty && !out.contains(v) { out.append(v) }
        }
        return out
    }

    /// The spans moved from the sent text onto the composer's text: the sent text is the composer's text trimmed (plus attachment
    /// lines after it), so each span shifts by the leading blanks (in UTF-16 units), and spans past the typed part are dropped.
    public static func composerSpans(_ composer: String, _ spans: [SecretSpan]) -> [SecretSpan] {
        let blank = CharacterSet.whitespacesAndNewlines
        let lead = composer.unicodeScalars.prefix { blank.contains($0) }.reduce(0) { $0 + UTF16.width($1) }
        let typed = composer.trimmingCharacters(in: .whitespacesAndNewlines).utf16.count
        return spans.filter { $0.end <= typed }.map { SecretSpan($0.start + lead, $0.end + lead, $0.kind) }
    }

    /// `text` with every span whose value is `value` (all spans when nil) replaced by `with`, last span first so offsets hold.
    public static func replace(_ text: String, _ spans: [SecretSpan], with: String, value: String? = nil) -> String {
        var u = Array(text.utf16)
        let w = Array(with.utf16)
        for s in spans.sorted(by: { $0.start > $1.start }) {
            if s.start < 0 || s.end > u.count || s.start >= s.end { continue }
            if let value, String(decoding: u[s.start..<s.end], as: UTF16.self).trimmingCharacters(in: .whitespacesAndNewlines) != value { continue }
            u.replaceSubrange(s.start..<s.end, with: w)
        }
        return String(decoding: u, as: UTF16.self)
    }

    /// The ways to keep it, the likeliest first (from the server's target and the kind of the first span).
    public static func uses(_ found: SecretFound) -> [SecretUse] {
        let kind = found.spans.first?.kind
        let first: SecretUse
        if found.target.type == SecretTarget.provider && !found.target.id.isEmpty { first = .aiKey }
        else if found.target.type == SecretTarget.connector && !found.target.id.isEmpty { first = .connectorKey }
        else if found.target.type == SecretTarget.wallet || kind == SecretSpan.card { first = .card }
        else if kind == SecretSpan.password { first = .login }
        else { first = .serviceKey }
        let rest: [SecretUse]
        switch first {
        case .card: rest = []   // a card number is only ever a card
        case .login: rest = [.serviceKey]
        default: rest = [.serviceKey, .login]
        }
        var out: [SecretUse] = []
        for u in [first] + rest where !out.contains(u) { out.append(u) }
        return out
    }

    /// The API address a few well-known services' keys are used on, for the service key form ("" when unknown).
    public static func hostGuess(_ label: String) -> String { hosts[label.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()] ?? "" }

    /// A vault name for a service key from its address ("api.github.com" -> "api-github-com").
    public static func nameFor(_ host: String) -> String {
        let lower = host.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        var n = ""
        var dash = false
        for ch in lower.unicodeScalars {
            if (ch.value >= 97 && ch.value <= 122) || (ch.value >= 48 && ch.value <= 57) { n.unicodeScalars.append(ch); dash = false }
            else if !dash { n.append("-"); dash = true }
        }
        n = String(n.trimmingCharacters(in: CharacterSet(charactersIn: "-")).prefix(60))
        if n.count >= 3 { return n }
        var k = "key-\(n)"
        while k.hasSuffix("-") { k.removeLast() }
        while k.count < 3 { k.append("x") }
        return k
    }

    private static let hosts: [String: String] = [
        "github": "api.github.com", "gitlab": "gitlab.com", "stripe": "api.stripe.com", "slack": "slack.com",
        "notion": "api.notion.com", "openai": "api.openai.com", "anthropic": "api.anthropic.com", "huggingface": "huggingface.co",
        "hugging face": "huggingface.co", "sendgrid": "api.sendgrid.com", "npm": "registry.npmjs.org",
    ]
    private static let types: Set<String> = [SecretTarget.provider, SecretTarget.connector, SecretTarget.wallet, SecretTarget.vault]
    private static func str(_ e: JSON?) -> String { e?.stringValue ?? "" }
    private static func int(_ e: JSON?) -> Int? {
        guard case .number(let d)? = e, d.rounded() == d, abs(d) < 1e15 else { return nil }
        return Int(d)
    }
}

/// The vault calls behind the save step (the value goes from the app straight to these endpoints, never through chat).
public enum SecretSave {
    /// PUT /api/providers/{name}/key
    public static func aiKey(_ api: KeloApi, provider: String, value: String) async throws {
        try await api.request("PUT", "/api/providers/\(KeloApi.segment(provider))/key",
                              body: ["api_key": .string(value.trimmingCharacters(in: .whitespacesAndNewlines))])
    }

    /// GET /api/keys/{id}: the connector's key form (its fields; the server checks the key once when saved).
    public static func connectorForm(_ api: KeloApi, connector: String) async throws -> KeyForm { try await api.keyForm(connector) }

    /// POST /api/keys/{form.id} with the form's values; `value` goes into the first secret field.
    public static func connectorKey(_ api: KeloApi, form: KeyForm, value: String, others: [String: String]) async throws {
        let secret = form.fields.first(where: \.secret)?.id ?? form.fields.first?.id ?? "key"
        var values = others
        values[secret] = value.trimmingCharacters(in: .whitespacesAndNewlines)
        try await api.request("POST", "/api/keys/\(KeloApi.segment(form.id))", body: ["values": .object(values.mapValues { .string($0) })])
    }

    /// POST /api/vault/logins, then PUT /api/vault/{name}/login (kept now). Returns the login's name.
    @discardableResult
    public static func login(_ api: KeloApi, site: String, label: String, username: String, password: String) async throws -> String {
        let trim: (String) -> String = { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        let name = try await api.request("POST", "/api/vault/logins", body: ["site": .string(trim(site)), "label": .string(trim(label))])["name"]?.text ?? ""
        try await api.request("PUT", "/api/vault/\(KeloApi.segment(name))/login",
                              body: ["username": .string(trim(username)), "password": .string(password), "mode": .string("now")])
        return name
    }

    /// POST /api/credentials (a bearer key for `host`), then PUT /api/credentials/{name}/secret. Returns its name.
    @discardableResult
    public static func serviceKey(_ api: KeloApi, host: String, label: String, value: String) async throws -> String {
        var h = host.trimmingCharacters(in: .whitespacesAndNewlines)
        for p in ["https://", "http://"] where h.hasPrefix(p) { h.removeFirst(p.count) }
        if let slash = h.firstIndex(of: "/") { h = String(h[..<slash]) }
        let name = SecretGuard.nameFor(h)
        let l = label.trimmingCharacters(in: .whitespacesAndNewlines)
        try await api.request("POST", "/api/credentials", body: [
            "name": .string(name), "label": .string(l.isEmpty ? h : l), "hosts": .array([.string(h)]),
            "placement": ["type": .string("bearer_header")],
        ])
        try await api.request("PUT", "/api/credentials/\(KeloApi.segment(name))/secret",
                              body: ["secret": .string(value.trimmingCharacters(in: .whitespacesAndNewlines))])
        return name
    }
}

/// The value from a refused message, handed to the wallet's card form when the owner keeps a card number (the number is already
/// filled in there). Memory only: never saved, never logged, never in saved UI state; dropped when the form takes it (`clear`) and
/// after `ttl` seconds at the latest. `kept()` tells the chat the card was kept, so it can put a reference in the composer.
public final class SecretHandoff: @unchecked Sendable, CustomStringConvertible, CustomDebugStringConvertible {
    public static let ttlSeconds: Double = 10 * 60

    private let lock = NSLock()
    private let clock: @Sendable () -> Double
    private let ttl: Double
    private var values: [String] = []
    private var at: Double = 0
    private var saved = false

    public init(clock: @escaping @Sendable () -> Double = { ProcessInfo.processInfo.systemUptime }, ttl: Double = SecretHandoff.ttlSeconds) {
        self.clock = clock
        self.ttl = ttl
    }

    public func offer(_ found: [String]) {
        lock.withLock { values = found.filter { !$0.isEmpty }; at = clock() }
    }

    /// The first value, while it is still fresh.
    public func peek() -> String? {
        lock.withLock {
            if values.isEmpty { return nil }
            if clock() - at > ttl { values = []; return nil }
            return values.first
        }
    }

    /// The form kept the value: it is dropped here, and the chat is told (`takeSaved`).
    public func kept() { lock.withLock { values = []; saved = true } }

    /// True once after `kept()`.
    public func takeSaved() -> Bool { lock.withLock { defer { saved = false }; return saved } }

    public func clear() { lock.withLock { values = [] } }

    public var description: String { "SecretHandoff(\(lock.withLock { values.count }))" }   // never the value
    public var debugDescription: String { description }
}
