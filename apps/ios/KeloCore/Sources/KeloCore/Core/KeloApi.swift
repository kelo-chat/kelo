import Foundation

/// An answer with its headers (names in lower case).
public struct HTTPReply: Sendable {
    public var status: Int
    public var body: Data
    public var headers: [String: String]

    public init(status: Int, body: Data, headers: [String: String] = [:]) {
        self.status = status
        self.body = body
        self.headers = headers
    }
}

/// A request body that goes up with progress: bytes in memory, or a file read from disk as it is sent (never held whole).
public enum HTTPUploadBody: Sendable {
    case data(Data)
    case file(URL)
}

/// One HTTP exchange: what the API client needs from the network. `URLSessionTransport` is the real one, `KE2ETransport` wraps it
/// for end-to-end encryption; tests use a fake, so nothing here ever needs a server.
public protocol HTTPTransport: Sendable {
    /// Sends `request` and returns the status code and the whole body. Throws `URLError` (or any error) when nothing came back.
    func send(_ request: URLRequest) async throws -> (Int, Data)
    /// Sends `request` and returns the status code and the body as a stream of bytes (server-sent events).
    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>)
    /// `send` with the answer's headers. Default: `send` without headers.
    func exchange(_ request: URLRequest) async throws -> HTTPReply
    /// `stream` with the answer's headers. Default: `stream` without headers.
    func streamExchange(_ request: URLRequest) async throws -> (Int, [String: String], AsyncThrowingStream<UInt8, Error>)
    /// Sends `body` with `request`, reporting (sent, total) bytes. Default: the body read into memory and `exchange`.
    func upload(_ request: URLRequest, body: HTTPUploadBody, progress: (@Sendable (Int64, Int64) -> Void)?) async throws -> HTTPReply
}

extension HTTPTransport {
    public func exchange(_ request: URLRequest) async throws -> HTTPReply {
        let (status, data) = try await send(request)
        return HTTPReply(status: status, body: data)
    }

    public func streamExchange(_ request: URLRequest) async throws -> (Int, [String: String], AsyncThrowingStream<UInt8, Error>) {
        let (status, bytes) = try await stream(request)
        return (status, [:], bytes)
    }

    public func upload(_ request: URLRequest, body: HTTPUploadBody, progress: (@Sendable (Int64, Int64) -> Void)?) async throws -> HTTPReply {
        var r = request
        switch body {
        case .data(let d): r.httpBody = d
        case .file(let u): r.httpBody = try Data(contentsOf: u)
        }
        let n = Int64(r.httpBody?.count ?? 0)
        let reply = try await exchange(r)
        progress?(n, n)
        return reply
    }
}

/// The real transport over URLSession. Timeouts like the Android client's: 15 s to connect is the closest URLSession offers
/// (`timeoutIntervalForRequest` is the idle time between bytes, 60 s, long enough for GET /api/turns/{id}?wait=20).
public final class URLSessionTransport: HTTPTransport, @unchecked Sendable {
    public static let shared = URLSessionTransport()

    public let session: URLSession

    public init(session: URLSession? = nil) {
        if let session {
            self.session = session
        } else {
            let config = URLSessionConfiguration.default
            config.timeoutIntervalForRequest = 60
            config.timeoutIntervalForResource = 600
            config.waitsForConnectivity = false
            config.httpCookieStorage = nil          // the API is bearer-token only
            config.urlCache = nil                   // answers carry personal data: nothing on disk
            self.session = URLSession(configuration: config)
        }
    }

    public func send(_ request: URLRequest) async throws -> (Int, Data) {
        let r = try await exchange(request)
        return (r.status, r.body)
    }

    public func exchange(_ request: URLRequest) async throws -> HTTPReply {
        let (data, response) = try await session.data(for: request)
        return HTTPReply(status: (response as? HTTPURLResponse)?.statusCode ?? 0, body: data, headers: Self.headers(response))
    }

