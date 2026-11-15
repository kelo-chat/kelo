import Testing
import Foundation
import KeloCore
@testable import KeloShared
@testable import KeloChat

/// The in-chat address form (shipping_form) and the order card's parts, as the chat list holds them.
@Suite struct ShippingReducerTests {
    private func keys() -> () -> String {
        var n = 0
        return { n += 1; return "k\(n)" }
    }

    private func form(_ id: String, expires: Double? = nil) -> TurnEvent {
        .shippingForm(.init(id: "call", formId: id, site: "shop.example.com", what: "블루 머그 2개", country: "KR",
                            prefill: ["name": "홍길동"], label: "집", save: true, askPayment: true, expiresAt: expires))
    }

    private func shipping(_ items: [ChatItem]) -> [ChatItem.Shipping] {
        items.compactMap { if case .shipping(let s) = $0 { return s }; return nil }
    }

    @Test func aFormShowsOnceAndAResendKeepsItsRow() {
        let k = keys()
        var items = ChatReducer.apply([], form("sf_1"), newKey: k)
        #expect(shipping(items).count == 1 && shipping(items)[0].state == .open && shipping(items)[0].form.prefill["name"] == "홍길동")
        // the same form again (a resend): it keeps its row and its state (Android ChatReducer)
        items = ChatReducer.apply(items, form("sf_1"), newKey: k)
        #expect(shipping(items).count == 1 && shipping(items)[0].key == "k1")
        items = ChatReducer.apply(items, form("sf_2"), newKey: k)
        #expect(shipping(items).map(\.state) == [.open, .open])
    }

    @Test func itsToolEndingClosesAnOpenForm() {
        let k = keys()
        var items = ChatReducer.apply([], form("sf_1"), newKey: k)
        items = ChatReducer.apply(items, .toolResult(.init(id: "call", name: "ask_shipping", output: "answered", isError: false, approved: nil)), newKey: k)
        #expect(shipping(items).map(\.state) == [.closed])
    }

    @Test func theReplyEndingExpiresAnOpenForm() {
        let k = keys()
        var items = ChatReducer.apply([], form("sf_1"), newKey: k)
        items = ChatReducer.apply(items, form("sf_2"), newKey: k)
        items = ChatReducer.shipping(items, shipping(items)[1].key) { $0.state = .done; $0.doneLine = "집 · 테헤란로 152" }
        items = ChatReducer.apply(items, form("sf_3"), newKey: k)
        items = ChatReducer.endTurn(items)
        // a sent one stays sent; the open one can no longer be answered
        #expect(shipping(items).map(\.state) == [.expired, .done, .expired])
        #expect(shipping(items)[1].doneLine == "집 · 테헤란로 152")
    }

    @Test func theStatusLineKeepsWhatItSaid() {
        let p = ChatReducer.phase(Phase(.thinking, since: 1), form("sf_1"), now: 5)
        #expect(p?.kind == .thinking && p?.since == 1)
    }
}

@MainActor
private func shippingApp(_ route: @escaping @Sendable (String, String, Data?) -> (Int, String)) -> (AppModel, RouteTransport) {
    let t = RouteTransport(route)
    let defaults = UserDefaults(suiteName: "kelo-ship-test-\(UUID().uuidString)")!
    let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")), prefs: Prefs(defaults: defaults),
                       transport: t, cache: ScreenCache(directory: nil, key: { nil }))
    return (app, t)
}

private func waitFor(_ cond: @MainActor () -> Bool) async {
    for _ in 0..<300 {
        if await cond() { return }
        try? await Task.sleep(nanoseconds: 10_000_000)
    }
}

@Suite(.serialized) struct ShippingModelTests {
    private static let event = TurnEvent.shippingForm(.init(id: "call_5", formId: "sf_7c1e2a", site: "shop.example.com", what: "블루 머그 2개",
                                                            country: "KR", label: "집", save: true, askPayment: true, expiresAt: nil))

    @MainActor private func card(_ m: ChatModel) -> ChatItem.Shipping? {
        m.items.compactMap { if case .shipping(let s) = $0 { return s }; return nil }.first
    }

