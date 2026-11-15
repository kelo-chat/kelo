import Testing
import Foundation
import KeloCore
@testable import KeloShared
@testable import KeloChat

@MainActor
private func secretApp(_ route: @escaping @Sendable (String, String, Data?) -> (Int, String)) -> (AppModel, RouteTransport) {
    let t = RouteTransport(route)
    let defaults = UserDefaults(suiteName: "kelo-secret-test-\(UUID().uuidString)")!
    let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")), prefs: Prefs(defaults: defaults),
                       transport: t, cache: ScreenCache(directory: nil, key: { nil }))
    return (app, t)
}

private func waitFor(_ cond: @MainActor () -> Bool) async {
    for _ in 0..<200 {
        if await cond() { return }
        try? await Task.sleep(nanoseconds: 10_000_000)
    }
}

private let openKey = #"{"id": "r1", "kind": "key", "name": "openweather", "label": "OpenWeather", "hosts": ["api.openweathermap.org"], "state": "open", "message_id": 7, "created_at": 100}"#
private let openCard = #"{"id": "r3", "kind": "card", "label": "Tickets", "state": "open", "message_id": null, "created_at": 120}"#

/// A switch the fake server flips (it is called from the transport's thread).
private final class Flag: @unchecked Sendable {
    private let lock = NSLock()
    private var v = false
    var value: Bool {
        get { lock.withLock { v } }
        set { lock.withLock { v = newValue } }
    }
}

private func secretRows(_ items: [ChatItem]) -> [SecretRequest] {
    items.compactMap { if case .secret(let s) = $0 { return s.request }; return nil }
}

struct SecretEntriesTests {
    private let req = SecretRequest(id: "r1", kind: SecretRequest.kindKey, name: "k", messageId: 7)

    @Test func anEntryShownFromTheListJoinsTheRowsWhenAnswered() {
        let items: [ChatItem] = [.msg(.init(key: "m7", mine: false, text: "I need a key", id: 7))]
        let next = SecretEntries.settle(items: items, secrets: [req], req, state: SecretRequest.stateSaved, result: SecretResult(checked: "ok"),
                                        followsHere: true)
        #expect(next.items.count == 2)
        #expect(secretRows(next.items).first?.state == SecretRequest.stateSaved)
        #expect(secretRows(next.items).first?.result?.checked == "ok")
        #expect(next.secrets.isEmpty)   // the server lists only open entries
        // the folded row and the kept list together still show it once
        #expect(secretRows(ChatReducer.withPending(next.items, next.secrets)).count == 1)
        // with no reply here it is not placed at all
        #expect(SecretEntries.settle(items: items, secrets: [req], req, state: SecretRequest.stateSaved, result: nil, followsHere: false).items == items)
    }

    /// Android SecretRequestsTest.anAnswerArrivingAfterTheReplyEndedDoesNotBringTheLineBack.
    @Test func anAnswerArrivingAfterTheReplyEndedDoesNotBringTheLineBack() {
        var n = 0
        let key: () -> String = { n += 1; return "k\(n)" }
        let login = SecretRequest(id: "sr_k", kind: "login", name: "login-kakao")
        var items = ChatReducer.apply([.msg(.init(key: "u", mine: true, text: "신청해 줘"))], .secretRequest(id: "c1", request: login), newKey: key)
        items = ChatReducer.apply(items, .error("결제 페이지를 열 수 없어요"), newKey: key)
        items = ChatReducer.endTurn(items)                      // the open entry stays: the server still waits for it
        #expect(!secretRows(items).isEmpty)
        // the PUT answer comes now: no reply runs here any more
        let answer = SecretSaveAnswer(ok: true, reply: "live")
        var saved = login
        saved.state = SecretRequest.stateSaved
        items = ChatReducer.afterAnswer(items, saved, followsHere: SecretRules.followsHere(answer, busy: false))
        #expect(secretRows(items).isEmpty)
        // the same when the entry was shown only from the server's list (not in the rows): it is not placed
        let rows: [ChatItem] = [.msg(.init(key: "u", mine: true, text: "신청해 줘"))]
        #expect(ChatReducer.afterAnswer(rows, saved, followsHere: SecretRules.followsHere(answer, busy: false)) == rows)
    }

