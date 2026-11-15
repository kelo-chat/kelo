import Foundation
import KeloCore
import KeloShared

/// A fake server for the page models: each route ("GET /api/x", path without the query) answers with what its handler returns; a
/// handler may wait (for a `Gate`) so a test can make an older answer arrive after a newer one.
public final class RoutedTransport: HTTPTransport, @unchecked Sendable {
    public struct Call: Equatable, Sendable { public var method: String; public var path: String; public var query: String; public var body: String }
    public typealias Handler = @Sendable (Call, Int) async -> (Int, String)

    private let lock = NSLock()
    private var routes: [String: Handler] = [:]
    private var counts: [String: Int] = [:]
    private var log: [Call] = []
    private var unreachable = false
    /// true: every call fails as if the server could not be reached.
    public var down: Bool { get { lock.withLock { unreachable } } set { lock.withLock { unreachable = newValue } } }

    public init() {}
    public var calls: [Call] { lock.withLock { log } }
    public func count(_ route: String) -> Int { lock.withLock { counts[route] ?? 0 } }

    /// `route` is "METHOD /path"; the handler gets the call and how many calls this route had before it.
    public func on(_ route: String, _ handler: @escaping Handler) { lock.withLock { routes[route] = handler } }
    public func on(_ route: String, _ body: String) { on(route) { _, _ in (200, body) } }
    public func on(_ route: String, _ status: Int, _ body: String) { on(route) { _, _ in (status, body) } }

    public func send(_ request: URLRequest) async throws -> (Int, Data) {
        if down { throw URLError(.notConnectedToInternet) }
        let url = request.url
        let call = Call(method: request.httpMethod ?? "GET", path: url?.path ?? "", query: url?.query ?? "",
                        body: request.httpBody.map { String(decoding: $0, as: UTF8.self) } ?? "")
        let key = "\(call.method) \(call.path)"
        let (handler, n): (Handler?, Int) = lock.withLock {
            log.append(call)
            let n = counts[key] ?? 0
            counts[key] = n + 1
            return (routes[key], n)
        }
        guard let handler else { return (404, Data(#"{"detail":"no route"}"#.utf8)) }
        let (status, body) = await handler(call, n)
        return (status, Data(body.utf8))
    }

    public func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) { throw URLError(.unsupportedURL) }

    public func api() -> KeloApi {
        KeloApi(session: Session(serverUrl: "https://kelo.test", token: "tok"), transport: self, onUnauthorized: {})
    }
}

/// Holds an answer back until the test lets it go.
public actor Gate {
    public init() {}
    private var open = false
    private var waiting: [CheckedContinuation<Void, Never>] = []
    public func wait() async { if open { return }; await withCheckedContinuation { waiting.append($0) } }
    public func release() { open = true; waiting.forEach { $0.resume() }; waiting = [] }
}

/// Waits (up to ~2 s) for a condition a model reaches by itself.
@MainActor public func eventually(_ what: String = "", _ cond: () -> Bool) async -> Bool {
    for _ in 0..<200 { if cond() { return true }; try? await Task.sleep(nanoseconds: 10_000_000) }
    return cond()
}


public func memoryCache() -> ScreenCache { ScreenCache(directory: nil, key: { nil }) }

/// A profile host that keeps what the pages hand it.
@MainActor public final class FakeProfileHost: ProfileHost {
    public var profile: Profile?
    public var avatar: (version: Int64, hasData: Bool)?
    public init(_ profile: Profile? = Profile(agentName: "Kelo", tone: "polite")) { self.profile = profile }
    public func setProfile(_ p: Profile) { profile = p }
    public func avatarChanged(_ image: PlatformImage?, version: Int64, data: Data?) { avatar = (version, data != nil) }
}

/// A session kept in memory only (tests and previews).
public final class MemorySessionStore: SessionStore, @unchecked Sendable {
    private var session: Session?
    public init(_ session: Session? = nil) { self.session = session }
    public func load() -> Session? { session }
    public func save(_ session: Session?) { self.session = session }
}
