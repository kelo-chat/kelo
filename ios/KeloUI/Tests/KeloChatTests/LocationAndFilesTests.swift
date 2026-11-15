import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

/// Port of android chat/LocationAndFilesTest.kt and device/PhonePlaceTest (backlog 24: the location card; 26: the user's files in
/// their bubble): events, the list, and the pure rules of the screens.
struct LocationAndFilesTests {
    private func keys() -> () -> String { var n = 0; return { n += 1; return "k\(n)" } }
    private func ev(_ json: String) -> TurnEvent { TurnEvent.parse(try! JSON.parse(Data(json.utf8))) }
    private func ask(_ items: [ChatItem]) -> ChatItem.LocationAsk? {
        if case .locationAsk(let l)? = items.last { return l }
        return nil
    }

    @Test func theLocationCardComesWaitsAndEnds() {
        let key = keys()
        let waiting = ev(#"{"type":"location_request","approval_id":"a1","state":"waiting","reason":"To find parking near you","settings":true,"expires_at":99}"#)
        #expect(waiting == .locationRequest(.init(approvalId: "a1", state: "waiting", reason: "To find parking near you", settings: true)))
        var items = ChatReducer.apply([.msg(.init(key: "u", mine: true, text: "주차장 찾아줘"))], waiting, newKey: key)
        let card = ask(items)
        #expect(card?.open == true && card?.settings == true && card?.reason == "To find parking near you")
        // this phone is reading and sending it; a repeated "waiting" does not undo that
        items = ChatReducer.locationState(items, "a1", ChatItem.LocationAsk.sending)
        items = ChatReducer.apply(items, waiting, newKey: key)
        #expect(ask(items)?.state == ChatItem.LocationAsk.sending)
        // the server's end state wins
        items = ChatReducer.apply(items, ev(#"{"type":"location_request","approval_id":"a1","state":"allowed"}"#), newKey: key)
        #expect(ask(items)?.state == ChatItem.LocationAsk.allowed)
        #expect(items.count == 2)
        // an end event for a card this phone never showed adds nothing
        #expect(ChatReducer.apply(items, ev(#"{"type":"location_request","approval_id":"zz","state":"expired"}"#), newKey: key).map(\.key) == items.map(\.key))
    }

    @Test func aCardNobodyAnsweredEndsWithTheReply() {
        let items = ChatReducer.apply([], .locationRequest(.init(approvalId: "a1")), newKey: keys())
        #expect(ask(ChatReducer.endTurn(items))?.state == ChatItem.LocationAsk.expired)
        let done = ChatReducer.locationState(items, "a1", ChatItem.LocationAsk.allowed)
        #expect(ask(ChatReducer.endTurn(done))?.state == ChatItem.LocationAsk.allowed)
    }

    @Test func theCardsTapsAndEndLines() {
        #expect(LocationAskRules.onShare(granted: true, settings: true, blocked: true) == .send)
        #expect(LocationAskRules.onShare(granted: false, settings: false, blocked: false) == .askSystem)
        #expect(LocationAskRules.onShare(granted: false, settings: true, blocked: false) == .openSettings)
        #expect(LocationAskRules.onShare(granted: false, settings: false, blocked: true) == .openSettings)
        #expect(LocationAskRules.onShare(granted: true, settings: false, blocked: false, servicesOn: false) == .servicesOff)
        #expect(LocationAskRules.onShare(granted: false, settings: true, blocked: true, servicesOn: false) == .servicesOff)
        #expect(LocationAskRules.endLine(ChatItem.LocationAsk.waiting) == nil)
        #expect(LocationAskRules.endLine(ChatItem.LocationAsk.sending) == nil)
        #expect(LocationAskRules.endLine("allowed") == R.string.location_ask_shared)
        #expect(LocationAskRules.endLine("refused") == R.string.location_ask_refused)
        #expect(LocationAskRules.endLine("cancelled") == R.string.location_ask_expired)
    }

    @Test func theUserMessageEventPutsTheFilesOnTheBubble() {
        let e = ev(#"""
            {"type":"user_message","message_id":41,"auto_text":true,"attachments":[
            {"name":"photo.jpg","path":"uploads/photo.jpg","kind":"image","mime":"image/jpeg","size":120,"url":"/api/uploads/photo.jpg/file","thumb":"/api/uploads/photo.jpg/thumb"},
            {"name":"report.pdf","path":"uploads/report.pdf","kind":"document","url":"/api/uploads/report.pdf/file","thumb":null},
            {"name":""}]}
            """#)
        guard case .userMessage(let u) = e else { Issue.record("not a user_message"); return }
        #expect(u.messageId == 41 && u.autoText)
        #expect(u.attachments.map(\.name) == ["photo.jpg", "report.pdf"])
        #expect(u.attachments[0].visual && u.attachments[1].thumb == nil)
        let sent = ChatItem.msg(.init(key: "u", mine: true, text: "photo.jpg 내용을 읽고 요약해줘.", files: [MessageFile(name: "photo.jpg", path: "uploads/photo.jpg", kind: "image")]))
        let items = ChatReducer.apply([sent], e, newKey: keys())
        let m = items.first?.asMsg
        #expect(items.count == 1 && m?.id == 41 && m?.autoText == true && m?.files.first?.thumb == "/api/uploads/photo.jpg/thumb")
    }

    @Test func storedMessagesCarryTheirFiles() throws {
        let m = try JSONDecoder().decode(ChatMessage.self, from: Data(#"{"id":3,"role":"user","content":"x","attachments":[{"name":"a.png","path":"uploads/a.png","kind":"image","thumb":"/t"}],"auto_text":true}"#.utf8))
        #expect(m.attachments.first?.name == "a.png" && m.autoText)
        #expect(try JSONDecoder().decode(ChatMessage.self, from: Data(#"{"id":4}"#.utf8)).attachments.isEmpty)
        // kept on the phone and read back
        #expect(try JSONDecoder().decode(ChatMessage.self, from: JSONEncoder().encode(m)) == m)
        guard case .msg(let item) = ChatReducer.item(m) else { Issue.record("no message"); return }
        #expect(item.files.count == 1 && item.autoText)
    }

    @Test func theBubbleHidesTheAppsOwnWords() {
        let files = [MessageFile(name: "a.jpg", path: "uploads/a.jpg", kind: "image"), MessageFile(name: "b.pdf", path: "uploads/b.pdf")]
        let line = { (s: String) in "📎 \(s)" }
        #expect(MessageFileRules.shownText("a.jpg, b.pdf 내용을 읽고 요약해줘.", files: files, autoText: true, line: line) == "")
        #expect(MessageFileRules.shownText("이거 봐줘\n\n📎 uploads/a.jpg\n📎 uploads/b.pdf", files: files, autoText: false, line: line) == "이거 봐줘")
        #expect(MessageFileRules.shownText("plain", files: [], autoText: true, line: line) == "plain")
        #expect(MessageFileRules.rows(1) == [1])
        #expect(MessageFileRules.rows(2) == [2])
        #expect(MessageFileRules.rows(3) == [2, 1])
        #expect(MessageFileRules.rows(4) == [2, 2])
        #expect(MessageFileRules.rows(0) == [])
    }

    @Test func aWaitingCardComesBackFromThePendingListAfterAReload() throws {
        let pending = try JSON.parse(Data(#"""
            {"items":[
            {"type":"location_request","kind":"location","approval_id":"a1","state":"waiting","reason":"For the weather","settings":false,"name":"my_location","asked_at":5},
            {"type":"approval_request","approval_id":"b1","name":"browser_click","asked_at":4}]}
            """#.utf8))
        let locations: [TurnEvent.LocationRequest] = (pending["items"]?.arrayValue ?? []).compactMap {
            if case .locationRequest(let l) = TurnEvent.parse($0) { return l }
            return nil
        }
        #expect(locations.count == 1)
        // the location item is not read as an approval card
        #expect(!PendingApproval.parseList(pending).contains { $0.id == "a1" })
        let reloaded: [ChatItem] = [.msg(.init(key: "m1", mine: true, text: "날씨 알려줘", id: 1))]
        let shown = ChatReducer.withPendingLocations(reloaded, locations, busy: false)
        #expect(shown.last?.key == "loc:a1")
        #expect(ask(shown)?.reason == "For the weather")
        #expect(ChatReducer.withPendingLocations(shown, locations, busy: false).map(\.key) == shown.map(\.key))   // not twice
        // answered on another phone (or run out): no longer listed -> it ends here; a reply followed here says that itself
        #expect(ask(ChatReducer.withPendingLocations(shown, [], busy: false))?.state == ChatItem.LocationAsk.expired)
        #expect(ask(ChatReducer.withPendingLocations(shown, [], busy: true))?.state == ChatItem.LocationAsk.waiting)
        // one this phone is sending stays as it is
        let sending = ChatReducer.locationState(shown, "a1", ChatItem.LocationAsk.sending)
        #expect(ask(ChatReducer.withPendingLocations(sending, [], busy: false))?.state == ChatItem.LocationAsk.sending)
    }

    @Test func longFileNamesKeepTheirEnd() {
        #expect(MessageFileRules.chipName("notes.docx") == "notes.docx")
        let cut = MessageFileRules.chipName("family-trip-budget-third-quarter-2026-final-revised-v3.pdf")
        #expect(cut.hasSuffix("v3.pdf") && cut.contains("…") && cut.count <= 26, "\(cut)")
        let noDot = MessageFileRules.chipName(String(repeating: "가", count: 40))
        #expect(noDot.count <= 13 && noDot.contains("…"))
        // Korean letters are twice as wide: cut sooner, the extension kept
        let ko = MessageFileRules.chipName("2026년-3분기-가족여행-예산-정리-최종본-수정3.pdf")
        #expect(ko.hasSuffix(".pdf") && ko.filter { $0 == "…" }.count == 1 && MessageFileRules.columns(ko) <= 26, "\(ko)")
        #expect(MessageFileRules.columns("가") == 2 && MessageFileRules.columns("a") == 1 && MessageFileRules.columns("ж") == 1)
        #expect(MessageFileRules.chipName("짧은이름.pdf") == "짧은이름.pdf")
    }

    @Test func theTurnCarriesThePathsAndAutoText() {
        let body = TurnBody.of(text: "x", timezone: "Asia/Seoul", attachments: ["uploads/a.jpg"], autoText: true)
        #expect(body["attachments"] == .array([.object(["path": .string("uploads/a.jpg")])]))
        #expect(body["auto_text"] == .bool(true))
        let plain = TurnBody.of(text: "x", timezone: "Asia/Seoul")
        #expect(plain["attachments"] == nil && plain["auto_text"] == nil)
    }

    @MainActor @Test func theNeighbourhoodGoesWithThePlace() {
        let b = PhonePlace.body(lat: 37.498123, lon: 127.027612, accuracy: 35, name: "강남구", country: "KR", sublocality: "역삼동")
        #expect(b["sublocality"] == .string("역삼동"))
        #expect(PhonePlace.body(lat: 37.5, lon: 127.0, accuracy: nil, name: "", country: "")["sublocality"] == nil)
        #expect(PhonePlace.sublocalityOf("강남구", town: "강남구") == "")   // only repeats the town
        #expect(PhonePlace.sublocalityOf(nil, town: "Seoul") == "")
        #expect(PhonePlace.sublocalityOf(" Yeoksam-dong ", town: "Seoul") == "Yeoksam-dong")
    }
}

/// The chat sends the files with the message and answers the location card (ChatModel against a scripted server).
@Suite(.serialized) struct LocationAndFilesModelTests {
    @MainActor private func chat(_ route: @escaping @Sendable (String, String, Data?) -> (Int, String)) -> (ChatModel, RouteTransport) {
        let t = RouteTransport(route)
        let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")),
                           prefs: Prefs(defaults: UserDefaults(suiteName: "kelo-loc-\(UUID().uuidString)")!), transport: t,
                           cache: ScreenCache(directory: nil, key: { nil }))
        return (ChatModel(app: app, uploader: FakeUploader()), t)
    }

    @MainActor private func until(_ cond: () -> Bool) async -> Bool {
        for _ in 0..<300 { if cond() { return true }; try? await Task.sleep(nanoseconds: 10_000_000) }
        return cond()
    }

    @MainActor @Test func aSharedPlaceGoesToTheServerAndTheCardEnds() async {
        let (m, t) = chat { method, path, _ in
            method == "POST" && path == "/api/location-requests/a1" ? (200, #"{"ok": true}"#) : (200, "[]")
        }
        m.apply(.locationRequest(.init(approvalId: "a1")))
        m.answerLocation("a1", place: ["lat": .number(37.5), "lon": .number(127.0), "sublocality": .string("역삼동")])
        #expect(await until { m.items.contains { if case .locationAsk(let l) = $0 { return l.state == "allowed" }; return false } })
        let sent = t.requests.first { $0.url?.path == "/api/location-requests/a1" }
        let body = try? JSON.parse(sent?.httpBody ?? Data())
        #expect(body?["allowed"] == .bool(true) && body?["place"]?["sublocality"] == .string("역삼동"))
    }

    @MainActor @Test func notNowAndAGoneQuestionAndANetworkFailure() async {
        let status = Box409()
        let (m, t) = chat { method, path, _ in
            guard method == "POST", path.hasPrefix("/api/location-requests/") else { return (200, "[]") }
            return (status.next(), #"{"detail": "gone"}"#)
        }
        for id in ["r", "g", "n"] { m.apply(.locationRequest(.init(approvalId: id))) }
        status.codes = [200]
        m.answerLocation("r", place: nil)
        #expect(await until { state(m, "r") == "refused" })
        status.codes = [409]
        m.answerLocation("g", place: ["lat": .number(1), "lon": .number(2)])
        #expect(await until { state(m, "g") == "expired" })
        status.codes = [503]
        m.answerLocation("n", place: nil)
        #expect(await until { t.requests.filter { $0.url?.path == "/api/location-requests/n" }.count == 1 && state(m, "n") == "waiting" })
        let body = try? JSON.parse(t.requests.first { $0.url?.path == "/api/location-requests/r" }?.httpBody ?? Data())
        #expect(body?["allowed"] == .bool(false) && body?["place"] == nil)
    }

    @MainActor @Test func filesGoWithTheMessageAndNothingTypedIsTheAppsOwnSentence() async {
        let (m, t) = chat { method, path, _ in
            if method == "POST" && path == "/api/turns" { return (200, #"{"id": "t1"}"#) }
            if path.hasPrefix("/api/turns/t1") { return (200, #"{"events": [{"type": "done"}], "next": 1, "done": true}"#) }
            return (200, "[]")
        }
        m.attach(image: true, label: "photo.jpg") {
            PreparedUpload(name: "photo.jpg", source: .data(Data([1, 2, 3])), type: "image/jpeg", length: 3)
        }
        #expect(await until { m.attachments.allSatisfy { $0.state == .done } })
        m.send(words: ChatWords.attachmentWords())
        await m.waitForTurn()
        let post = t.requests.first { $0.httpMethod == "POST" && $0.url?.path == "/api/turns" }
        let body = try? JSON.parse(post?.httpBody ?? Data())
        #expect(body?["attachments"] == .array([.object(["path": .string("uploads/photo.jpg")])]))
        #expect(body?["auto_text"] == .bool(true))
        let bubble = m.items.first { $0.isMine }?.asMsg
        #expect(bubble?.files.map(\.path) == ["uploads/photo.jpg"] && bubble?.autoText == true)
        #expect(m.attachments.isEmpty)
    }

    @MainActor @Test func aReloadBringsTheWaitingCardBackAndASkippedListLeavesItAlone() async {
        let listed = Box409()
        listed.codes = [1]
        let (m, _) = chat { method, path, _ in
            if path.hasPrefix("/api/messages") { return (200, #"[{"id": 1, "role": "user", "content": "날씨 알려줘"}]"#) }
            if path == "/api/approvals/pending" {
                return (200, listed.codes.isEmpty ? #"{"items": []}"#
                    : #"{"items": [{"type": "location_request", "approval_id": "a1", "state": "waiting", "reason": "For the weather"}]}"#)
            }
            return (200, "[]")
        }
        try? await m.reloadNow()
        #expect(await until { state(m, "a1") == "waiting" })
        // the status poll says nothing waits: the list is not fetched, so the card is not touched
        m.refreshAsks()
        #expect(state(m, "a1") == "waiting")
        // a fetched list without it ends it
        listed.codes = []
        m.refreshAsks(force: true)
        #expect(await until { state(m, "a1") == "expired" })
    }

    @MainActor private func state(_ m: ChatModel, _ id: String) -> String? {
        for i in m.items { if case .locationAsk(let l) = i, l.approvalId == id { return l.state } }
        return nil
    }
}

/// Answers in the order given (then 200).
private final class Box409: @unchecked Sendable {
    private let lock = NSLock()
    private var _codes: [Int] = []
    var codes: [Int] {
        get { lock.withLock { _codes } }
        set { lock.withLock { _codes = newValue } }
    }
    func next() -> Int { lock.withLock { _codes.isEmpty ? 200 : _codes.removeFirst() } }
}
