import Foundation
import CryptoKit

/// KE2E for one signed-in server (spec §1.3, §5): the pinned key, the clock correction, and whether the server ever offered KE2E.
/// Shared by the transport (which reads it on every call) and the app (which saves a new pin next to the session and shows the
/// state in My → Devices). Thread-safe.
public final class KE2EState: @unchecked Sendable {
    private let lock = NSLock()
    private var _pin: KE2EPin?
    private var _clock = KE2EClock()
    private let onPin: @Sendable (KE2EPin) -> Void

    /// `onPin`: a pin was set or changed (trust on first use, verified by a scanned QR): keep it with the session.
    public init(pin: KE2EPin? = nil, onPin: @escaping @Sendable (KE2EPin) -> Void = { _ in }) {
        _pin = pin
        self.onPin = onPin
    }

    /// The pinned key: from then on every call goes through KE2E and no plaintext answer is taken (no fallback).
    public var pin: KE2EPin? { lock.withLock { _pin } }
    public var clock: KE2EClock { lock.withLock { _clock } }

    public func setPin(_ pin: KE2EPin) {
        let changed = lock.withLock { () -> Bool in
            defer { _pin = pin }
            return _pin != pin
        }
        if changed { onPin(pin) }
    }

    /// A server `now` from a clock refusal: corrected once; false when the refusal carried none.
    func correctClock(serverNow: Int64?) -> Bool { lock.withLock { _clock.correct(serverNow: serverNow) } }

    /// A scanned pairing link's key for this server: the same key marks the pin verified (true); another key changes nothing (false,
    /// the owner pairs again instead).
    @discardableResult
    public func verify(scanned key: KE2EServerKey) -> Bool {
        guard let p = pin, let next = p.scanned(key) else { return false }
        if !p.verified { setPin(next) }
        return true
    }
}

/// The one place KE2E plugs into the app: wraps the real transport, seals every API call to the pinned server key and opens the
/// answers (spec §1.4). Screens never see it. There is no plaintext mode (spec 5.18): without a pin the first call takes the
/// server's key from `GET /api/e2e/info` (trust on first use, not verified); a server that offers none (older than 0.9.0, or one
/// whose answer was tampered with on the path) is "update your server" (`unsupported`), never a reason to send in the clear.
public final class KE2ETransport: HTTPTransport, @unchecked Sendable {
    public let base: HTTPTransport
    /// The server address the API calls go to ("https://host[/path]").
    public let baseUrl: String
    public let state: KE2EState
    /// Paths that stay plaintext on purpose (spec §1.5).
    public static let plaintextPaths: Set<String> = ["/healthz", KE2E.infoPath]
    /// This app's own code (never from a server) for "the pinned server does not speak KE2E": it must be updated (no fallback).
    public static let unsupportedCode = "e2e_unsupported"

    public init(base: HTTPTransport = URLSessionTransport.shared, baseUrl: String, state: KE2EState) {
        self.base = base
        var b = baseUrl
        while b.hasSuffix("/") { b.removeLast() }
        self.baseUrl = b
        self.state = state
    }

    // ---- errors ----------------------------------------------------------------------------------------------------------------

    static func keyChanged() -> ApiError { ApiError(status: 409, detail: "The server's key changed: pair this phone again.", code: KE2E.keyChangedCode) }
    static func unsupported() -> ApiError {
        ApiError(status: ApiError.noResponse, detail: "The server does not speak end-to-end encryption: it must be updated.", code: unsupportedCode)
    }
    /// A frame that does not open, a cut-off answer: a connection error, everything read is dropped.
    static func broken() -> ApiError { ApiError(status: ApiError.noResponse, detail: "The encrypted connection broke off.") }

    // ---- paths -----------------------------------------------------------------------------------------------------------------

    private var basePath: String { URLComponents(string: baseUrl)?.percentEncodedPath ?? "" }