    /// Android SecretRequestsTest.aSaveWithNoReplyClearsTheLineAtOnce.
    @Test func aSaveWithNoReplyClearsTheLineAtOnce() {
        let login = SecretRequest(id: "sr_k", kind: "login", name: "login-kakao")
        let rows: [ChatItem] = [.msg(.init(key: "u", mine: true, text: "신청해 줘")), .secret(.init(key: "s", request: login))]
        var saved = login
        saved.state = SecretRequest.stateSaved
        #expect(ChatReducer.afterAnswer(rows, saved, followsHere: SecretRules.followsHere(SecretSaveAnswer(), busy: false)).map(\.key) == ["u"])
        #expect(ChatReducer.afterAnswer(rows, saved, followsHere: SecretRules.followsHere(SecretSaveAnswer(reply: "none"), busy: false)).map(\.key) == ["u"])
        // with a reply to follow, the line folds in place and that reply's end clears it
        let folded = ChatReducer.afterAnswer(rows, saved, followsHere: SecretRules.followsHere(SecretSaveAnswer(turnId: "t2", reply: "started"), busy: false))
        #expect(secretRows(folded).first?.state == "saved")
        #expect(ChatReducer.endTurn(folded).map(\.key) == ["u"])
        // a list refresh with no reply running drops answered lines; open ones stay
        #expect(ChatReducer.dropSettled(folded).map(\.key) == ["u"])
        #expect(ChatReducer.dropSettled(rows) == rows)
    }

    /// Android SecretRequestsTest.whetherAReplyHereWillClearTheLine.
    @Test func whetherAReplyHereWillClearTheLine() {
        #expect(SecretRules.followsHere(SecretSaveAnswer(turnId: "t1"), busy: false))                       // a reply to follow
        #expect(SecretRules.followsHere(SecretSaveAnswer(turnId: "t1", reply: "started"), busy: false))
        #expect(!SecretRules.followsHere(SecretSaveAnswer(turnId: "t1", reply: "none"), busy: true))      // the server says none
        #expect(SecretRules.followsHere(SecretSaveAnswer(), busy: true))                                    // the reply here goes on
        #expect(SecretRules.followsHere(SecretSaveAnswer(reply: "live"), busy: true))
        #expect(!SecretRules.followsHere(SecretSaveAnswer(), busy: false))                                  // nothing runs here
        #expect(!SecretRules.followsHere(SecretSaveAnswer(reply: "live"), busy: false))                    // runs, but not followed here
        #expect(!SecretRules.followsHere(SecretSaveAnswer(reply: "queued"), busy: false))
        #expect(!SecretRules.followsHere(nil, busy: false))                                                 // cancel / gone, nothing runs
        #expect(SecretRules.followsHere(nil, busy: true))
        // older and newer answers parse
        #expect(SecretSaveAnswer.parse(try! JSON.parse(Data(#"{"ok":true,"turn_id":null,"reply":"live"}"#.utf8))).reply == "live")
        #expect(SecretSaveAnswer.parse(try! JSON.parse(Data(#"{"ok":true}"#.utf8))).reply == nil)
        #expect(SecretSaveAnswer(turnId: "t", reply: "none").turnToFollow == nil)
        #expect(SecretSaveAnswer(turnId: "t", reply: "started").turnToFollow == "t")
    }

    /// Android SecretRequestsTest.anAnsweredEntryNeverSticksAboveTheComposer (spec 5.16): a login request whose asking reply was never
    /// stored (message_id null) is saved here, the run fails; the folded "saved" line must not stay at the end of the chat.
    @Test func anAnsweredEntryNeverSticksAboveTheComposer() {
        var n = 0
        let key: () -> String = { n += 1; return "k\(n)" }
        let login = SecretRequest(id: "sr_k", kind: "login", name: "login-kakao", label: "카카오 (선물하기 체험단)", messageId: nil, createdAt: 1)
        var items: [ChatItem] = [.msg(.init(key: "u", mine: true, text: "선물하기 체험단 신청해 줘"))]
        var secrets = [login]
        // the open entry from GET /api/secret-requests shows at the end; the owner saves it here
        #expect(ChatReducer.withPending(items, secrets).last?.key == "secret:sr_k")
        var saved = login
        saved.state = SecretRequest.stateSaved
        items = ChatReducer.placeAnswered(items, saved)
        secrets = secrets.map { $0.id == "sr_k" ? saved : $0 }
        #expect(ChatReducer.isSettledEntry(items.last!))
        // the run fails: an error row, then the reply's end (done, error, stopped all end the same way)
        items = ChatReducer.apply(items, .error("결제 페이지를 열 수 없어요"), newKey: key)
        for end in ["done", "error", "cancelled"] {
            let ended = ChatReducer.endTurn(items)
            #expect(secretRows(ended).isEmpty, "\(end)")
            #expect(secretRows(ChatReducer.withPending(ended, SecretRules.settled(secrets))).isEmpty, "\(end)")
        }
        // a reload (new rows from the server) with the old kept list: still nothing at the end
        let reloaded: [ChatItem] = [.msg(.init(key: "m1", mine: true, text: "선물하기 체험단 신청해 줘", id: 1))]
        #expect(ChatReducer.withPending(reloaded, secrets).map(\.key) == reloaded.map(\.key))
        #expect(SecretRules.settled(secrets).isEmpty)
        // an entry the server still has open keeps showing
        #expect(SecretRules.settled(secrets + [SecretRequest(id: "sr_o", kind: "key", name: "telegram")]).map(\.id) == ["sr_o"])
    }

