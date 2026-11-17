import Foundation

/// The real socket of a `VncRelay`: `GET /api/browser/sessions/{id}/vnc?ticket=…&width=…&height=…&density=…` as a WebSocket, opened with
/// the same connection code as the live view. With a pinned KE2E key it goes to `/e2e/ws` (the first message is the sealed request for
/// the vnc path, then every message in both directions is one sealed frame), exactly like `LiveSocket`.
public final class VncSocket: VncTransport, @unchecked Sendable {
    private let task: URLSessionWebSocketTask
    private let lock = NSLock()
    private var cipher: KE2EWebSocketCipher?

    private init(task: URLSessionWebSocketTask, cipher: KE2EWebSocketCipher?) { self.task = task; self.cipher = cipher }

    /// wss://host/api/browser/sessions/{id}/vnc?… (ws:// for an http server).
    public static func socketURL(baseUrl: String, tabId: String, ticket: String, view: VncViewSize) -> URL? {
        var base = baseUrl
        if base.hasPrefix("https://") { base = "wss://" + base.dropFirst("https://".count) }
        else if base.hasPrefix("http://") { base = "ws://" + base.dropFirst("http://".count) }
        else { return nil }
        return URL(string: base + VncRules.path(tabId: tabId, ticket: ticket, view: view))
    }

    /// Opens the socket. Throws when the address is bad or (KE2E) the opening is refused; for a plain socket a refusal shows on the
    /// first `receive`.
    public static func open(api: KeloApi, tabId: String, ticket: String, view: VncViewSize, session: URLSession = .shared) async throws -> VncSocket {
        let pin = api.e2e?.state.pin
        guard let target = socketURL(baseUrl: api.baseUrl, tabId: tabId, ticket: ticket, view: view),
              let url = pin == nil ? target : LiveSocket.e2eURL(baseUrl: api.baseUrl) else { throw ApiError(status: 0, detail: "bad address") }
        var req = URLRequest(url: url)
        let lang = AcceptLanguageValue.current
        if pin == nil {
            if !lang.isEmpty { req.setValue(lang, forHTTPHeaderField: AcceptLanguage.header) }
            req.setValue(Versioning.currentAppHeader, forHTTPHeaderField: Versioning.appHeader)
        }
        let ws = session.webSocketTask(with: req)
        ws.resume()
        var cipher: KE2EWebSocketCipher?
        if let pin {
            var inner = URLRequest(url: target)
            if !lang.isEmpty { inner.setValue(lang, forHTTPHeaderField: AcceptLanguage.header) }
            inner.setValue(Versioning.currentAppHeader, forHTTPHeaderField: Versioning.appHeader)
            do { cipher = try await KE2ESocketHandshake.begin(ws, inner: inner, api: api, pin: pin) } catch {
                ws.cancel(with: .normalClosure, reason: nil)
                throw error
            }
        }
        return VncSocket(task: ws, cipher: cipher)
    }

    public func receive() async throws -> VncInbound {
        let message = try await task.receive()
        let sealed = lock.withLock { cipher != nil }
        if sealed {
            guard case .data(let d) = message else { throw LiveClosed(code: KE2E.closeBadEnvelope) }
            switch try lock.withLock({ try cipher!.open(d) }) {
            case .text(let t): return .text(t)
            case .binary(let b): return .binary(b)
            }
        }
        switch message {
        case .string(let t): return .text(t)
        case .data(let d): return .binary(d)
        @unknown default: return .binary(Data())
        }
    }

    /// The bytes are sealed and queued under one lock so the order on the wire is the order of the calls.
    public func send(_ data: Data) {
        lock.withLock {
            let message: URLSessionWebSocketTask.Message
            if cipher != nil {
                guard let sealed = try? cipher!.seal(.binary(data)) else { task.cancel(with: .normalClosure, reason: nil); return }
                message = .data(sealed)
            } else {
                message = .data(data)
            }
            task.send(message) { _ in }
        }
    }

    public func close() { task.cancel(with: .normalClosure, reason: nil) }
}