    @MainActor @Test func sendingFoldsTheCardAndOpensTheCardFormForCard() async {
        let (app, t) = shippingApp { method, path, _ in
            if method == "POST" && path == "/api/shipping-forms/sf_7c1e2a" {
                return (200, #"{"ok":true,"profile":{"id":"ship_1","label":"집","country":"KR","one_line":"서울특별시 강남구 테헤란로 152, 06236"},"formatted":"x"}"#)
            }
            return (200, "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.apply(Self.event)
        let key = card(m)!.key
        m.answerShipping(key, address: ["country": "KR", "name": "홍길동"], save: true, label: "집", payment: "card")
        #expect(card(m)?.sending == true)
        await waitFor { card(m)?.state == .done }
        #expect(card(m)?.doneLine == "집 · 서울특별시 강남구 테헤란로 152, 06236")
        let post = t.requests.first { $0.url?.path == "/api/shipping-forms/sf_7c1e2a" }
        let body = try? JSON.parse(post?.httpBody ?? Data())
        #expect(body?["save"] == .bool(true) && body?["label"] == .string("집") && body?["payment"] == .string("card"))
        #expect(body?["address"]?["name"] == .string("홍길동"))
        // "Add a card": the wallet's own card form opens
        #expect(app.router.myPageRequest == .walletAdd)
        // a second tap on a folded card sends nothing
        m.answerShipping(key, address: [:], save: false, label: "", payment: nil)
        try? await Task.sleep(nanoseconds: 50_000_000)
        #expect(t.requests.filter { $0.url?.path == "/api/shipping-forms/sf_7c1e2a" }.count == 1)
    }

    @MainActor @Test func notSavedUsesTheFormattedAddress() async {
        let (app, _) = shippingApp { method, path, _ in
            if method == "POST" && path.hasPrefix("/api/shipping-forms/") {
                return (200, #"{"ok":true,"profile":null,"formatted":"Jane Roe\n1600 Amphitheatre Pkwy\nMOUNTAIN VIEW, CA 94043"}"#)
            }
            return (200, "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.apply(Self.event)
        m.answerShipping(card(m)!.key, address: ["country": "US"], save: false, label: "Home", payment: "site")
        await waitFor { card(m)?.state == .done }
        #expect(card(m)?.doneLine == "Jane Roe, 1600 Amphitheatre Pkwy, MOUNTAIN VIEW, CA 94043")
        #expect(app.router.myPageRequest == nil)
    }

    @MainActor @Test func aWrongFieldKeepsTheFormOpenAndAGoneFormExpires() async {
        let gone = LockedFlag()
        let (app, _) = shippingApp { method, path, _ in
            if method == "POST" && path.hasPrefix("/api/shipping-forms/") {
                return gone.value ? (404, #"{"detail":"no such form"}"#) : (422, #"{"detail":"postal_code '1' is not a postal code of KR (e.g. 03051)"}"#)
            }
            return (200, "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.apply(Self.event)
        let key = card(m)!.key
        m.answerShipping(key, address: ["country": "KR"], save: true, label: "집", payment: nil)
        await waitFor { card(m)?.sending == false }
        #expect(card(m)?.state == .open)
        #expect(card(m)?.error?.contains("postal_code") == true)
        gone.value = true
        m.answerShipping(key, address: ["country": "KR"], save: true, label: "집", payment: nil)
        await waitFor { card(m)?.state != .open }
        #expect(card(m)?.state == .expired)
    }

    @MainActor @Test func cancelTellsTheAgentAndTimeRunsOut() async {
        let (app, t) = shippingApp { _, _, _ in (200, #"{"ok":true}"#) }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.apply(Self.event)
        m.cancelShipping(card(m)!.key)
        #expect(card(m)?.state == .cancelled)
        await waitFor { t.requests.contains { $0.url?.path == "/api/shipping-forms/sf_7c1e2a" } }
        let body = t.requests.first { $0.url?.path == "/api/shipping-forms/sf_7c1e2a" }?.httpBody
        #expect(String(decoding: body ?? Data(), as: UTF8.self).contains("\"cancel\":true"))
        let other = ChatModel(app: app, uploader: FakeUploader())
        other.apply(Self.event)
        other.expireShipping(card(other)!.key)
        #expect(card(other)?.state == .expired)
        other.cancelShipping(card(other)!.key)   // nothing to cancel any more
        #expect(card(other)?.state == .expired)
    }
}

final class LockedFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var v = false
    var value: Bool {
        get { lock.withLock { v } }
        set { lock.withLock { v = newValue } }
    }
}
