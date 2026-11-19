import Foundation

/// The three calls a reply needs; `KeloTurnApi` is the real one, tests use a fake.
public protocol TurnApi: Sendable {
    /// POST /api/turns {text, timezone} -> id
    func start(text: String, timezone: String) async throws -> String
    /// The same with "answers": the quick-reply fields `text` answers (their plain forms; the server checks them).
    func start(text: String, timezone: String, answers: [ReplyAnswer]) async throws -> String
    /// The same with "secrets": "allow" (the owner chose to send a message the server held back for a secret; `SecretGuard`).
    func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?) async throws -> String
    /// The same with the files sent with it ("attachments": "uploads/<name>" paths, kept with the message by the server) and
    /// "auto_text": the text is the app's own sentence (nothing was typed), so the bubbles show only the files.
    func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?, attachments: [String], autoText: Bool) async throws -> String
    /// The same with "call": the call id when the message comes from a voice call in the Kelo app.
    func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?, attachments: [String], autoText: Bool, call: String?) async throws -> String
    /// GET /api/turns/{id}?after=n&wait=20
    func poll(id: String, after: Int) async throws -> TurnPoll
    /// DELETE /api/turns/{id}
    func stop(id: String) async throws
    /// Follows reply `id` over its WebSocket from event `after`: `onEvent` gets (seq, event) in order (it may throw to end the socket;
    /// that error comes out). Answers how it ended. The default (fakes, older code) says the socket is not available, so the
    /// follower polls.
    func socket(id: String, after: Int, onEvent: @Sendable (Int, TurnEvent) async throws -> Void) async throws -> SocketEnd
}

/// How a reply's WebSocket ended.
public enum SocketEnd: Equatable, Sendable {
    /// The reply is over; `next` = the count of its events.
    case done(next: Int)
    /// The server does not know the reply (GET would answer 404).
    case gone
    /// Closed or failed before the reply was over (no signal, a proxy cut it, 4401 ...).
    case broken
    /// No socket here (a fake, or the route refused the upgrade at once).
    case unavailable
}

/// Whether sockets are tried at all: one per app process. After a route did not let them through they are skipped for `blockMs`.
public enum SocketGate {
    public static let blockMs: Int64 = 10 * 60_000
    private static let until = Box(0)
    public static func isOpen(_ now: Int64) -> Bool { now >= until.value }
    public static func block(_ now: Int64) { until.value = now + blockMs }
    static func reset() { until.value = 0 }

    private final class Box: @unchecked Sendable {
        private let lock = NSLock()
        private var v: Int64
        init(_ v: Int64) { self.v = v }
        var value: Int64 { get { lock.withLock { v } } set { lock.withLock { v = newValue } } }
    }
}

public extension TurnApi {
    func socket(id: String, after: Int, onEvent: @Sendable (Int, TurnEvent) async throws -> Void) async throws -> SocketEnd { .unavailable }
    func start(text: String, timezone: String, answers: [ReplyAnswer]) async throws -> String { try await start(text: text, timezone: timezone) }
    func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?) async throws -> String {
        guard secrets == nil else { throw ApiError(status: ApiError.noResponse, detail: "secrets") }
        return try await start(text: text, timezone: timezone, answers: answers)
    }
    func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?, attachments: [String], autoText: Bool) async throws -> String {
        try await start(text: text, timezone: timezone, answers: answers, secrets: secrets, attachments: attachments, autoText: autoText, call: nil)
    }
    func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?, attachments: [String], autoText: Bool, call: String?) async throws -> String {
        try await start(text: text, timezone: timezone, answers: answers, secrets: secrets, attachments: attachments, autoText: autoText)
    }
}

