import Foundation

/// A pairing link as the server makes it (POST /api/devices/pair): "https://host[:port]/#pair=CODE", and with KE2E
/// "…/#pair=CODE&k=<base64url server key>" (docs/security/e2e-and-phone-key.md §1.3). The QR code on the server's pairing screen holds
/// exactly this text, so a scanned code and a pasted link go through the same `PairingLinks.parse`.
public struct PairingLink: Equatable, Sendable {
    public let serverUrl: String
    public let code: String
    /// The server's KE2E key from `k`: pinned as verified (it came from the owner's own screen). nil for an older server.
    public let serverKey: KE2EServerKey?

    public init(serverUrl: String, code: String, serverKey: KE2EServerKey? = nil) {
        self.serverUrl = serverUrl
        self.code = code
        self.serverKey = serverKey
    }
}

/// Android core/Pairing.kt `PairingLinks`, same rules.
public enum PairingLinks {
    public static let marker = "#pair="
    /// The server checks the code's length too (PairIn in server.py).
    public static let codeLength = 16...128

    /// A pairing code typed by hand in the access-key field, as it goes to /api/pair: trimmed, single spaces between groups taken
    /// out ("ABCD EFGH JKMN PQRS" -> "ABCDEFGHJKMNPQRS"). Android ConnectViewModel.codeOf.
    public static func codeOf(_ text: String) -> String {
        text.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: " ", with: "")
    }

    /// A pairing code typed by hand in the access-key field (the server's multi-use review code, "ABCD-EFGH-JKMN-PQRS", dashes and
    /// spaces optional): what the server could accept once `codeOf` took the spaces out (PairIn: 16-128 characters, no other
    /// whitespace). Android ConnectViewModel.looksLikeCode.
    public static func looksLikeCode(_ text: String) -> Bool {
        let t = codeOf(text)
        return codeLength.contains(t.count) && !t.unicodeScalars.contains { CharacterSet.whitespacesAndNewlines.contains($0) || CharacterSet.controlCharacters.contains($0) }
    }

    /// Reads a scanned or pasted pairing link; nil when `raw` is not one. Fragment parameters after the code ("&k=…", and any later
    /// ones) are split off before the code is read; unknown ones are ignored. A `k` that is not a valid key makes the link unusable
    /// (never a silent fall back to an unpinned pairing).
    public static func parse(_ raw: String) -> PairingLink? {
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let at = s.range(of: marker), at.lowerBound > s.startIndex else { return nil }
        let params = s[at.upperBound...].split(separator: "&", omittingEmptySubsequences: false).map(String.init)
        var serverKey: KE2EServerKey?
        for p in params.dropFirst() where p.hasPrefix("k=") {
            guard let k = percentDecode(String(p.dropFirst(2))), let key = try? KE2EServerKey(base64url: k) else { return nil }
            serverKey = key
        }
        guard let code = percentDecode(params.first ?? "")?.trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
        guard codeLength.contains(code.count),
              !code.unicodeScalars.contains(where: { CharacterSet.whitespacesAndNewlines.contains($0) || CharacterSet.controlCharacters.contains($0) })
        else { return nil }
        guard let server = ServerAddress.normalize(String(s[..<at.lowerBound]), defaultScheme: nil) else { return nil }
        return PairingLink(serverUrl: server, code: code, serverKey: serverKey)
    }

    /// Like decodeURIComponent: "%xx" sequences only ("+" stays "+"); nil for a broken escape.
    static func percentDecode(_ s: String) -> String? {
        // a lone "%" not followed by two hex digits is an error, as in java.net.URLDecoder
        let scalars = Array(s.unicodeScalars)
        var i = 0
        while i < scalars.count {
            if scalars[i] == "%" {
                guard i + 2 < scalars.count, scalars[i + 1].properties.isASCIIHexDigit, scalars[i + 2].properties.isASCIIHexDigit else { return nil }
                i += 3
            } else { i += 1 }
        }
        return s.removingPercentEncoding
    }
}

/// Typed server addresses (Android core/Pairing.kt `ServerAddress`).
public enum ServerAddress {
    /// Plain http is allowed only to these, and only in debug builds (release builds talk https only, as App Transport Security
    /// requires by default).
    public static let cleartextHosts: Set<String> = ["localhost", "127.0.0.1", "10.0.2.2"]

    #if DEBUG
    public static let isDebugBuild = true
    #else
    public static let isDebugBuild = false
    #endif