    /// Android SecretRequestsTest.aReplyEndingKeepsAnOpenEntry: the server keeps an open entry, so the reply's end leaves it.
    @Test func aReplyEndingKeepsAnOpenEntry() {
        var n = 0
        let key: () -> String = { n += 1; return "k\(n)" }
        let items = ChatReducer.apply([.msg(.init(key: "u", mine: true, text: "토큰"))],
                                      .secretRequest(id: "c1", request: SecretRequest(id: "sr_1", kind: "key", name: "telegram")), newKey: key)
        #expect(secretRows(items).map(\.id) == ["sr_1"])
        #expect(ChatReducer.endTurn(items).map(\.key) == items.map(\.key))
        #expect(secretRows(ChatReducer.endTurn(items)).first?.open == true)
    }

    @Test func anEntryInTheRowsFoldsWhereItIs() {
        let items: [ChatItem] = [.secret(.init(key: "k1", request: req)), .msg(.init(key: "m8", mine: false, text: "later", id: 8))]
        let next = SecretEntries.settle(items: items, secrets: [], req, state: SecretRequest.stateCancelled, result: nil, followsHere: true)
        #expect(next.items.count == 2)
        #expect(next.items[0].key == "k1")
        #expect(secretRows(next.items).first?.state == SecretRequest.stateCancelled)
        #expect(next.secrets.isEmpty)
    }

    @Test func anAnsweredEntryIsNotChangedAgain() {
        var saved = req; saved.state = SecretRequest.stateSaved
        let next = SecretEntries.settle(items: [.secret(.init(key: "k1", request: saved))], secrets: [saved], req,
                                        state: SecretRequest.stateExpired, result: nil, followsHere: true)
        #expect(secretRows(next.items).first?.state == SecretRequest.stateSaved)
        #expect(next.secrets.isEmpty)
    }

    @Test func thePollAsksAgainOnlyWhenTheCountChanges() {
        #expect(!SecretEntries.pollWants(before: nil, now: 0))
        #expect(SecretEntries.pollWants(before: nil, now: 2))
        #expect(!SecretEntries.pollWants(before: 2, now: 2))
        #expect(SecretEntries.pollWants(before: 2, now: 1))
        #expect(SecretEntries.pollWants(before: 0, now: 1))
    }
}

