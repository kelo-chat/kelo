import Testing
import Foundation
import KeloCore
@testable import KeloShared

/// Answers POST /api/secret-requests/{id}/card from a fixed status; every request is kept.
private final class CardAnswerTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var log: [(String, String, String)] = []
    let status: Int
    let body: String
    init(status: Int, body: String) { self.status = status; self.body = body }
    var calls: [(String, String, String)] { lock.withLock { log } }

    func send(_ request: URLRequest) async throws -> (Int, Data) {
        let sent = request.httpBody.map { String(decoding: $0, as: UTF8.self) } ?? ""
        lock.withLock { log.append((request.httpMethod ?? "", request.url!.path, sent)) }
        return (status, Data(body.utf8))
    }

    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let (code, data) = try await send(request)
        return (code, AsyncThrowingStream { c in for b in data { c.yield(b) }; c.finish() })
    }
}

struct SecretEntryWordsTests {
    private let key = SecretRequest(id: "r1", kind: "key", name: "openweather", label: "OpenWeather",
                                    hosts: ["api.openweathermap.org"], reason: "To check the forecast", check: true)
    private let login = SecretRequest(id: "r2", kind: SecretRequest.kindLogin, name: "shop", hosts: ["shop.example.com", "id.example.com"])
    private let card = SecretRequest(id: "r3", kind: SecretRequest.kindCard, label: "Concert tickets", hosts: [], reason: "Concert tickets")

    @Test func titleNamesTheKeyTheSiteOrTheCard() {
        #expect(SecretEntryWords.title(key).0 == R.string.secret_title_key)
        #expect(SecretEntryWords.title(key).1 == "OpenWeather")
        var unnamed = key; unnamed.label = ""
        #expect(SecretEntryWords.title(unnamed).1 == "openweather")
        #expect(SecretEntryWords.title(login).0 == R.string.secret_title_login)
        #expect(SecretEntryWords.title(login).1 == "shop.example.com")   // no label: the first host
        #expect(SecretEntryWords.title(card).0 == R.string.secret_title_card)
        #expect(SecretEntryWords.title(card).1 == nil)
    }

    @Test func reasonIsLeftOutWhenTheLabelSaysIt() {
        #expect(SecretEntryWords.reason(key) == "To check the forecast")
        #expect(SecretEntryWords.reason(card) == "Concert tickets")   // reason == label: the label once
        var plain = card; plain.reason = ""
        #expect(SecretEntryWords.reason(plain) == "Concert tickets")
        #expect(SecretEntryWords.reason(login) == nil)
    }

    @Test func destinationSaysWhereTheValueGoes() {
        #expect(SecretEntryWords.destination(key) == (R.string.secret_hosts_only, "api.openweathermap.org"))
        #expect(SecretEntryWords.destination(login).1 == "shop.example.com, id.example.com")
        #expect(SecretEntryWords.destination(card) == (R.string.secret_card_wallet, nil))
        var site = card; site.hosts = ["tickets.example.com"]
        #expect(SecretEntryWords.destination(site) == (R.string.secret_card_site, "tickets.example.com"))
    }

    @Test func anAnsweredEntryFoldsIntoOneLine() {
        var k = key; k.state = SecretRequest.stateSaved; k.result = SecretResult(checked: "ok")
        #expect(SecretEntryWords.folded(k).key == R.string.secret_done_checked)
        k.result = SecretResult(checked: "unchecked")
        #expect(SecretEntryWords.folded(k).key == R.string.secret_done_key)
        #expect(SecretEntryWords.folded(k).arg == "OpenWeather")
        #expect(!SecretEntryWords.folded(k).muted)
        var l = login; l.state = SecretRequest.stateSaved
        #expect(SecretEntryWords.folded(l).key == R.string.secret_done_login)
        #expect(SecretEntryWords.folded(l).arg == "shop")
        var c = card; c.state = SecretRequest.stateSaved; c.result = SecretResult(card: SecretCard(id: "c1", label: "Blue card"))
        #expect(SecretEntryWords.folded(c).key == R.string.secret_done_card)
        #expect(SecretEntryWords.folded(c).arg == "Blue card")
        c.result = SecretResult(card: SecretCard(id: "c1"))
        #expect(SecretEntryWords.folded(c).arg == "Concert tickets")
        var x = key; x.state = SecretRequest.stateCancelled
        #expect(SecretEntryWords.folded(x).key == R.string.secret_cancelled)
        #expect(SecretEntryWords.folded(x).muted)
        for s in [SecretRequest.stateExpired, SecretRequest.stateReplaced] {
            x.state = s
            #expect(SecretEntryWords.folded(x).key == R.string.secret_gone)
            #expect(SecretEntryWords.folded(x).icon == .clock)
        }
    }