    /// The path and query the server routes, relative to `baseUrl` ("/api/talk?x=1").
    func innerPath(_ url: URL?) -> String? {
        guard let url, let c = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        var path = c.percentEncodedPath
        let prefix = basePath
        if !prefix.isEmpty, path.hasPrefix(prefix) { path = String(path.dropFirst(prefix.count)) }
        if path.isEmpty { path = "/" }
        if let q = c.percentEncodedQuery { path += "?" + q }
        return path
    }

    func isPlaintext(_ request: URLRequest) -> Bool {
        guard let p = innerPath(request.url) else { return true }
        return Self.plaintextPaths.contains(String(p.prefix { $0 != "?" }))
    }

    func outer(_ original: URLRequest, body: Data? = nil) throws -> URLRequest {
        guard let url = URL(string: baseUrl + KE2E.path) else { throw ApiError(status: 0, detail: "bad address") }
        var r = URLRequest(url: url, timeoutInterval: original.timeoutInterval)
        r.httpMethod = "POST"
        r.setValue(KE2E.contentType, forHTTPHeaderField: "Content-Type")
        r.httpBody = body
        return r
    }

    func head(_ request: URLRequest) throws -> KE2ERequestHead {
        guard let path = innerPath(request.url) else { throw ApiError(status: 0, detail: "bad address") }
        return KE2ERequestHead.make(method: request.httpMethod ?? "GET", path: path, headers: request.allHTTPHeaderFields ?? [:],
                                    now: state.clock.now())
    }

    /// An outer answer that is not a KE2E answer: thrown as the error it means.
    func outerFailure(_ status: Int, _ body: Data, _ headers: [String: String]) -> Error {
        let e = ApiError.from(status: status, body: body)
        if e.code == KE2E.keyChangedCode { return Self.keyChanged() }
        if status == 404 || status == 405 || (status == 200 && !headers.isEmpty) { return Self.unsupported() }
        return e
    }

    func isKE2E(_ status: Int, _ headers: [String: String]) -> Bool {
        guard status == 200 else { return false }
        guard let ct = headers["content-type"] else { return true }   // a transport that gives no headers (tests)
        return ct.lowercased().hasPrefix(KE2E.contentType)
    }

    /// A sealed or plaintext `400 {"code": "e2e_clock", "now"}`: corrects the clock (once per call).
    func clockRefusal(_ status: Int, _ body: Data) -> Bool {
        status == 400 && state.correctClock(serverNow: KE2EClock.serverNow(in: body))
    }

    // ---- trust on first use ------------------------------------------------------------------------------------------------

    /// GET /api/e2e/info in plaintext: pins the key unverified. A server that offers no key (404, a refusal, a page that is not the
    /// key) throws `unsupported`: it must be updated. Only a network failure throws anything else.
    @discardableResult
    public func adoptServerKey(appHeader: String = Versioning.currentAppHeader) async throws -> Bool {
        guard let url = URL(string: baseUrl + KE2E.infoPath) else { throw ApiError(status: 0, detail: "bad address") }
        var r = URLRequest(url: url)
        if !appHeader.isEmpty { r.setValue(appHeader, forHTTPHeaderField: Versioning.appHeader) }
        let reply = try await base.exchange(r)
        // an older server: 404 (or a refusal: older servers ask for a token on unknown addresses), or its website's page
        let json = (200...299).contains(reply.status) ? try? JSON.parse(reply.body) : nil
        guard let json, json["pk"] != nil else { throw Self.unsupported() }
        let key: KE2EServerKey
        do { key = try KE2EInfo.parse(json) } catch { throw Self.broken() }
        if let p = state.pin {
            // never replace a pinned key silently: a different one means pair again
            guard p.key == key else { throw Self.keyChanged() }
            return true
        }
        state.setPin(KE2EPin(key: key, verified: false))
        return true
    }

    /// The pinned key, taken now by trust on first use when there is none yet.
    func key() async throws -> KE2EServerKey {
        if let p = state.pin { return p.key }
        try await adoptServerKey()
        guard let p = state.pin else { throw Self.unsupported() }
        return p.key
    }