    /// "kelo.example.com/" -> "https://kelo.example.com"; "http://10.0.2.2:8000" stays. Nil when it is not a usable address (other
    /// schemes, no host, a user name or password, a query, a fragment). A path is kept (without the trailing slash). `defaultScheme`
    /// is added when the text has none; nil means a scheme is required.
    public static func normalize(_ raw: String, defaultScheme: String? = "https") -> String? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return nil }
        if !s.contains("://") {
            guard let d = defaultScheme else { return nil }
            s = d + "://" + s
        }
        guard let c = URLComponents(string: s, encodingInvalidCharacters: false) else { return nil }
        guard let scheme = c.scheme?.lowercased(), scheme == "http" || scheme == "https" else { return nil }
        guard var host = c.host?.lowercased(), !host.isEmpty else { return nil }
        if c.percentEncodedUser != nil || c.percentEncodedPassword != nil || c.percentEncodedQuery != nil { return nil }
        if let f = c.percentEncodedFragment, !f.isEmpty { return nil }
        if !hostIsValid(host) { return nil }
        if host.contains(":") && !host.hasPrefix("[") { host = "[\(host)]" }
        let port = c.port.map { ":\($0)" } ?? ""
        var path = c.percentEncodedPath
        while path.hasSuffix("/") { path.removeLast() }
        return "\(scheme)://\(host)\(port)\(path)"
    }

    /// Server names as java.net.URI accepts them: letters, digits, '-' and '.', or an IP literal.
    static func hostIsValid(_ host: String) -> Bool {
        if host.contains(":") { return true }   // IPv6 literal, already checked by URLComponents
        return host.unicodeScalars.allSatisfy { ($0.isASCII && (CharacterSet.alphanumerics.contains($0))) || $0 == "-" || $0 == "." }
            && !host.hasPrefix(".") && !host.hasPrefix("-")
    }

    /// https anywhere; plain http only to `cleartextHosts` and only in a debug build.
    public static func isAllowed(_ normalized: String, debug: Bool = ServerAddress.isDebugBuild) -> Bool {
        guard let c = URLComponents(string: normalized) else { return false }
        let host = (c.host ?? "").lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        return c.scheme == "https" || (debug && c.scheme == "http" && cleartextHosts.contains(host))
    }
}

/// The signed-in state of one phone: which server, with which device token. The app keeps it in the Keychain (needs Xcode, see
/// README); nothing here stores it.
public struct Session: Equatable, Sendable, Codable {
    public let serverUrl: String
    public let token: String
    /// The server's pinned KE2E key (spec §1.3); nil for a server that never offered KE2E (or not checked yet). Optional, so a
    /// session saved by an older version still reads.
    public var e2e: KE2EPin?

    public init(serverUrl: String, token: String, e2e: KE2EPin? = nil) {
        self.serverUrl = serverUrl
        self.token = token
        self.e2e = e2e
    }
}

/// POST /api/pair answer.
public struct PairResult: Decodable, Equatable, Sendable {
    public let token: String
    public let deviceId: Int64?

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        token = j.str("token")
        deviceId = j.optInt64("device_id")
    }
}

/// The two ways a phone joins a Kelo server. Neither saves anything; the caller keeps the session (with its KE2E pin).
///
/// Always through KE2E (spec 5.18): a link with `k` pins that key as verified, so the pairing call itself is already sealed; without
/// `k` (a typed code or key) the key comes from `GET /api/e2e/info` by trust on first use (not verified). A server that offers no
/// key throws `ApiError` with code `KE2ETransport.unsupportedCode` ("update your server").
public struct PairingClient: Sendable {
    let transport: HTTPTransport
    let appHeader: String

    public init(transport: HTTPTransport = URLSessionTransport.shared, appHeader: String = Versioning.currentAppHeader) {
        self.transport = transport
        self.appHeader = appHeader
    }

    /// The KE2E transport for `serverUrl`, with the link's key or the one the server offers.
    func connect(_ serverUrl: String, key: KE2EServerKey?) async throws -> (HTTPTransport, KE2EState) {
        let state = KE2EState(pin: key.map { KE2EPin(key: $0, verified: true) })
        let e2e = KE2ETransport(base: transport, baseUrl: serverUrl, state: state)
        if key == nil { try await e2e.adoptServerKey(appHeader: appHeader) }
        return (e2e, state)
    }

    /// POST /api/pair {code, name} -> this device's own token. Throws `ApiError` (400: expired or used link, 429: too many tries).
    /// The one-time code is sent once and never kept.
    public func pair(_ link: PairingLink, deviceName: String) async throws -> Session {
        let (t, state) = try await connect(link.serverUrl, key: link.serverKey)
        let api = KeloApi(baseUrl: link.serverUrl, token: "", transport: t, appHeader: appHeader)
        let result: PairResult = try await api.post("/api/pair", body: ["code": .string(link.code), "name": .string(deviceName)])
        if result.token.isEmpty { throw ApiError(status: 0, detail: "unreadable answer") }
        return Session(serverUrl: link.serverUrl, token: result.token, e2e: state.pin)
    }

    /// Server address + access key, checked with GET /api/profile. Throws `ApiError` (401: wrong key).
    public func checkKey(serverUrl: String, key: String) async throws -> (Session, Profile) {
        let (t, state) = try await connect(serverUrl, key: nil)
        let api = KeloApi(baseUrl: serverUrl, token: key, transport: t, appHeader: appHeader)
        let profile: Profile = try await api.get("/api/profile")
        return (Session(serverUrl: serverUrl, token: key, e2e: state.pin), profile)
    }
}