    @Test func kindLineAndSaveLabel() {
        #expect(SecretEntryWords.kindLine(key) == (R.string.secret_kind_key, "api.openweathermap.org"))
        #expect(SecretEntryWords.kindLine(login).0 == R.string.secret_kind_login)
        #expect(SecretEntryWords.kindLine(card) == (R.string.secret_kind_card, ""))
        #expect(SecretEntryWords.saveLabel(busy: false, check: true) == R.string.save)
        #expect(SecretEntryWords.saveLabel(busy: true, check: true) == R.string.secret_checking)
        #expect(SecretEntryWords.saveLabel(busy: true, check: false) == R.string.vault_saving)
    }

    @Test func everyWordHasATranslation() {
        // the keys the entry uses exist in every language the app ships (a missing one would show its raw name)
        let keys: [StringKey] = [R.string.secret_title_key, R.string.secret_title_login, R.string.secret_title_card, R.string.secret_hosts_only,
                                 R.string.secret_card_site, R.string.secret_card_wallet, R.string.secret_check_note, R.string.secret_field_key,
                                 R.string.secret_checking, R.string.secret_never_chat, R.string.secret_done_key,
                                 R.string.secret_done_checked, R.string.secret_done_login, R.string.secret_done_card, R.string.secret_cancelled,
                                 R.string.secret_gone, R.string.secret_waiting_title, R.string.secret_waiting_help, R.string.secret_kind_key,
                                 R.string.secret_kind_login, R.string.secret_kind_card, R.string.secret_fill, R.string.secret_card_next,
                                 R.string.secret_failed, R.string.secret_show, R.string.secret_hide, R.string.secret_enter]
        let base = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/KeloShared/Resources")
        let langs = (try? FileManager.default.contentsOfDirectory(atPath: base.path))?.filter { $0.hasSuffix(".lproj") } ?? []
        #expect(langs.count >= 20)
        for lang in langs {
            let text = (try? String(contentsOf: base.appendingPathComponent(lang).appendingPathComponent("Localizable.strings"), encoding: .utf8)) ?? ""
            for k in keys { #expect(text.contains("\"\(k.name)\" = "), "\(lang) lacks \(k.name)") }
        }
    }
}

@Suite(.serialized) struct SecretCardWaitTests {
    private func api(_ t: CardAnswerTransport) -> KeloApi { KeloApi(baseUrl: "https://kelo.test", token: "tok", transport: t) }

    @MainActor @Test func aCardForAWaitingEntryAnswersIt() async {
        let t = CardAnswerTransport(status: 200, body: #"{"ok": true, "turn_id": "t9"}"#)
        let w = SecretCardWait()
        w.start("r3", returnTo: .chat)
        #expect(w.waiting == "r3")
        let back = await w.cardAdded(SecretCard(id: "c1", label: "Blue"), api: api(t))
        #expect(back == .chat)
        #expect(w.waiting == nil)
        #expect(w.tick == 1)
        #expect(t.calls.count == 1)
        #expect(t.calls[0].0 == "POST" && t.calls[0].1 == "/api/secret-requests/r3/card")
        #expect(t.calls[0].2.contains("\"card_id\":\"c1\"") || t.calls[0].2.contains("\"card_id\" : \"c1\""))
        #expect(!t.calls[0].2.contains("4242"))   // only the id goes, never the card's digits
        let o = w.take("r3")
        #expect(o?.kind == .saved)
        #expect(o?.turnId == "t9")
        #expect(w.take("r3") == nil)   // once
    }

    @MainActor @Test func nothingWaitingSendsNothing() async {
        let t = CardAnswerTransport(status: 200, body: "{}")
        let w = SecretCardWait()
        let back = await w.cardAdded(SecretCard(id: "c1"), api: api(t))
        #expect(back == nil)
        #expect(t.calls.isEmpty)
        #expect(w.tick == 0)
    }

    @MainActor @Test func aGoneEntryAndAFailureAreToldApart() async {
        let w = SecretCardWait()
        w.start("r3")
        _ = await w.cardAdded(SecretCard(id: "c1"), api: api(CardAnswerTransport(status: 404, body: #"{"detail": "no such request"}"#)))
        #expect(w.take("r3")?.kind == .gone)
        w.start("r4")
        _ = await w.cardAdded(SecretCard(id: "c1"), api: api(CardAnswerTransport(status: 422, body: #"{"detail": "this request is not for a card"}"#)))
        let o = w.take("r4")
        #expect(o?.kind == .failed)
        #expect(o?.detail == "this request is not for a card")
    }

    @MainActor @Test func leavingTheFormStopsTheWait() {
        let w = SecretCardWait()
        w.start("r3", returnTo: .chat)
        w.stop()
        #expect(w.waiting == nil && w.returnTo == nil)
    }

    @Test func theAddedCardIsReadFromTheWalletAnswer() {
        let j = JSON.object(["id": .string("c7"), "label": .string("Blue"), "brand": .string("visa"), "last4": .string("4242"), "default": .bool(true)])
        let c = SecretCardWait.card(from: j)
        #expect(c == SecretCard(id: "c7", label: "Blue"))
        let wrapped = SecretCardWait.card(from: .object(["card": j]))
        #expect(wrapped.id == "c7")
    }
}
