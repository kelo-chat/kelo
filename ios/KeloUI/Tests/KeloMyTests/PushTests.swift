import Testing
import Foundation
import KeloCore
import KeloShared
@testable import KeloMy

/// Records every request and answers {"ok":true,...}.
private final class PushTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var log: [(method: String, path: String, body: String)] = []
    var requests: [(method: String, path: String, body: String)] { lock.withLock { log } }
    var status = 200

    func send(_ request: URLRequest) async throws -> (Int, Data) {
        let body = request.httpBody.map { String(decoding: $0, as: UTF8.self) } ?? ""
        lock.withLock { log.append((request.httpMethod ?? "", request.url?.path ?? "", body)) }
        return (status, Data(#"{"ok":true,"push":true,"removed":true}"#.utf8))
    }
    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) { throw URLError(.unsupportedURL) }
}

/// Remote notifications: the token as the server wants it, registering and taking it back, and where a tapped push opens.
@MainActor @Suite(.serialized) struct PushTests {
    private func api(_ t: PushTransport) -> KeloApi {
        KeloApi(session: Session(serverUrl: "https://kelo.example", token: "t"), transport: t, onUnauthorized: {})
    }
    private func prefs() -> Prefs { Prefs(defaults: UserDefaults(suiteName: "push-tests-\(UUID().uuidString)")!) }

    @Test func theTokenGoesToTheServerAsLowercaseHex() {
        #expect(PushRules.hex(Data([0x00, 0xAB, 0x0F, 0xFF])) == "00ab0fff")
        #expect(PushRules.hex(Data(repeating: 0xA1, count: 32)) == String(repeating: "a1", count: 32))
        #expect(PushRules.platform == "ios-apns")
    }

    @Test func registeringPostsTheTokenThePlatformAndTheEnvironmentAndANewTokenReplacesTheOld() async throws {
        let t = PushTransport()
        let p = prefs()
        let r = PushRegistrar(prefs: p)
        #expect(!r.registered)
        let ok = await r.received(token: Data(repeating: 0xA1, count: 32), api: api(t), environment: "development")
        #expect(ok && r.registered)
        let ok2 = await r.received(token: Data(repeating: 0xB2, count: 32), api: api(t), environment: "production")   // token refresh
        #expect(ok2)
        let posts = t.requests.filter { $0.method == "POST" }
        #expect(posts.count == 2 && posts.allSatisfy { $0.path == "/api/push/register" })
        let first = try #require(try JSON.parse(Data(posts[0].body.utf8)))
        #expect(first["token"]?.stringValue == String(repeating: "a1", count: 32))
        #expect(first["platform"]?.stringValue == "ios-apns" && first["environment"]?.stringValue == "development")
        #expect(try JSON.parse(Data(posts[1].body.utf8))["environment"]?.stringValue == "production")
    }

    @Test func nothingIsSentWithoutASessionOrWhenTheServerRefuses() async {
        let t = PushTransport()
        let r = PushRegistrar(prefs: prefs())
        #expect(await r.received(token: Data(repeating: 1, count: 32), api: nil) == false)
        #expect(await r.received(token: Data(), api: api(t)) == false)
        t.status = 422
        #expect(await r.received(token: Data(repeating: 1, count: 32), api: api(t), environment: "production") == false)
        #expect(!r.registered)
    }

    @Test func unregisteringDeletesTheTokenAndForgetsItLocally() async {
        let t = PushTransport()
        let r = PushRegistrar(prefs: prefs())
        _ = await r.received(token: Data(repeating: 1, count: 32), api: api(t), environment: "production")
        await r.unregister(api: api(t))
        #expect(!r.registered)
        #expect(t.requests.last?.method == "DELETE" && t.requests.last?.path == "/api/push/register")
        await r.unregister(api: nil)   // nothing to call, nothing breaks
    }

    @Test func signingOutTakesTheTokenBackWhileTheSessionStillWorks() async throws {
        let store = MemorySessionStore(Session(serverUrl: "https://kelo.example", token: "t"))
        let t = PushTransport()
        let app = AppModel(store: store, transport: t)
        var calls = 0
        app.onSignOut = { api in calls += 1; Task { await PushRegistrar(prefs: Prefs(defaults: UserDefaults(suiteName: "x-\(UUID().uuidString)")!)).unregister(api: api) } }
        app.signOut()
        #expect(calls == 1 && app.api == nil)
        app.signOut()   // already signed out: no second call
        #expect(calls == 1)
        for _ in 0..<100 where !t.requests.contains(where: { $0.method == "DELETE" }) { try await Task.sleep(nanoseconds: 10_000_000) }
        #expect(t.requests.contains { $0.method == "DELETE" && $0.path == "/api/push/register" })
    }

    @Test func theEnvironmentComesFromTheProfileEmbeddedInTheBuild() {
        func profile(_ env: String) -> Data { Data("\u{1}junk<key>Entitlements</key><dict><key>aps-environment</key>\n\t<string>\(env)</string></dict>\u{2}".utf8) }
        #expect(PushRules.environment(provision: profile("development")) == "development")
        #expect(PushRules.environment(provision: profile("production")) == "production")
        #expect(PushRules.environment(provision: nil) == "production")                 // TestFlight and the App Store: no profile
        #expect(PushRules.environment(provision: Data("no entitlement here".utf8)) == "production")
    }

    @Test func aPushIsRecognisedByItsKindAndIdsOnly() {
        let info: [AnyHashable: Any] = ["aps": ["alert": ["loc-key": "kelo_push_update"]], "type": "task_done", "id": "41", "task_id": "9"]
        #expect(PushRules.payload(info) == PushPayload(type: "task_done", id: 41, taskId: 9))
        #expect(PushRules.payload(["type": "hello", "id": "1"]) == nil)
        #expect(PushRules.payload(["type": "task_done"]) == nil)
        #expect(PushRules.payload(["open": "chat"]) == nil)
        #expect(PushRules.isBarePush(userInfo: info))
        #expect(!PushRules.isBarePush(userInfo: ["open": "task:9", "type": "task_done", "id": "41"]))   // a local notification
    }

    @Test func aTappedPushOpensWhereTheLocalNotificationOfThatKindOpens() {
        func open(_ type: String, _ extra: [String: String] = [:]) -> String? {
            var info: [AnyHashable: Any] = ["type": type, "id": "7"]
            for (k, v) in extra { info[k] = v }
            return PushRules.openText(userInfo: info)
        }
        #expect(open("task_done", ["task_id": "9"]) == "task:9")
        #expect(open("task_failed", ["task_id": "9"]) == "task:9")
        #expect(open("approval_waiting", ["approval_id": "ab-1", "task_id": "9"]) == "chat")
        #expect(open("payment_auto") == "wallet")
        #expect(open("schedule_disabled", ["schedule_id": "3"]) == "schedule:3")
        #expect(open("schedule_paused") == "schedules")
        #expect(open("watch_fired") == "watchers")
        #expect(open("proactive") == "chat" && open("followup") == "chat")
        #expect(open("storage") == "storage")
        // a local notification keeps its own text
        #expect(PushRules.openText(userInfo: ["open": "chat:5", "type": "followup", "id": "7"]) == "chat:5")
        #expect(PushRules.openText(userInfo: ["something": "else"]) == nil)
        for text in ["task:9", "wallet", "schedule:3", "watchers", "storage", "chat"] { #expect(OpenTarget.decode(text) != nil) }
    }
}