/// Builds the POST /api/turns body (KeloTurnApi and tests).
public enum TurnBody {
    public static func of(text: String, timezone: String, answers: [ReplyAnswer] = [], secrets: String? = nil, attachments: [String] = [],
                          autoText: Bool = false, call: String? = nil, canCall: Bool = false, amend: Bool = false) -> JSON {
        // "ai_consent": held back (428) until someone agreed on this server (AiConsent)
        var body: [String: JSON] = ["text": .string(text), "timezone": .string(timezone), "ai_consent": .string(AiConsent.check)]
        if let secrets { body["secrets"] = .string(secrets) }
        if !attachments.isEmpty {
            body["attachments"] = .array(attachments.map { .object(["path": .string($0)]) })
            if autoText { body["auto_text"] = .bool(true) }
        }
        if !answers.isEmpty {
            body["answers"] = .array(answers.map { .object(["question": .number(Double($0.question)), "value": .string($0.value)]) })
        }
        if let call { body["call"] = .string(call) }
        if amend, call != nil { body["amend"] = .bool(true) }   // replaces the call's unanswered words (server calls.py)
        body["can_call"] = .bool(canCall)
        return .object(body)
    }
}

/// One answered quick-reply question sent with the chat message (POST /api/turns "answers"): its place and the answer's plain form.
public struct ReplyAnswer: Equatable, Sendable {
    public var question: Int
    public var value: String
    public init(_ question: Int, _ value: String) { self.question = question; self.value = value }
}

public struct KeloTurnApi: TurnApi {
    public let api: KeloApi
    public let canCall: @Sendable () -> Bool
    /// The call's words replace its unanswered words (POST /api/turns "amend", with `call`).
    public let amend: Bool

    public init(api: KeloApi, canCall: @escaping @Sendable () -> Bool = { false }, amend: Bool = false) {
        self.api = api
        self.canCall = canCall
        self.amend = amend
    }

    public func start(text: String, timezone: String) async throws -> String {
        try await start(text: text, timezone: timezone, answers: [])
    }

    public func start(text: String, timezone: String, answers: [ReplyAnswer]) async throws -> String {
        try await start(text: text, timezone: timezone, answers: answers, secrets: nil)
    }

    public func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?) async throws -> String {
        try await start(text: text, timezone: timezone, answers: answers, secrets: secrets, attachments: [], autoText: false)
    }

    public func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?, attachments: [String], autoText: Bool) async throws -> String {
        try await start(text: text, timezone: timezone, answers: answers, secrets: secrets, attachments: attachments, autoText: autoText, call: nil)
    }

    public func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?, attachments: [String], autoText: Bool, call: String?) async throws -> String {
        let body = TurnBody.of(text: text, timezone: timezone, answers: answers, secrets: secrets, attachments: attachments, autoText: autoText, call: call, canCall: canCall(), amend: amend)
        return try await api.request("POST", "/api/turns", body: body)["id"]?.text ?? ""
    }

    public func poll(id: String, after: Int) async throws -> TurnPoll {
        TurnPoll.parse(try await api.request("GET", "/api/turns/\(KeloApi.segment(id))?after=\(after)&wait=20"))
    }

    public func stop(id: String) async throws {
        try await api.request("DELETE", "/api/turns/\(KeloApi.segment(id))")
    }

    public func socket(id: String, after: Int, onEvent: @Sendable (Int, TurnEvent) async throws -> Void) async throws -> SocketEnd {
        try await TurnSocket(api: api).run(id: id, after: after, onEvent: onEvent)
    }
}

/// Start a reply, then follow it with short requests instead of one long stream (Android chat/TurnFollower.kt). This is the app's
/// main path: quick tunnels and sleeping phones drop streams, and a dropped request is simply made again from the same place, so
/// nothing is lost while the reply keeps running on the server. Returns when the reply is over.
///
/// Cancelling the calling Task is the Stop button (or leaving the screen): the server is told to stop with DELETE /api/turns/{id}.
public struct TurnFollower: Sendable {
    /// Failed requests in a row (about two minutes) before the connection counts as lost.
    public static let giveUpAfter = 30
    static let stopTimeoutNanos: UInt64 = 10_000_000_000

    /// Failed socket attempts in a row (without a new event) before the follower polls instead.
    public static let socketTries = 3

