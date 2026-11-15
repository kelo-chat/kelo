import Testing
import Foundation
import CryptoKit
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

/// What the model server answers, switchable while a test runs.
private final class ModelServer: @unchecked Sendable {
    private let lock = NSLock()
    private var failing = false
    private var listBody = #"{"provider":"antigravity-oauth","models":["gemini-3.8-flash-medium","claude-sonnet-4-6"],"labels":{"gemini-3.8-flash-medium":"Gemini 3.8 Flash (Medium)","claude-sonnet-4-6":"Claude Sonnet 4.6 (Thinking)"},"info":{"claude-sonnet-4-6":{"images":true,"effort_levels":["low","high"]}},"error":null,"stale":false,"fetched_at":1}"#
    var fail: Bool {
        get { lock.withLock { failing } }
        set { lock.withLock { failing = newValue } }
    }

    func route(_ method: String, _ path: String) -> (Int, String) {
        if path.hasPrefix("/api/profile") {
            return (200, #"{"agent_name":"Kelo","provider":"antigravity-oauth","model":"gemini-3.8-flash-medium"}"#)
        }
        if path.hasPrefix("/api/models") {
            if fail { return (200, #"{"provider":"antigravity-oauth","models":[],"labels":{},"info":{},"error":"agy models timed out"}"#) }
            return (200, lock.withLock { listBody })
        }
        if path.hasPrefix("/api/providers") {
            if fail { return (503, #"{"detail":"busy"}"#) }
            return (200, #"[{"name":"antigravity-oauth","display_name":"Antigravity","configured":true,"signed_in":true,"account":"me@example.com","signin":true,"effort_levels":[],"cli":{"installed":true,"logged_in":true,"checked_at":1700000000.5}}]"#)
        }
        return (200, "[]")
    }
}

@MainActor
private func makeApp(_ server: ModelServer, cache: ScreenCache) -> (AppModel, RouteTransport) {
    let t = RouteTransport { method, path, _ in server.route(method, path) }
    let defaults = UserDefaults(suiteName: "kelo-catalog-test-\(UUID().uuidString)")!
    let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")), prefs: Prefs(defaults: defaults),
                       strings: Strings(code: "en"), transport: t, cache: cache)
    return (app, t)
}

private func until(_ cond: @MainActor () -> Bool) async {
    for _ in 0..<300 {
        if await cond() { return }
        try? await Task.sleep(nanoseconds: 10_000_000)
    }
}

/// The kept model lists in the chat: the chip, the chooser and the sheet show the last good list at once (Android ModelCatalogTest).
@Suite(.serialized) struct ChatModelCatalogTests {
    private func diskCache() -> (URL, () -> ScreenCache) {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("kelo-chat-catalog-\(UUID().uuidString)")
        let key = SymmetricKey(size: .bits256)
        return (dir, { ScreenCache(directory: dir, key: { key }) })
    }

    @Test func theChatsFormKeepsEachModelsFacts() throws {
        let l = ModelList(provider: "p", models: ["a"], labels: ["a": "A"], info: ["a": ModelEffort(images: true, effortLevels: [])])
        #expect(ModelCatalog.keepable(l))
        #expect(ModelCatalog.placeholder(provider: "p", model: "sonnet", as: ModelList.self) == ModelList(provider: "p", models: ["sonnet"]))
        #expect(ModelCatalog.shown(ModelList.catalogFailure(provider: "p", error: "x"), l)?.stale == true)
        #expect(ModelCatalog.shown(nil, l) == l)
    }

    @MainActor @Test func fetchKeepsAGoodAnswerAndAFailureKeepsTheLastGoodOne() async throws {
        let (dir, newCache) = diskCache()
        defer { try? FileManager.default.removeItem(at: dir) }
        let server = ModelServer()
        let (app, t) = makeApp(server, cache: newCache())
        let api = try app.requireApi()

        let got = try await ModelCatalog.fetch(api, app.cache, provider: "antigravity-oauth", as: ModelList.self)
        #expect(got.labels["gemini-3.8-flash-medium"] == "Gemini 3.8 Flash (Medium)")
        #expect(t.requests.contains { $0.url?.query?.contains("provider=antigravity-oauth") == true })
        server.fail = true
        let failed = try await ModelCatalog.fetch(api, app.cache, provider: "antigravity-oauth", as: ModelList.self)
        #expect(failed.error != nil)

        // the next launch: the last good list, with its labels and facts (the chat's form), and 마이's form from the same copy
        let next = newCache()
        next.use(server: "https://kelo.test")
        let kept = try #require(ModelCatalog.keptList(next, "antigravity-oauth", as: ModelList.self))
        #expect(kept.models == ["gemini-3.8-flash-medium", "claude-sonnet-4-6"])
        #expect(kept.info["claude-sonnet-4-6"]?.images == true)
        #expect(kept.info["claude-sonnet-4-6"]?.effortLevels == ["low", "high"])
        #expect(ModelCatalog.keptList(next, "antigravity-oauth", as: ModelList.self)?.labels["claude-sonnet-4-6"] == "Claude Sonnet 4.6 (Thinking)")
        #expect(ModelCatalog.shown(failed, kept)?.labels["gemini-3.8-flash-medium"] == "Gemini 3.8 Flash (Medium)")
        #expect(ModelCatalog.shown(failed, kept)?.stale == true)

        // the providers: a good answer is kept (both forms read it), a failure keeps it
        server.fail = false
        let rows = try await ModelCatalog.fetchModelProviders(api, app.cache)
        #expect(rows.single?.displayName == "Antigravity")
        server.fail = true
        _ = try? await ModelCatalog.fetchModelProviders(api, app.cache)
        let again = newCache()
        again.use(server: "https://kelo.test")
        let keptRows = ModelCatalog.keptModelProviders(again)
        #expect(keptRows == rows)
        #expect(keptRows.single?.account == "me@example.com")   // what only the chat's rows know is not lost
    }

    @MainActor @Test func theChatReadsProvidersKeptByAnEarlierVersion() {
        let cache = ScreenCache(directory: nil, key: { nil })
        cache.use(server: "https://kelo.test")
        cache.put(ModelCatalog.legacyChatProvidersSlot, [ModelProvider(name: "openai", displayName: "OpenAI")])
        #expect(ModelCatalog.keptModelProviders(cache).map(\.name) == ["openai"])
        ModelCatalog.keepProviders(cache, [["name": "anthropic", "display_name": "Anthropic"]])
        #expect(ModelCatalog.keptModelProviders(cache).map(\.name) == ["anthropic"])
    }

    @MainActor @Test func theChipShowsTheKeptNameAtOnceAndKeepsItWhenTheRefreshFails() async throws {
        let (dir, newCache) = diskCache()
        defer { try? FileManager.default.removeItem(at: dir) }
        let server = ModelServer()
        // an earlier run kept the list and the providers
        do {
            let (app, _) = makeApp(server, cache: newCache())
            _ = try await ModelCatalog.fetch(try app.requireApi(), app.cache, provider: "antigravity-oauth", as: ModelList.self)
            _ = try await ModelCatalog.fetchModelProviders(try app.requireApi(), app.cache)
        }
        server.fail = true
        let (app, _) = makeApp(server, cache: newCache())
        let m = ChatModel(app: app, uploader: FakeUploader())
        #expect(m.providers.map(\.name) == ["antigravity-oauth"])   // the last provider rows, before anything is fetched
        m.loadModelList(provider: "antigravity-oauth")
        // at once, before the server answers: the kept list, so the chip already reads as the server's label
        #expect(m.modelList?.labels["gemini-3.8-flash-medium"] == "Gemini 3.8 Flash (Medium)")
        let profile = Profile(agentName: "Kelo", provider: "antigravity-oauth", model: "gemini-3.8-flash-medium")
        #expect(ChatWords.chip(profile, providers: m.providers, list: m.modelList) == "Gemini 3.8 Flash (Medium)")
        // the refresh fails: the kept list stays, marked stale; never blank
        await until { m.modelList?.stale == true }
        #expect(m.modelList?.stale == true)
        #expect(m.modelList?.models.count == 2)
        #expect(ChatWords.chip(profile, providers: m.providers, list: m.modelList) == "Gemini 3.8 Flash (Medium)")
    }

    @MainActor @Test func withNothingKeptTheDerivedNameAlreadyReadsLikeTheLabel() async throws {
        let server = ModelServer()
        let (app, _) = makeApp(server, cache: ScreenCache(directory: nil, key: { nil }))
        let m = ChatModel(app: app, uploader: FakeUploader())
        let profile = Profile(agentName: "Kelo", provider: "antigravity-oauth", model: "gemini-3.8-flash-medium")
        let before = ChatWords.chip(profile, providers: [], list: nil)
        m.loadModelList(provider: "antigravity-oauth")
        await until { m.modelList != nil }
        #expect(m.modelList?.labels["gemini-3.8-flash-medium"] == "Gemini 3.8 Flash (Medium)")
        #expect(ChatWords.chip(profile, providers: [], list: m.modelList) == before)   // the chip's wording does not change
    }
}

private extension Array {
    var single: Element? { count == 1 ? self[0] : nil }
}
