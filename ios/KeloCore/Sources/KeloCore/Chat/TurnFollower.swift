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
    /// GET /api/turns/{id}?after=n&wait=20
    func poll(id: String, after: Int) async throws -> TurnPoll
    /// DELETE /api/turns/{id}
    func stop(id: String) async throws
}

public extension TurnApi {
    func start(text: String, timezone: String, answers: [ReplyAnswer]) async throws -> String { try await start(text: text, timezone: timezone) }
    func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?) async throws -> String {
        guard secrets == nil else { throw ApiError(status: ApiError.noResponse, detail: "secrets") }
        return try await start(text: text, timezone: timezone, answers: answers)
    }
    func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?, attachments: [String], autoText: Bool) async throws -> String {
        try await start(text: text, timezone: timezone, answers: answers, secrets: secrets)
    }
}

/// Builds the POST /api/turns body (KeloTurnApi and tests).
public enum TurnBody {
    public static func of(text: String, timezone: String, answers: [ReplyAnswer] = [], secrets: String? = nil, attachments: [String] = [],
                          autoText: Bool = false) -> JSON {
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
    public init(api: KeloApi) { self.api = api }

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
        let body = TurnBody.of(text: text, timezone: timezone, answers: answers, secrets: secrets, attachments: attachments, autoText: autoText)
        return try await api.request("POST", "/api/turns", body: body)["id"]?.text ?? ""
    }

    public func poll(id: String, after: Int) async throws -> TurnPoll {
        TurnPoll.parse(try await api.request("GET", "/api/turns/\(KeloApi.segment(id))?after=\(after)&wait=20"))
    }

    public func stop(id: String) async throws {
        try await api.request("DELETE", "/api/turns/\(KeloApi.segment(id))")
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

    let api: TurnApi
    let sleep: @Sendable (UInt64) async throws -> Void

    public init(api: TurnApi, sleep: @escaping @Sendable (_ millis: UInt64) async throws -> Void = { try await Task.sleep(nanoseconds: $0 * 1_000_000) }) {
        self.api = api
        self.sleep = sleep
    }

    /// Anything that is not an answer from the server (a broken line, an unreadable body) counts as passing.
    public static func isTransient(_ e: Error) -> Bool {
        guard let a = e as? ApiError else { return true }
        return a.isTransient
    }

    public static func backoff(_ failures: Int) -> UInt64 { UInt64(min(500 * failures, 4000)) }

    /// `secrets`: `SecretGuard.allow` only after the owner chose "Send as is" for a message the server held back.
    public func talk(text: String, timezone: String, answers: [ReplyAnswer] = [], secrets: String? = nil, attachments: [String] = [],
                     autoText: Bool = false, onEvent: @Sendable (TurnEvent) async -> Void) async throws {
        let id: String
        if !attachments.isEmpty {
            id = try await api.start(text: text, timezone: timezone, answers: answers, secrets: secrets, attachments: attachments, autoText: autoText)
        } else if secrets != nil { id = try await api.start(text: text, timezone: timezone, answers: answers, secrets: secrets) }
        else if answers.isEmpty { id = try await api.start(text: text, timezone: timezone) }
        else { id = try await api.start(text: text, timezone: timezone, answers: answers) }
        try await follow(id: id, onEvent: onEvent)
    }

    /// Follows the reply `id` until it is over: one started here, or one the server started for this phone (a secure entry saved
    /// after the reply that asked had ended). Cancelling tells the server to stop, as for a reply sent from here.
    public func follow(id: String, onEvent: @Sendable (TurnEvent) async -> Void) async throws {
        do {
            var after = 0
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

extension KeloApi {
    /// POST /api/talk {text, timezone}: the reply as one server-sent-events stream. The app follows replies with `TurnFollower`
    /// (short requests survive tunnels that do not pass streams and phones that sleep); this is for a direct connection where a
    /// stream works, and for tools. Cancelling the iteration closes the stream, which stops the reply on the server.
    public func talkStream(text: String, timezone: String) -> AsyncThrowingStream<TurnEvent, Error> {
        sseEvents(path: "/api/talk", body: ["text": .string(text), "timezone": .string(timezone)])
    }

    /// Any of the server's SSE endpoints (POST with a JSON body), event by event.
    public func sseEvents(path: String, body: JSON) -> AsyncThrowingStream<TurnEvent, Error> {
        AsyncThrowingStream { cont in
            let task = Task {
                do {
                    var r = try makeRequest(path, method: "POST")
                    r.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
                    r.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    r.httpBody = Data(body.compactString.utf8)
                    let (status, headers, bytes): (Int, [String: String], AsyncThrowingStream<UInt8, Error>)
                    do { (status, headers, bytes) = try await transport.streamExchange(r) } catch let e as ApiError { onFailure(e); throw e } catch {
                        if Task.isCancelled { throw CancellationError() }
                        throw ApiError(status: ApiError.noResponse, detail: error.localizedDescription)
                    }
                    if status != 0 { onServerVersion(headers[Versioning.serverHeader.lowercased()]) }
                    if !(200...299).contains(status) {
                        var body = Data()
                        for try await b in bytes { body.append(b); if body.count > 65_536 { break } }
                        throw failure(status, body)
                    }
                    var parser = SSEParser()
                    var chunk: [UInt8] = []
                    for try await b in bytes {
                        chunk.append(b)
                        if b == 0x0A || b == 0x0D {
                            for m in parser.feed(chunk) {
                                guard let json = try? JSON.parse(m.data) else { continue }
                                cont.yield(TurnEvent.parse(json))
                            }
                            chunk.removeAll(keepingCapacity: true)
                        }
                    }
                    _ = parser.feed(chunk)
                    cont.finish()
                } catch {
                    cont.finish(throwing: error)
                }
            }
            cont.onTermination = { _ in task.cancel() }
        }
    }
}