    // ---- HTTPTransport -----------------------------------------------------------------------------------------------------

    public func send(_ request: URLRequest) async throws -> (Int, Data) {
        let r = try await exchange(request)
        return (r.status, r.body)
    }

    public func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let (s, _, b) = try await streamExchange(request)
        return (s, b)
    }

    public func exchange(_ request: URLRequest) async throws -> HTTPReply {
        if isPlaintext(request) { return try await base.exchange(request) }
        return try await sealed(request, key: try await key(), retry: true)
    }

    private func sealed(_ request: URLRequest, key: KE2EServerKey, retry: Bool) async throws -> HTTPReply {
        var body = request.httpBody ?? Data()
        defer { body.resetBytes(in: 0..<body.count) }
        let ex = try KE2EExchange.seal(head: try head(request), body: body, to: key)
        let reply = try await base.exchange(try outer(request, body: ex.outerBody))
        guard isKE2E(reply.status, reply.headers) else {
            if retry, clockRefusal(reply.status, reply.body) { return try await sealed(request, key: key, retry: false) }
            throw outerFailure(reply.status, reply.body, reply.headers)
        }
        let head: KE2EResponseHead, inner: Data
        do { (head, inner) = try KE2EResponseReader.readWhole(reply.body, key: try ex.responseKey()) } catch { throw Self.broken() }
        if retry, clockRefusal(head.status, inner) { return try await sealed(request, key: key, retry: false) }
        return HTTPReply(status: head.status, body: inner, headers: head.headers)
    }

    public func streamExchange(_ request: URLRequest) async throws -> (Int, [String: String], AsyncThrowingStream<UInt8, Error>) {
        if isPlaintext(request) { return try await base.streamExchange(request) }
        return try await sealedStream(request, key: try await key(), retry: true)
    }

    private func sealedStream(_ request: URLRequest, key: KE2EServerKey, retry: Bool) async throws -> (Int, [String: String], AsyncThrowingStream<UInt8, Error>) {
        var body = request.httpBody ?? Data()
        defer { body.resetBytes(in: 0..<body.count) }
        let ex = try KE2EExchange.seal(head: try head(request), body: body, to: key)
        let (status, headers, bytes) = try await base.streamExchange(try outer(request, body: ex.outerBody))
        let source = ByteSource(bytes)
        guard isKE2E(status, headers) else {
            let plain = try await source.collect(limit: 65_536)
            if retry, clockRefusal(status, plain) { return try await sealedStream(request, key: key, retry: false) }
            throw outerFailure(status, plain, headers)
        }
        var reader = try ex.responseReader()
        var pending = Data()
        var head: KE2EResponseHead?
        do {
            while head == nil {
                guard let b = try await source.next() else { throw KE2EError.truncated }
                for part in try reader.feed([b]) {
                    switch part {
                    case .head(let h): head = h
                    case .body(let d): pending.append(d)
                    }
                }
            }
        } catch let e as ApiError { throw e } catch is CancellationError { throw CancellationError() } catch { throw Self.broken() }
        let h = head!
        if retry, h.status == 400 {
            // a clock refusal comes as one small sealed answer: read it whole
            var whole = pending
            do {
                while !reader.complete, let b = try await source.next() {
                    for case .body(let d) in try reader.feed([b]) { whole.append(d) }
                }
                try reader.finish()
            } catch { throw Self.broken() }
            if clockRefusal(h.status, whole) { return try await sealedStream(request, key: key, retry: false) }
            return (h.status, h.headers, Self.replay(whole))
        }
        let rest = AsyncThrowingStream<UInt8, Error> { cont in
            let task = Task {
                var r = reader
                do {
                    for b in pending { cont.yield(b) }
                    while let b = try await source.next() {
                        for case .body(let d) in try r.feed([b]) { for x in d { cont.yield(x) } }
                    }
                    try r.finish()
                    cont.finish()
                } catch is CancellationError {
                    cont.finish(throwing: CancellationError())
                } catch let e as ApiError {
                    cont.finish(throwing: e)
                } catch {
                    cont.finish(throwing: Task.isCancelled ? CancellationError() : Self.broken())
                }
            }
            cont.onTermination = { _ in task.cancel() }
        }
        return (h.status, h.headers, rest)
    }

    public func upload(_ request: URLRequest, body: HTTPUploadBody, progress: (@Sendable (Int64, Int64) -> Void)?) async throws -> HTTPReply {
        if isPlaintext(request) { return try await base.upload(request, body: body, progress: progress) }
        return try await sealedUpload(request, body: body, key: try await key(), progress: progress, retry: true)
    }

    /// A streamed request (envelope 0x02): head, then the body in 64 KiB frames, written to a temporary file of ciphertext only
    /// (a file is read piece by piece, never held whole) and sent from there with progress.
    private func sealedUpload(_ request: URLRequest, body: HTTPUploadBody, key: KE2EServerKey, progress: (@Sendable (Int64, Int64) -> Void)?,
                              retry: Bool) async throws -> HTTPReply {
        let ex = try KE2EExchange.sealStreamed(head: try head(request), to: key)
        let temp = FileManager.default.temporaryDirectory.appendingPathComponent("kelo-\(UUID().uuidString).ke2e")
        defer { try? FileManager.default.removeItem(at: temp) }
        try Self.writeStreamed(ex, body: body, to: temp)
        let reply = try await base.upload(try outer(request), body: .file(temp), progress: progress)
        guard isKE2E(reply.status, reply.headers) else {
            if retry, clockRefusal(reply.status, reply.body) {
                return try await sealedUpload(request, body: body, key: key, progress: progress, retry: false)
            }
            throw outerFailure(reply.status, reply.body, reply.headers)
        }
        let head: KE2EResponseHead, inner: Data
        do { (head, inner) = try KE2EResponseReader.readWhole(reply.body, key: try ex.responseKey()) } catch { throw Self.broken() }
        if retry, clockRefusal(head.status, inner) { return try await sealedUpload(request, body: body, key: key, progress: progress, retry: false) }
        return HTTPReply(status: head.status, body: inner, headers: head.headers)
    }

    static func writeStreamed(_ ex: KE2EExchange, body: HTTPUploadBody, to file: URL) throws {
        guard FileManager.default.createFile(atPath: file.path, contents: nil, attributes: [.posixPermissions: 0o600]) else {
            throw ApiError(status: 0, detail: "could not prepare the upload")
        }
        let out = try FileHandle(forWritingTo: file)
        defer { try? out.close() }
        try out.write(contentsOf: ex.outerBody)
        var w = try ex.bodyWriter()
        switch body {
        case .data(let d):
            try out.write(contentsOf: try w.whole(d))
        case .file(let url):
            let input = try FileHandle(forReadingFrom: url)
            defer { try? input.close() }
            var piece = try input.read(upToCount: KE2E.maxBodyFrame) ?? Data()
            if piece.isEmpty { try out.write(contentsOf: try w.frame(Data(), last: true)); return }
            while !piece.isEmpty {
                let next = try input.read(upToCount: KE2E.maxBodyFrame) ?? Data()
                try out.write(contentsOf: try w.frame(piece, last: next.isEmpty))
                piece = next
            }
        }
    }

    static func replay(_ data: Data) -> AsyncThrowingStream<UInt8, Error> {
        AsyncThrowingStream { c in
            for b in data { c.yield(b) }
            c.finish()
        }
    }
}

/// Reads an async byte stream from more than one place in order (the head first, then the rest in a task).
final class ByteSource: @unchecked Sendable {
    private var iterator: AsyncThrowingStream<UInt8, Error>.AsyncIterator

    init(_ stream: AsyncThrowingStream<UInt8, Error>) { iterator = stream.makeAsyncIterator() }

    func next() async throws -> UInt8? { try await iterator.next() }

    func collect(limit: Int) async throws -> Data {
        var d = Data()
        while d.count < limit, let b = try await next() { d.append(b) }
        return d
    }
}