    public func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let (status, _, bytes) = try await streamExchange(request)
        return (status, bytes)
    }

    public func streamExchange(_ request: URLRequest) async throws -> (Int, [String: String], AsyncThrowingStream<UInt8, Error>) {
        let (bytes, response) = try await session.bytes(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        let stream = AsyncThrowingStream<UInt8, Error> { cont in
            let task = Task {
                do {
                    for try await b in bytes { cont.yield(b) }
                    cont.finish()
                } catch { cont.finish(throwing: error) }
            }
            cont.onTermination = { _ in task.cancel() }
        }
        return (status, Self.headers(response), stream)
    }

    public func upload(_ request: URLRequest, body: HTTPUploadBody, progress: (@Sendable (Int64, Int64) -> Void)?) async throws -> HTTPReply {
        let delegate = progress.map { UploadProgressDelegate($0) }
        let data: Data, response: URLResponse
        switch body {
        case .data(let d): (data, response) = try await session.upload(for: request, from: d, delegate: delegate)
        case .file(let u): (data, response) = try await session.upload(for: request, fromFile: u, delegate: delegate)
        }
        return HTTPReply(status: (response as? HTTPURLResponse)?.statusCode ?? 0, body: data, headers: Self.headers(response))
    }

    static func headers(_ response: URLResponse) -> [String: String] {
        var out: [String: String] = [:]
        (response as? HTTPURLResponse)?.allHeaderFields.forEach { k, v in out[String(describing: k).lowercased()] = String(describing: v) }
        return out
    }
}

/// Bytes sent so far of an upload task.
final class UploadProgressDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    let report: @Sendable (Int64, Int64) -> Void
    init(_ report: @escaping @Sendable (Int64, Int64) -> Void) { self.report = report }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64, totalBytesSent: Int64,
                    totalBytesExpectedToSend: Int64) {
        report(totalBytesSent, totalBytesExpectedToSend)
    }
}

/// The Accept-Language value every request carries; set by the app on start, on a language pick and when the phone's languages
/// change (`AcceptLanguage.build`). Empty sends nothing.
public enum AcceptLanguageValue {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var value = ""

    public static var current: String {
        get { lock.lock(); defer { lock.unlock() }; return value }
        set { lock.lock(); value = newValue; lock.unlock() }
    }
}

/// The Kelo server's HTTP API, for one server and one token (Android core/KeloApi.kt).
///
/// - Every call sends `Authorization: Bearer <token>` (left out when `token` is empty, e.g. for POST /api/pair) and the
///   `Accept-Language` header (`AcceptLanguageValue.current`, unless the request already has one).
/// - A failed call throws `ApiError`; a 401 also calls `onUnauthorized` (the app then signs this device out).
/// - Calls are cancellable: cancelling the Task cancels the HTTP call (URLSession async API).
/// - Models decode leniently (unknown fields ignored, missing ones take defaults); a 2xx answer that is not JSON (a captive portal,
///   a wrong address) is "server not reached" (status 0), never a crash.
public final class KeloApi: Sendable {
    /// e.g. "https://kelo.example.com" (no trailing slash).
    public let baseUrl: String
    public let token: String
    public let transport: HTTPTransport
    let onUnauthorized: @Sendable () -> Void
    let acceptLanguage: @Sendable () -> String
    /// Every failed call (the app watches for 426 app_too_old, 409 e2e_key_changed, a server too old for KE2E).
    let onFailure: @Sendable (ApiError) -> Void
    /// The `X-Kelo-Server` header of every answer that came back (nil when the server sent none: older than 0.9.0).
    let onServerVersion: @Sendable (String?) -> Void
    /// `X-Kelo-App` sent with every request ("" sends none).
    let appHeader: String
    /// `X-Kelo-App-Channel` sent with every request ("" sends none).
    let channelHeader: String
    /// A newer released app named by an answer of /api/about or /api/profile (`X-Kelo-App-Update`; nil when that answer named none).
    let onAppUpdate: @Sendable (String?) -> Void
    /// A newer server release the caller may install (`X-Kelo-Server-Update` on /api/about and /api/profile; nil when none).
    let onServerUpdate: @Sendable (String?) -> Void
    /// Holds this session's upload limits answer (see `uploadLimits`).
    let uploadLimitsBox = UploadLimitsBox()