    let api: TurnApi
    let sleep: @Sendable (UInt64) async throws -> Void
    let clock: @Sendable () -> Int64

    public init(api: TurnApi, clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
                sleep: @escaping @Sendable (_ millis: UInt64) async throws -> Void = { try await Task.sleep(nanoseconds: $0 * 1_000_000) }) {
        self.api = api
        self.clock = clock
        self.sleep = sleep
    }

    private struct SocketGap: Error {}
    private final class Position: @unchecked Sendable {
        private let lock = NSLock()
        private var v = 0
        var after: Int { lock.withLock { v } }
        func advance() { lock.withLock { v += 1 } }
    }

    /// Anything that is not an answer from the server (a broken line, an unreadable body) counts as passing.
    public static func isTransient(_ e: Error) -> Bool {
        guard let a = e as? ApiError else { return true }
        return a.isTransient
    }

    public static func backoff(_ failures: Int) -> UInt64 { UInt64(min(500 * failures, 4000)) }

    /// `secrets`: `SecretGuard.allow` only after the owner chose "Send as is" for a message the server held back.
    public func talk(text: String, timezone: String, answers: [ReplyAnswer] = [], secrets: String? = nil, attachments: [String] = [],
                     autoText: Bool = false, call: String? = nil, onId: @Sendable (String) async -> Void = { _ in },
                     onEvent: @Sendable (TurnEvent) async -> Void) async throws {
        let id: String
        if call != nil {
            id = try await api.start(text: text, timezone: timezone, answers: answers, secrets: secrets, attachments: attachments, autoText: autoText, call: call)
        } else if !attachments.isEmpty {
            id = try await api.start(text: text, timezone: timezone, answers: answers, secrets: secrets, attachments: attachments, autoText: autoText)
        } else if secrets != nil { id = try await api.start(text: text, timezone: timezone, answers: answers, secrets: secrets) }
        else if answers.isEmpty { id = try await api.start(text: text, timezone: timezone) }
        else { id = try await api.start(text: text, timezone: timezone, answers: answers) }
        await onId(id)
        try await follow(id: id, onEvent: onEvent)
    }

    /// Follows the reply `id` until it is over: one started here, or one the server started for this phone (a secure entry saved
    /// after the reply that asked had ended). Cancelling tells the server to stop, as for a reply sent from here.
    public func follow(id: String, onEvent: @Sendable (TurnEvent) async -> Void) async throws {
        do {
            let position = Position()
            if SocketGate.isOpen(clock()) {
                var tries = 0
                while tries < TurnFollower.socketTries {
                    try Task.checkCancellation()
                    let before = position.after
                    let end: SocketEnd
                    do {
                        end = try await api.socket(id: id, after: before) { seq, ev in
                            let now = position.after
                            if seq == now { await onEvent(ev); position.advance() } else if seq > now { throw SocketGap() }
                        }
                    } catch {
                        if error is CancellationError || Task.isCancelled { throw CancellationError() }
                        end = .broken
                    }
                    switch end {
                    case .done: return
                    case .gone: throw ApiError(status: 404, detail: "that reply is gone (it was stopped, or finished a while ago)")
                    case .unavailable: tries = -1
                    case .broken:
                        tries = position.after > before ? 1 : tries + 1   // progress resets the count
                        if tries == 2 { await onEvent(.reconnecting) }
                        try await sleep(TurnFollower.backoff(tries))
                    }
                    if tries < 0 { break }
                }
                if tries >= TurnFollower.socketTries { SocketGate.block(clock()) }   // sockets do not get through here: poll for a while
            }
            try await pollFrom(id: id, after: position.after, onEvent: onEvent)
        } catch is CancellationError {
            // tell the server, without letting the cancelled task cut the request short; give up after 10 s
            let api = self.api
            let stop = Task.detached { try? await api.stop(id: id) }
            let timer = Task.detached { try? await Task.sleep(nanoseconds: TurnFollower.stopTimeoutNanos); stop.cancel() }
            _ = await stop.value
            timer.cancel()
            throw CancellationError()
        }
    }
}

