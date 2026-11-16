import Foundation

/// What comes over a live view connection.
public enum LiveEvent: Sendable {
    /// A text message (status, frame header, pong, error, ended).
    case message(LiveIn)
    /// A frame's JPEG, with the header that came right before it.
    case frame(FrameHeader, Data)
}

/// The live view's network side for one tab (server.py LIVE VIEW API; Android agent/LiveClient.kt): a one-use ticket, then a
/// WebSocket that streams frames (a text header, then the JPEG as a binary message), and pings every 20 s. The reconnect policy is
/// `LiveMachine`; the screen drives both. Typed text goes only into the one message that carries it and is never logged.
public final class LiveSocket: @unchecked Sendable {
    public let api: KeloApi
    public let tabId: String
    let session: URLSession
    private let lock = NSLock()
    private var task: URLSessionWebSocketTask?
    private var pingTask: Task<Void, Never>?
    /// The live view through KE2E (`/e2e/ws`): every message both ways sealed (nil for a server that never offered KE2E).
    private var cipher: KE2EWebSocketCipher?

    public init(api: KeloApi, tabId: String, session: URLSession = .shared) {
        self.api = api
        self.tabId = tabId
        self.session = session
    }

    /// POST /api/browser/sessions/{id}/live-ticket (404 = this server has no browser).
    public func ticket() async throws -> LiveTicket {
        try await api.post("/api/browser/sessions/\(KeloApi.segment(tabId))/live-ticket")
    }

    /// wss://host/api/browser/sessions/{id}/live?ticket=...&max_width=...&quality=...&fps=... (ws:// for an http server).
    public static func socketURL(baseUrl: String, tabId: String, ticket: String, quality: LiveQuality) -> URL? {
        var base = baseUrl
        if base.hasPrefix("https://") { base = "wss://" + base.dropFirst("https://".count) }
        else if base.hasPrefix("http://") { base = "ws://" + base.dropFirst("http://".count) }
        else { return nil }
        return URL(string: "\(base)/api/browser/sessions/\(KeloApi.segment(tabId))/live?ticket=\(KeloApi.query(ticket))&\(quality.query())")
    }

    /// Opens the WebSocket with `ticket` and streams what comes. The stream ends with an error carrying the close code
    /// (`LiveClosed`) so the caller can ask `LiveMachine.closed(code)` what to do next. With a pinned KE2E key the socket goes to
    /// `wss://host/e2e/ws`: the first message is the sealed request for the live path, the first answer the sealed head
    /// (`{"s": 101}`; anything else is the refusal's status, 4401 then), then every message is one sealed frame with its type byte.
    public func connect(ticket: String, quality: LiveQuality) -> AsyncThrowingStream<LiveEvent, Error> {
        AsyncThrowingStream { cont in
            let pin = api.e2e?.state.pin
            guard let target = LiveSocket.socketURL(baseUrl: api.baseUrl, tabId: tabId, ticket: ticket, quality: quality),
                  let url = pin == nil ? target : LiveSocket.e2eURL(baseUrl: api.baseUrl) else {
                cont.finish(throwing: ApiError(status: 0, detail: "bad address")); return
            }
            var req = URLRequest(url: url)
            let lang = AcceptLanguageValue.current
            if pin == nil {
                if !lang.isEmpty { req.setValue(lang, forHTTPHeaderField: AcceptLanguage.header) }
                req.setValue(Versioning.currentAppHeader, forHTTPHeaderField: Versioning.appHeader)
            }
            let ws = session.webSocketTask(with: req)
            lock.withLock { task = ws; cipher = nil }
            ws.resume()
            let reader = Task {
                var header: FrameHeader?
                do {
                    if let pin {
                        // the opening envelope: the live path (with its ticket) as the inner request
                        var inner = URLRequest(url: target)
                        if !lang.isEmpty { inner.setValue(lang, forHTTPHeaderField: AcceptLanguage.header) }
                        inner.setValue(Versioning.currentAppHeader, forHTTPHeaderField: Versioning.appHeader)
                        let c = try await KE2ESocketHandshake.begin(ws, inner: inner, api: self.api, pin: pin)
                        self.lock.withLock { self.cipher = c }
                    }
                    self.startPings(ws)
                    while !Task.isCancelled {
                        let message = try await ws.receive()
                        let opened: KE2EWebSocketMessage
                        if self.lock.withLock({ self.cipher != nil }) {
                            guard case .data(let d) = message else { throw LiveClosed(code: KE2E.closeBadEnvelope) }
                            opened = try self.lock.withLock { try self.cipher!.open(d) }
                        } else {
                            switch message {
                            case .string(let t): opened = .text(t)
                            case .data(let d): opened = .binary(d)
                            @unknown default: continue
                            }
                        }
                        switch opened {
                        case .text(let text):
                            let msg = LiveMessages.parse(text)
                            if case .frame(let h) = msg { header = h } else { cont.yield(.message(msg)) }
                        case .binary(let data):
                            if let h = header { cont.yield(.frame(h, data)); header = nil }
                        }
                    }
                    cont.finish()
                } catch let closed as LiveClosed {
                    ws.cancel(with: .normalClosure, reason: nil)
                    cont.finish(throwing: closed)
                } catch {
                    let code = ws.closeCode.rawValue
                    // a frame that does not open ends the socket like a broken connection; a server close code wins
                    cont.finish(throwing: LiveClosed(code: code == 0 || code == URLSessionWebSocketTask.CloseCode.invalid.rawValue ? 1006 : code))
                }
            }
            cont.onTermination = { [weak self] _ in
                reader.cancel()
                self?.close()
            }
        }
    }