    public init(
        baseUrl: String,
        token: String,
        transport: HTTPTransport = URLSessionTransport.shared,
        acceptLanguage: @escaping @Sendable () -> String = { AcceptLanguageValue.current },
        appHeader: String = Versioning.currentAppHeader,
        channelHeader: String = Versioning.currentChannel,
        onUnauthorized: @escaping @Sendable () -> Void = {},
        onFailure: @escaping @Sendable (ApiError) -> Void = { _ in },
        onServerVersion: @escaping @Sendable (String?) -> Void = { _ in },
        onAppUpdate: @escaping @Sendable (String?) -> Void = { _ in },
        onServerUpdate: @escaping @Sendable (String?) -> Void = { _ in }
    ) {
        var b = baseUrl
        while b.hasSuffix("/") { b.removeLast() }
        self.baseUrl = b
        self.token = token
        self.transport = transport
        self.acceptLanguage = acceptLanguage
        self.appHeader = appHeader
        self.channelHeader = channelHeader
        self.onAppUpdate = onAppUpdate
        self.onServerUpdate = onServerUpdate
        self.onUnauthorized = onUnauthorized
        self.onFailure = onFailure
        self.onServerVersion = onServerVersion
    }

    public convenience init(session: Session, transport: HTTPTransport = URLSessionTransport.shared, onUnauthorized: @escaping @Sendable () -> Void = {},
                            onFailure: @escaping @Sendable (ApiError) -> Void = { _ in },
                            onServerVersion: @escaping @Sendable (String?) -> Void = { _ in },
                            onAppUpdate: @escaping @Sendable (String?) -> Void = { _ in },
                            onServerUpdate: @escaping @Sendable (String?) -> Void = { _ in }) {
        self.init(baseUrl: session.serverUrl, token: session.token, transport: transport, onUnauthorized: onUnauthorized,
                  onFailure: onFailure, onServerVersion: onServerVersion, onAppUpdate: onAppUpdate, onServerUpdate: onServerUpdate)
    }

    /// The transport's end-to-end layer, when this API talks through one.
    public var e2e: KE2ETransport? { transport as? KE2ETransport }

    /// Absolute URL text for an API path such as "/api/goals".
    public func urlString(_ path: String) -> String { baseUrl + (path.hasPrefix("/") ? path : "/" + path) }