extension TurnFollower {
    /// The long-poll loop (GET /api/turns/{id}?after=n&wait=20), from event `after`.
    fileprivate func pollFrom(id: String, after start: Int, onEvent: @Sendable (TurnEvent) async -> Void) async throws {
        var after = start
        var failures = 0
        while true {
            try Task.checkCancellation()
            let r: TurnPoll
            do {
                r = try await api.poll(id: id, after: after)
            } catch {
                if error is CancellationError || Task.isCancelled { throw CancellationError() }
                failures += 1
                if !TurnFollower.isTransient(error) || failures >= TurnFollower.giveUpAfter { throw error }
                if failures == 2 { await onEvent(.reconnecting) }
                try await sleep(TurnFollower.backoff(failures))
                continue
            }
            failures = 0
            for e in r.events { await onEvent(e) }
            after = r.next
            if r.done { return }
        }
    }
}

/// A reply's WebSocket (`GET /api/turns/{id}/ws?after=N`, bearer header; through a pinned KE2E key `/e2e/ws` with the sealed first
/// message, then every message sealed: `KE2ESocketHandshake`, the same as the live view).
struct TurnSocket {
    let api: KeloApi
    var session: URLSession = .shared
    private struct FromCallback: Error { let inner: Error }

    func run(id: String, after: Int, onEvent: @Sendable (Int, TurnEvent) async throws -> Void) async throws -> SocketEnd {
        let path = "/api/turns/\(KeloApi.segment(id))/ws?after=\(after)"
        let pin = api.e2e?.state.pin
        guard var inner = try? api.makeRequest(path), let target = inner.url.flatMap({ TurnSocket.socketURL($0) }) else { return .unavailable }
        var req = inner   // the bearer header stays on a plain socket
        if pin == nil { req.url = target }
        else if let e2e = LiveSocket.e2eURL(baseUrl: api.baseUrl) { req = URLRequest(url: e2e) }
        else { return .unavailable }
        inner.httpMethod = "GET"
        let ws = session.webSocketTask(with: req)
        ws.resume()
        defer { ws.cancel(with: .normalClosure, reason: nil) }
        var cipher: KE2EWebSocketCipher?
        do {
            if let pin { cipher = try await KE2ESocketHandshake.begin(ws, inner: inner, api: api, pin: pin) }
            while true {
                let message = try await ws.receive()
                let text: String
                if cipher != nil {
                    guard case .data(let d) = message, case .text(let t) = try cipher!.open(d) else { return .broken }
                    text = t
                } else {
                    guard case .string(let t) = message else { continue }
                    text = t
                }
                guard let o = (try? JSON.parse(text)).map(Lenient.init) else { continue }
                if let seq = o["seq"]?.intValue, let event = o["event"] {
                    do { try await onEvent(seq, TurnEvent.parse(event)) } catch { throw FromCallback(inner: error) }
                } else if o.optBool("done") == true {
                    return .done(next: o.int("next"))
                } else if o["type"]?.text == "gone" {
                    return .gone
                }   // a ping: nothing
            }
        } catch let f as FromCallback {
            throw f.inner   // the caller ended the socket (a gap)
        } catch let closed as LiveClosed {
            return closed.code == 4404 ? .unavailable : .broken
        } catch {
            if error is CancellationError || Task.isCancelled { throw CancellationError() }
            // the upgrade was refused (404 from an older server, 403/502 from a proxy): no socket here
            if let r = ws.response as? HTTPURLResponse, r.statusCode != 101 { return .unavailable }
            return .broken
        }
    }

    /// wss://host/path (ws:// for an http server).
    static func socketURL(_ u: URL) -> URL? {
        var s = u.absoluteString
        if s.hasPrefix("https://") { s = "wss://" + s.dropFirst("https://".count) }
        else if s.hasPrefix("http://") { s = "ws://" + s.dropFirst("http://".count) }
        else { return nil }
        return URL(string: s)
    }
}