    /// wss://host/e2e/ws (ws:// for an http server).
    public static func e2eURL(baseUrl: String) -> URL? {
        var base = baseUrl
        if base.hasPrefix("https://") { base = "wss://" + base.dropFirst("https://".count) }
        else if base.hasPrefix("http://") { base = "ws://" + base.dropFirst("http://".count) }
        else { return nil }
        return URL(string: base + KE2E.webSocketPath)
    }

    /// The opening envelope's inner head: GET of the live path and query relative to the server address.
    static func innerHead(_ request: URLRequest, baseUrl: String, clock: KE2EClock) throws -> KE2ERequestHead {
        guard let url = request.url, let c = URLComponents(url: url, resolvingAgainstBaseURL: false) else { throw ApiError(status: 0, detail: "bad address") }
        var path = c.percentEncodedPath
        let prefix = URLComponents(string: baseUrl)?.percentEncodedPath ?? ""
        if !prefix.isEmpty, path.hasPrefix(prefix) { path = String(path.dropFirst(prefix.count)) }
        if let q = c.percentEncodedQuery { path += "?" + q }
        return KE2ERequestHead.make(method: "GET", path: path, headers: request.allHTTPHeaderFields ?? [:], now: clock.now())
    }

    /// Sends one JSON message (input, quality, control); sealed when the socket is KE2E.
    public func send(_ message: JSON) async throws {
        let ws = lock.withLock { task }
        try await ws?.send(try outgoing(.text(message.compactString)))
    }

    private func outgoing(_ m: KE2EWebSocketMessage) throws -> URLSessionWebSocketTask.Message {
        try lock.withLock {
            if cipher != nil { return .data(try cipher!.seal(m)) }
            if case .text(let t) = m { return .string(t) }
            if case .binary(let d) = m { return .data(d) }
            return .string("")
        }
    }

    public func close() {
        let (ws, p) = lock.withLock { () -> (URLSessionWebSocketTask?, Task<Void, Never>?) in
            defer { task = nil; pingTask = nil; cipher = nil }
            return (task, pingTask)
        }
        p?.cancel()
        ws?.cancel(with: .normalClosure, reason: nil)
    }

    private func startPings(_ ws: URLSessionWebSocketTask) {
        let p = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(LiveMachine.pingMs) * 1_000_000)
                if Task.isCancelled { break }
                if let m = try? self.outgoing(.text(LiveMessages.ping())) { try? await ws.send(m) }
            }
        }
        lock.withLock { pingTask?.cancel(); pingTask = p }
    }
}