    /// A request with the address, the bearer token and the language set, for calls the helpers below do not cover.
    public func makeRequest(_ path: String, method: String = "GET") throws -> URLRequest {
        guard let url = URL(string: urlString(path)) else { throw ApiError(status: 0, detail: "bad address") }
        var r = URLRequest(url: url)
        r.httpMethod = method
        if !token.isEmpty { r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let lang = acceptLanguage()
        if !lang.isEmpty { r.setValue(lang, forHTTPHeaderField: AcceptLanguage.header) }
        if !appHeader.isEmpty { r.setValue(appHeader, forHTTPHeaderField: Versioning.appHeader) }
        if !channelHeader.isEmpty { r.setValue(channelHeader, forHTTPHeaderField: Versioning.channelHeader) }
        return r
    }

    /// Runs `request` and returns the body of a 2xx answer; anything else becomes an `ApiError`.
    public func send(_ request: URLRequest) async throws -> Data {
        try await sendReply(request).body
    }

    /// `send` with the answer's headers (lower-case names).
    public func sendReply(_ request: URLRequest) async throws -> HTTPReply {
        try await checked(path: request.url?.path) { try await self.transport.exchange(request) }
    }

    /// A request body sent with progress (uploads): a file goes up from disk, never read whole. Errors as for `send`.
    public func upload(_ request: URLRequest, body: HTTPUploadBody, progress: (@Sendable (Int64, Int64) -> Void)? = nil) async throws -> Data {
        try await checked(path: request.url?.path) { try await self.transport.upload(request, body: body, progress: progress) }.body
    }

    private func checked(path: String? = nil, _ run: () async throws -> HTTPReply) async throws -> HTTPReply {
        let reply: HTTPReply
        do {
            reply = try await run()
        } catch is CancellationError {
            throw CancellationError()
        } catch let e as URLError where e.code == .cancelled {
            throw CancellationError()
        } catch let e as ApiError {
            onFailure(e)
            throw e
        } catch {
            if Task.isCancelled { throw CancellationError() }
            let e = ApiError(status: ApiError.noResponse, detail: error.localizedDescription)
            onFailure(e)
            throw e
        }
        if reply.status != 0 { onServerVersion(reply.headers[Versioning.serverHeader.lowercased()]) }
        // a newer app that works with this server: only /api/about and /api/profile say so (no header there = none any more)
        if reply.status == 200, path == "/api/about" || path == "/api/profile" {
            onAppUpdate(AppNoticeRules.clean(reply.headers[Versioning.updateHeader.lowercased()]))
            onServerUpdate(AppNoticeRules.clean(reply.headers[Versioning.serverUpdateHeader.lowercased()]))
        }
        guard (200...299).contains(reply.status) else { throw failure(reply.status, reply.body) }
        return reply
    }

    func failure(_ status: Int, _ body: Data) -> ApiError {
        if status == 401 { onUnauthorized() }
        let e = ApiError.from(status: status, body: body)
        onFailure(e)
        return e
    }

    static let jsonMethods: Set<String> = ["POST", "PUT", "PATCH"]

    /// One JSON call. `body` is sent as application/json; an empty answer comes back as `.null`.
    @discardableResult
    public func request(_ method: String, _ path: String, body: JSON? = nil) async throws -> JSON {
        var r = try makeRequest(path, method: method)
        if let body {
            r.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
            r.httpBody = Data(body.compactString.utf8)
        } else if KeloApi.jsonMethods.contains(method) {
            r.httpBody = Data()
        }
        return try KeloApi.readJson(await send(r))
    }

    /// A JSON call whose body is given as bytes (a request that carries card details: they never become a String). The caller
    /// wipes the bytes afterwards.
    @discardableResult
    public func requestBytes(_ method: String, _ path: String, body: Data) async throws -> JSON {
        var r = try makeRequest(path, method: method)
        r.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        r.httpBody = body
        return try KeloApi.readJson(await send(r))
    }

    public func get<T: Decodable>(_ path: String, as type: T.Type = T.self) async throws -> T {
        try KeloApi.decode(T.self, try await request("GET", path))
    }
    public func post<T: Decodable>(_ path: String, body: JSON? = nil, as type: T.Type = T.self) async throws -> T {
        try KeloApi.decode(T.self, try await request("POST", path, body: body))
    }
    public func put<T: Decodable>(_ path: String, body: JSON? = nil, as type: T.Type = T.self) async throws -> T {
        try KeloApi.decode(T.self, try await request("PUT", path, body: body))
    }
    public func patch<T: Decodable>(_ path: String, body: JSON? = nil, as type: T.Type = T.self) async throws -> T {
        try KeloApi.decode(T.self, try await request("PATCH", path, body: body))
    }
    public func delete<T: Decodable>(_ path: String, body: JSON? = nil, as type: T.Type = T.self) async throws -> T {
        try KeloApi.decode(T.self, try await request("DELETE", path, body: body))
    }

    /// The same with progress; a file goes up from disk (through KE2E as a streamed 0x02 request).
    public func upload(name: String, body: HTTPUploadBody, contentType: String?, progress: (@Sendable (Int64, Int64) -> Void)? = nil) async throws -> String {
        var r = try makeRequest("/api/uploads/\(KeloApi.segment(name))", method: "PUT")
        if let contentType { r.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        return try KeloApi.readJson(await upload(r, body: body, progress: progress))["path"]?.text ?? ""
    }

    /// Raw bytes of a GET (e.g. /api/uploads/{name}/file, /api/agent/avatar). Errors as for `request`.
    public func download(_ path: String) async throws -> Data {
        try await send(makeRequest(path))
    }

    /// A 2xx body as JSON: empty or blank is `.null`; anything that is not JSON is "server not reached".
    public static func readJson(_ data: Data) throws -> JSON {
        if data.isEmpty || String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return .null }
        do { return try JSON.parse(data) } catch { throw ApiError(status: 0, detail: "unreadable answer") }
    }

    public static func decode<T: Decodable>(_ type: T.Type, _ value: JSON) throws -> T {
        do { return try JSON.decode(T.self, from: value) } catch { throw ApiError(status: 0, detail: "unreadable answer") }
    }

    /// One path segment, encoded like encodeURIComponent (spaces as %20, never +).
    public static func segment(_ s: String) -> String {
        var allowed = CharacterSet.alphanumerics.intersection(CharacterSet(charactersIn: Unicode.Scalar(0)...Unicode.Scalar(127)))
        allowed.insert(charactersIn: "-_.!~*'()")
        return s.addingPercentEncoding(withAllowedCharacters: allowed) ?? s
    }

    /// A query value, encoded the same way.
    public static func query(_ s: String) -> String { segment(s) }
}