@Suite(.serialized) struct ChatSecretEntryTests {
    @MainActor @Test func openEntriesFromTheServerShowUnderTheirMessage() async {
        let (app, _) = secretApp { method, path, _ in
            switch (method, path) {
            case ("GET", let p) where p.hasPrefix("/api/messages"):
                return (200, #"[{"id": 6, "role": "user", "content": "check the weather"}, {"id": 7, "role": "assistant", "content": "I need a key"}]"#)
            case ("GET", "/api/secret-requests"): return (200, "{\"items\": [\(openKey), \(openCard)]}")
            case ("GET", "/api/followups"): return (200, #"{"items": []}"#)
            default: return (200, "[]")
            }
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.start()
        await waitFor { m.secrets.count == 2 && m.loaded }
        let rows = m.rows
        #expect(m.items.count == 2)
        #expect(rows.count == 4)
        // the key under message 7, the card (its reply not stored yet) at the end
        #expect(rows[2].key == "secret:r1")
        #expect(rows[3].key == "secret:r3")
    }

    @MainActor @Test func cancelFoldsAtOnceAndTellsTheServer() async {
        let cancelled = Flag()
        let (app, t) = secretApp { method, path, _ in
            switch (method, path) {
            case ("GET", "/api/secret-requests"): return (200, cancelled.value ? #"{"items": []}"# : "{\"items\": [\(openKey)]}")
            case ("POST", "/api/secret-requests/r1/cancel"): cancelled.value = true; return (200, #"{"ok": true}"#)
            default: return (200, "[]")
            }
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        await m.refreshSecretsNow()
        guard let r = m.secrets.first else { Issue.record("no entry"); return }
        m.secretCancelled(r)
        #expect(secretRows(m.rows).isEmpty)   // no reply runs here: the line goes at once (spec 5.16)
        await waitFor { t.requests.contains { $0.httpMethod == "POST" && $0.url?.path == "/api/secret-requests/r1/cancel" } }
        #expect(t.requests.contains { $0.httpMethod == "POST" && $0.url?.path == "/api/secret-requests/r1/cancel" })
        await m.refreshSecretsNow()
        #expect(secretRows(m.rows).isEmpty)
    }

    @MainActor @Test func aSaveThatCarriesOnFollowsTheNewReply() async {
        let (app, t) = secretApp { method, path, _ in
            switch (method, path) {
            case ("GET", let p) where p.hasPrefix("/api/turns/t9"):
                return (200, #"{"events": [{"type": "text", "text": "Thanks, the forecast is sunny."}, {"type": "done"}], "next": 2, "done": true}"#)
            case ("GET", "/api/secret-requests"): return (200, #"{"items": []}"#)
            default: return (200, "[]")
            }
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        let r = SecretRequest(id: "r1", kind: SecretRequest.kindKey, name: "openweather")
        m.secretSaved(r, answer: SecretSaveAnswer(ok: true, checked: true, turnId: "t9"), result: SecretResult(checked: "ok"))
        #expect(m.busy)
        #expect(secretRows(m.items).first?.state == SecretRequest.stateSaved)   // folded while the reply runs
        await m.waitForTurn()
        #expect(!m.busy)
        #expect(secretRows(m.items).isEmpty)   // the reply's end clears the finished line (spec 5.16)
        #expect(m.items.contains { $0.asMsg?.text == "Thanks, the forecast is sunny." && $0.asMsg?.mine == false })
        #expect(!m.items.contains { $0.asMsg?.mine == true })   // no bubble of the user's own
        #expect(t.requests.contains { $0.url?.path.hasPrefix("/api/turns/t9") == true })
    }

    @MainActor @Test func aSaveWithoutATurnStartsNothing() async {
        let (app, t) = secretApp { _, _, _ in (200, "[]") }
        let m = ChatModel(app: app, uploader: FakeUploader())
        let r = SecretRequest(id: "r2", kind: SecretRequest.kindLogin, name: "shop")
        m.secretSaved(r, answer: SecretSaveAnswer(ok: true), result: SecretResult(mode: "after_success"))
        #expect(!m.busy)
        #expect(secretRows(m.rows).isEmpty)   // nothing here would clear it later, so it goes now
        #expect(!t.requests.contains { $0.url?.path.hasPrefix("/api/turns") == true })
    }

    @MainActor @Test func aGoneEntryFolds() {
        let (app, _) = secretApp { _, _, _ in (200, "[]") }
        let m = ChatModel(app: app, uploader: FakeUploader())
        let r = SecretRequest(id: "r5", kind: SecretRequest.kindKey, name: "x")
        m.secretGone(r)
        #expect(secretRows(m.rows).isEmpty)   // no reply runs here
    }

    @MainActor @Test func aCancelWhoseAnswerStartsAReplyFollowsIt() async {
        let (app, t) = secretApp { method, path, _ in
            switch (method, path) {
            case ("POST", "/api/secret-requests/r1/cancel"): return (200, #"{"ok": true, "turn_id": "t4", "reply": "started"}"#)
            case ("GET", let p) where p.hasPrefix("/api/turns/t4"):
                return (200, #"{"events": [{"type": "text", "text": "OK, without it."}, {"type": "done"}], "next": 2, "done": true}"#)
            default: return (200, #"{"items": []}"#)
            }
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.secretCancelled(SecretRequest(id: "r1", kind: SecretRequest.kindKey, name: "k"))
        await waitFor { t.requests.contains { $0.url?.path.hasPrefix("/api/turns/t4") == true } && !m.busy }
        #expect(m.items.contains { $0.asMsg?.text == "OK, without it." })
    }
}