/// The opening of a WebSocket through KE2E (`/e2e/ws`), shared by the live view and a reply's socket: the first message is the
/// sealed request for the inner path (with the session's headers), the first answer the sealed head (`{"s": 101}`; anything else is
/// the refusal's status). Throws `LiveClosed` (4401 for a refused token, 4404 for another refusal, bad envelope for garbage).
enum KE2ESocketHandshake {
    static func begin(_ ws: URLSessionWebSocketTask, inner: URLRequest, api: KeloApi, pin: KE2EPin) async throws -> KE2EWebSocketCipher {
        let ex = try KE2EExchange.seal(head: try LiveSocket.innerHead(inner, baseUrl: api.baseUrl, clock: api.e2e?.state.clock ?? KE2EClock()),
                                       body: Data(), to: pin.key)
        var c = try ex.webSocketCipher()
        try await ws.send(.data(ex.outerBody))
        guard case .data(let first) = try await ws.receive(), case .text(let headText) = try c.open(first),
              let head = try? KE2EResponseHead.parse(Data(headText.utf8)) else { throw LiveClosed(code: KE2E.closeBadEnvelope) }
        guard head.status == 101 else { throw LiveClosed(code: head.status == 401 ? KE2E.closeUnauthorized : 4404) }
        return c
    }
}

/// The WebSocket closed with this code (4401 ticket, 4404 no tab, 4408 silent, 4429 too many tries, 1000 tab closed, 1006 dropped).
public struct LiveClosed: Error, Equatable, Sendable { public let code: Int }

/// The same frames without a WebSocket (GET /api/browser/sessions/{id}/frame?after=&wait=10).
public struct LiveFrame: Sendable {
    public let seq: Int64, w: Int, h: Int, url: String, title: String, youControl: Bool, controller: String
    public let cssW: Int, cssH: Int
    public let jpeg: Data

    public init(seq: Int64, w: Int, h: Int, url: String, title: String, youControl: Bool, controller: String,
                cssW: Int = 0, cssH: Int = 0, jpeg: Data) {
        self.seq = seq; self.w = w; self.h = h; self.url = url; self.title = title
        self.youControl = youControl; self.controller = controller
        self.cssW = cssW; self.cssH = cssH; self.jpeg = jpeg
    }
}

extension KeloApi {
    /// One frame newer than `after`; nil when nothing newer came within `wait` (204). 404 = the tab is gone (thrown as ApiError).
    public func liveFrame(tabId: String, after: Int64, quality: LiveQuality, wait: Int = 10) async throws -> LiveFrame? {
        let r = try makeRequest("/api/browser/sessions/\(KeloApi.segment(tabId))/frame?after=\(after)&wait=\(wait)&\(quality.query())")
        let (status, data, headers) = try await sendWithHeaders(r)
        if status == 204 { return nil }
        func h(_ k: String) -> String { (headers[k.lowercased()] ?? "").removingPercentEncoding ?? "" }
        return LiveFrame(seq: Int64(h("X-Frame-Seq")) ?? after, w: Int(h("X-Frame-W")) ?? 0, h: Int(h("X-Frame-H")) ?? 0,
                         url: h("X-Frame-Url"),
                         title: h("X-Frame-Title"), youControl: h("X-You-Control") == "true" || h("X-You-Control") == "1",
                         controller: h("X-Controller"),
                         cssW: Int(h("X-Frame-Css-W")) ?? 0,
                         cssH: Int(h("X-Frame-Css-H")) ?? 0,
                         jpeg: data)
    }

    /// A GET whose answer headers matter (the live frame). Errors as for `send`; through KE2E the headers come from the answer head.
    func sendWithHeaders(_ request: URLRequest) async throws -> (Int, Data, [String: String]) {
        let r = try await sendReply(request)
        return (r.status, r.body, r.headers)
    }
}
