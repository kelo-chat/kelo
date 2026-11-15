import Foundation
import Testing
@testable import KeloCore

/// The server's 409 `secret_in_message` answer and the offsets it gives (UTF-16 code units; Android SecretGuardTest).
@Suite struct SecretGuardTests {
    private func detail(_ s: String) -> [String: JSON]? { (try? JSON.parse(s))?.objectValue }
    private let key = "sk-ant-api03-" + String(repeating: "A", count: 40)

    /// UTF-16 offset of `part` in `text`.
    private func at(_ part: String, in text: String) -> Int {
        let r = text.range(of: part)!
        return text.utf16.distance(from: text.utf16.startIndex, to: r.lowerBound)
    }

    @Test func parsesTheAnswer() {
        let f = SecretGuard.parse(detail(#"{"code":"secret_in_message","spans":[{"start":12,"end":63,"kind":"api_key"}],"placeholder":"[hidden key]","target":{"type":"provider","id":"anthropic"},"label":" Anthropic "}"#))
        #expect(f?.spans == [SecretSpan(12, 63, "api_key")])
        #expect(f?.placeholder == "[hidden key]")
        #expect(f?.target == SecretTarget("provider", "anthropic"))
        #expect(f?.label == "Anthropic")
    }

    @Test func otherAnswersAndOddFieldsAreNotASecretSheet() {
        #expect(SecretGuard.parse(nil) == nil)
        #expect(SecretGuard.parse(detail(#"{"code":"something_else"}"#)) == nil)
        #expect(SecretGuard.of(ApiError(status: 409, detail: "conflict")) == nil)
        #expect(SecretGuard.of(ApiError(status: 400, detail: "x", detailJson: detail(#"{"code":"secret_in_message"}"#))) == nil)
        let f = SecretGuard.of(ApiError(status: 409, detail: "HTTP 409", detailJson: detail(
            #"{"code":"secret_in_message","spans":[{"start":5,"end":2},{"start":"x"},{"start":30,"end":40}],"target":{"type":"spaceship"}}"#)))
        #expect(f?.spans == [SecretSpan(30, 40, "token")])   // bad spans dropped, a missing kind is a token
        #expect(f?.target == SecretTarget.keychain)          // an unknown target is the Keychain
        #expect(f?.label == "")
    }

    @Test func theErrorOfA409KeepsTheObjectDetail() {
        let body = #"{"detail":{"code":"secret_in_message","spans":[{"start":0,"end":10,"kind":"token"}],"placeholder":"[x]","target":{"type":"vault","id":""}}}"#
        let e = ApiError.from(status: 409, body: Data(body.utf8))
        #expect(e.detail == "HTTP 409")
        #expect(SecretGuard.of(e)?.spans.count == 1)
        #expect(!e.description.contains("secret_in_message"))
        #expect(ApiError.from(status: 400, body: Data(#"{"detail":"no model"}"#.utf8)).detailJson == nil)
    }

    @Test func valuesAreCutByUtf16OffsetsAfterEmojiAndKorean() {
        let before = "내 키는 👩‍💻 여기: "
        let text = before + key + " 저장해줘"
        let start = before.utf16.count   // UTF-16 units: the emoji counts as several, far more than its one Character
        #expect(start != before.count)
        #expect(SecretGuard.values(text, [SecretSpan(start, start + key.utf16.count, "api_key")]) == [key])
        // a span past the end is skipped, never a crash
        #expect(SecretGuard.values(text, [SecretSpan(start, text.utf16.count + 5, "api_key")]).isEmpty)
        // the same value twice comes once
        let twice = text + " " + key
        #expect(SecretGuard.values(twice, [SecretSpan(start, start + key.utf16.count, "api_key"),
                                           SecretSpan(twice.utf16.count - key.utf16.count, twice.utf16.count, "api_key")]) == [key])
    }

    @Test func composerSpansFollowTheTrimAndSkipAttachmentLines() {
        let composer = "  \n한국어 🙂 " + key + "  "
        let sent = composer.trimmingCharacters(in: .whitespacesAndNewlines) + "\n\n📎 report.pdf"
        let a = at(key, in: sent)
        let spans = [SecretSpan(a, a + key.utf16.count, "api_key"), SecretSpan(sent.utf16.count - 5, sent.utf16.count, "token")]
        let moved = SecretGuard.composerSpans(composer, spans)
        #expect(moved.count == 1)
        #expect(SecretGuard.values(composer, moved) == [key])
        #expect(moved[0].start == a + 3)
    }

    @Test func replaceGoesFromTheLastSpanSoOffsetsHold() {
        let b = "ghp_" + String(repeating: "b", count: 36)
        let text = "🙂 첫째 \(key) 둘째 \(b) 끝"
        let spans = [SecretSpan(at(key, in: text), at(key, in: text) + key.utf16.count, "api_key"),
                     SecretSpan(at(b, in: text), at(b, in: text) + b.utf16.count, "token")]
        #expect(SecretGuard.replace(text, spans, with: "[x]") == "🙂 첫째 [x] 둘째 [x] 끝")
        #expect(SecretGuard.replace(text, spans, with: "(saved)", value: key) == "🙂 첫째 (saved) 둘째 \(b) 끝")
        #expect(SecretGuard.replace(text, [SecretSpan(3, 9999, "token")], with: "x") == text)   // out of range: untouched
    }

    @Test func usesStartWithTheServersTarget() {
        func found(_ type: String, _ id: String, _ kind: String) -> SecretFound {
            SecretFound(spans: [SecretSpan(0, 10, kind)], placeholder: "", target: SecretTarget(type, id))
        }
        #expect(SecretGuard.uses(found("provider", "anthropic", "api_key")) == [.aiKey, .serviceKey, .login])
        #expect(SecretGuard.uses(found("connector", "slack", "token")).first == .connectorKey)
        #expect(SecretGuard.uses(found("wallet", "", "card")) == [.card])
        #expect(SecretGuard.uses(found("vault", "", "card")) == [.card])
        #expect(SecretGuard.uses(found("vault", "", "password")) == [.login, .serviceKey])
        #expect(SecretGuard.uses(found("vault", "", "token")).first == .serviceKey)
        #expect(SecretGuard.uses(found("provider", "", "api_key")).first == .serviceKey)   // no id: not an AI key form
        #expect(SecretGuard.hostGuess(" GitHub ") == "api.github.com")
        #expect(SecretGuard.hostGuess("Nowhere") == "")
        #expect(SecretGuard.nameFor("api.github.com") == "api-github-com")
        #expect(SecretGuard.nameFor("  A..b ") == "a-b")
        #expect(SecretGuard.nameFor("") == "key")
        #expect(SecretGuard.nameFor("x") == "key-x")
    }

    @Test func theHandOffKeepsTheValueOnlyBrieflyAndNeverPrintsIt() {
        let now = Box<Double>(0)
        let h = SecretHandoff(clock: { now.value }, ttl: 1)
        h.offer(["4111111111111111"])
        #expect(!String(describing: h).contains("4111"))
        #expect(!String(reflecting: h).contains("4111"))
        #expect(h.peek() == "4111111111111111")
        now.value = 2
        #expect(h.peek() == nil)                  // too old: gone
        h.offer(["4111111111111111"])
        h.clear()
        #expect(h.peek() == nil)                  // the form took it: gone
        h.offer(["4111111111111111"])
        h.kept()
        #expect(h.peek() == nil)
        #expect(h.takeSaved())
        #expect(!h.takeSaved())
    }

    @Test func sendAsIsAddsSecretsAllowToTheTurn() async throws {
        let t = FakeTransport(.ok(200, #"{"id":"t1"}"#), .ok(200, #"{"events":[{"type":"done"}],"next":1,"done":true}"#))
        let api = KeloApi(baseUrl: "https://k.example", token: "tok", transport: t)
        try await TurnFollower(api: KeloTurnApi(api: api)).talk(text: "키 \(key)", timezone: "UTC", secrets: SecretGuard.allow) { _ in }
        let body = try JSON.parse(t.requests.value[0].httpBody ?? Data())
        #expect(body["secrets"]?.stringValue == "allow")
        #expect(body["text"]?.stringValue == "키 \(key)")
        #expect(TurnBody.of(text: "a", timezone: "UTC")["secrets"] == nil)
    }

    @Test func theSaveCallsGoStraightToTheVault() async throws {
        let t = FakeTransport(.ok(200, "{}"), .ok(200, #"{"name":"github-login"}"#), .ok(200, "{}"), .ok(200, "{}"), .ok(200, "{}"),
                              .ok(200, "{}"))
        let api = KeloApi(baseUrl: "https://k.example", token: "tok", transport: t)
        try await SecretSave.aiKey(api, provider: "anthropic", value: " \(key) ")
        try await SecretSave.login(api, site: "https://github.com/login", label: "GitHub", username: " me ", password: "pw 1")
        try await SecretSave.serviceKey(api, host: "https://api.github.com/v3", label: "", value: key)
        try await SecretSave.connectorKey(api, form: KeyForm(id: "slack", fields: [KeyField(id: "team", label: "Team", secret: false),
                                                                                    KeyField(id: "token", label: "Token", secret: true)]),
                                          value: key, others: ["team": "t1"])
        let r = t.requests.value
        func body(_ i: Int) -> JSON { (try? JSON.parse(r[i].httpBody ?? Data())) ?? .null }
        #expect(r[0].httpMethod == "PUT" && r[0].url?.path == "/api/providers/anthropic/key" && body(0)["api_key"]?.stringValue == key)
        #expect(r[1].url?.path == "/api/vault/logins" && body(1)["site"]?.stringValue == "https://github.com/login")
        #expect(r[2].url?.path == "/api/vault/github-login/login" && body(2)["mode"]?.stringValue == "now"
                && body(2)["username"]?.stringValue == "me" && body(2)["password"]?.stringValue == "pw 1")
        #expect(r[3].url?.path == "/api/credentials" && body(3)["name"]?.stringValue == "api-github-com"
                && body(3)["hosts"] == .array([.string("api.github.com")]) && body(3)["label"]?.stringValue == "api.github.com"
                && body(3)["placement"]?["type"]?.stringValue == "bearer_header")
        #expect(r[4].httpMethod == "PUT" && r[4].url?.path == "/api/credentials/api-github-com/secret" && body(4)["secret"]?.stringValue == key)
        #expect(r[5].url?.path == "/api/keys/slack" && body(5)["values"] == .object(["team": .string("t1"), "token": .string(key)]))
    }
}
