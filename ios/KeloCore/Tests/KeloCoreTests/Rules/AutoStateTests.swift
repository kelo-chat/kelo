import Foundation
import Testing
@testable import KeloCore

/// Whether a card pays on its own, why not, how many cards do, and what a schedule may do without asking, as the server sends them
/// (Android wallet/AutoStateTest.kt).
@Suite struct AutoStateTests {
    private func decode<T: Decodable>(_ type: T.Type, _ text: String) throws -> T { try JSONDecoder().decode(T.self, from: Data(text.utf8)) }

    private func card(_ extra: String) throws -> WalletCard {
        try decode(WalletCard.self, #"{"id":"c1","label":"Visa ••4242","brand":"visa","last4":"4242","usable":true,"expired":false,"# + extra + "}")
    }

    @Test func aCardSavedToAskEachTimeSaysSoEvenWhenTheWalletIsNotPaused() throws {
        let c = try card(#""auto_pay":false,"server_copy":false,"pays_automatically":false,"ask_reason":"not_auto","ask_reason_text":"saved to ask""#)
        #expect(!c.autoNow)
        #expect(c.reason == WalletCard.reasonNotAuto)
        #expect(c.askReasonText == "saved to ask")
        let info = try decode(WalletInfo.self, #"{"paused":false,"auto_cards":0,"cards":[]}"#)
        #expect(info.autoCount == 0)                       // the list never says "on" while no card pays on its own
        #expect(WalletAutoSummary.of(info) == WalletAutoSummary.none)
    }

    @Test func pausedAndLockedAreTheReasonsTheServerGives() throws {
        let paused = try card(#""auto_pay":true,"server_copy":true,"pays_automatically":false,"ask_reason":"paused""#)
        #expect(!paused.autoNow)
        #expect(paused.reason == WalletCard.reasonPaused)
        let locked = try card(#""auto_pay":true,"server_copy":true,"pays_automatically":false,"ask_reason":"vault_locked""#)
        #expect(locked.reason == WalletCard.reasonVaultLocked)
        #expect(try card(#""auto_pay":true,"server_copy":false,"pays_automatically":false,"ask_reason":"no_server_copy""#).reason == WalletCard.reasonNoServerCopy)
        #expect(try card(#""auto_pay":true,"server_copy":true,"pays_automatically":true,"ask_reason":"""#).autoNow)
    }

    @Test func anOlderServerWithoutTheFieldsFallsBackToTheSwitchAndThePause() throws {
        let on = try card(#""auto_pay":true,"server_copy":true"#)
        let off = try card(#""auto_pay":false"#)
        #expect(on.paysAutomatically == nil && on.autoNow)
        #expect(on.reason == "")
        #expect(off.reason == WalletCard.reasonNotAuto)
        #expect(WalletInfo(paused: false, cards: [on, off]).autoCount == 1)
        #expect(WalletInfo(paused: true, cards: [on, off]).autoCount == 0)
        #expect(WalletAutoSummary.of(WalletInfo(paused: false, cards: [on, off])) == .cards(1))
        #expect(WalletAutoSummary.of(WalletInfo(paused: true, cards: [on, off])) == .paused)
    }

    @Test func anExpiredOrUnfinishedCardNeverCountsAsPayingOnItsOwn() throws {
        let expired = try decode(WalletCard.self, #"{"id":"c","auto_pay":true,"pays_automatically":true,"usable":true,"expired":true}"#)
        let unfinished = try decode(WalletCard.self, #"{"id":"c","auto_pay":true,"pays_automatically":true,"usable":false}"#)
        #expect(!expired.autoNow && !expired.canPay)
        #expect(!unfinished.autoNow && !unfinished.canPay)
    }

    @Test func theListNotesFollowTheCount() throws {
        let asks = try card(#""auto_pay":false,"pays_automatically":false,"ask_reason":"not_auto""#)
        let pays = try card(#""auto_pay":true,"server_copy":true,"pays_automatically":true"#)
        let none = WalletInfo(paused: false, cards: [asks], autoCards: 0)
        #expect(WalletAutoSummary.showsNoneNote(none) && !WalletAutoSummary.showsFirstSiteRule(none))
        let some = WalletInfo(paused: false, cards: [asks, pays], autoCards: 1)
        #expect(!WalletAutoSummary.showsNoneNote(some) && WalletAutoSummary.showsFirstSiteRule(some))
        // no cards at all, or everything paused: no "no card pays" note
        #expect(!WalletAutoSummary.showsNoneNote(WalletInfo(paused: false, cards: [], autoCards: 0)))
        #expect(!WalletAutoSummary.showsNoneNote(WalletInfo(paused: true, cards: [asks], autoCards: 0)))
        // the server's count wins over counting here
        #expect(WalletInfo(paused: false, cards: [pays], autoCards: 0).autoCount == 0)
    }

    @Test func scheduleGrantsComeWithTheWalletAndTheLogin() throws {
        let info = try decode(WalletInfo.self, """
            {"paused":false,"cards":[],"auto_cards":0,"schedule_grants":[{"id":3,"schedule_id":4,"schedule_name":"Weekly order",
             "kind":"purchase","site":"example.com","what":"water","max_amount":20000,"currency":"KRW","monthly_cap":80000,
             "runs_per_month":4,"max_amount_text":"20,000원","monthly_cap_text":"80,000원","spent_this_month":20000}]}
            """)
        #expect(info.scheduleGrants.count == 1)
        let g = info.scheduleGrants[0]
        #expect(g.isPurchase && !g.isLogin)
        #expect(g.scheduleId == 4 && g.monthlyCapText == "80,000원" && g.spentThisMonth == 20000 && g.runsPerMonth == 4)
        let entry = try decode(VaultEntry.self, """
            {"name":"login-shop","kind":"login","schedule_grants":[{"id":5,"schedule_id":4,"kind":"login","site":"example.com",
             "login":"login-shop","login_label":"Shop"},{"kind":"login"}]}
            """)
        #expect(entry.scheduleGrants.map(\.kind) == [ScheduleGrant.kindLogin])   // an entry without its id is left out
        #expect(entry.scheduleGrants[0].loginName == "Shop")
        #expect(ScheduleGrant(id: 1, login: "login-x").loginName == "login-x")
        #expect(try decode(VaultEntry.self, #"{"name":"k","kind":"api"}"#).scheduleGrants.isEmpty)
    }

    @Test func grantsAreListedAndRevokedOnTheSchedulesAddress() async throws {
        let t = FakeTransport(.ok(200, #"{"items":[{"id":9,"schedule_id":4,"kind":"login","site":"coupang.com","login":"login-coupang"}]}"#),
                              .ok(200, #"{"ok":true}"#))
        let api = KeloApi(baseUrl: "https://k.example", token: "tok", transport: t)
        let list = try await api.scheduleGrants(4).items
        #expect(list.map(\.id) == [9])
        try await api.revokeScheduleGrant(list[0])
        let r = t.requests.value
        #expect(r.map { $0.httpMethod ?? "" } == ["GET", "DELETE"])
        #expect(r.map { $0.url?.path ?? "" } == ["/api/schedules/4/grants", "/api/schedules/4/grants/9"])
    }

    @Test func aSchedulePurchasePaidFromTheSitesBalanceComesWithoutACard() throws {
        let e = TurnEvent.parse(try JSON.parse(#"{"type":"payment_auto","id":"p","schedule_grant":{"id":3,"schedule_id":4,"via":"balance"},"payment":{"site":"shop.example","amount_text":"20,000원"}}"#))
        guard case .paymentAuto(let p) = e else { Issue.record("not payment_auto"); return }
        #expect(p.card == nil && p.site == "shop.example" && p.amountText == "20,000원")
    }

    @Test func moneyIsInTheCurrencysOwnUnits() {
        #expect(Money.decimals("KRW") == 0 && Money.decimals("usd") == 2)
        #expect(Money.number(20000, "KRW", locale: Locale(identifier: "en_US")) == "20,000")
    }
    // ---- nickname, default card, a card's own history (Android AutoStateTest)

    private func named(_ id: String, isDefault: Bool = false, usable: Bool = true) throws -> WalletCard {
        try decode(WalletCard.self, #"{"id":"\#(id)","label":"\#(id)","brand":"visa","last4":"4242","usable":\#(usable),"expired":false,"default":\#(isDefault)}"#)
    }

    @Test func theDefaultCardAndWhatDeletingItAsks() throws {
        let a = try named("a", isDefault: true), b = try named("b", usable: false), c = try named("c")
        #expect(DefaultCardRules.defaultCard([b, a, c])?.id == "a")
        #expect(DefaultCardRules.choicesOnDelete(c, [a, b, c]) == nil)            // not the default: just delete
        #expect(DefaultCardRules.choicesOnDelete(a, [a, c]) == nil)               // one left: the server makes it the default
        #expect(DefaultCardRules.choicesOnDelete(a, [a, b, c])?.map(\.id) == ["b", "c"])
        #expect(DefaultCardRules.suggested([b, c])?.id == "c")                     // a card that can pay is offered first
        #expect(!(try decode(WalletCard.self, #"{"id":"x"}"#)).isDefault)          // an older server: no default
    }

    @Test func theNicknameIsTheTitleWithBrandAndLast4Under() {
        #expect(CardMask.subtitle(label: "Everyday card", brandWord: "Visa", last4: "4242") == "Visa ••4242")
        #expect(CardMask.subtitle(label: "Visa ••4242", brandWord: "Visa", last4: "4242") == nil)   // saved without a nickname
        #expect(CardBrief(id: "c", label: "생활비 카드", brand: "mastercard", last4: "5100").subtitle == "Mastercard ••5100")
    }

    @Test func aCardsHistoryPagesNewestFirstAndLinksToItsChatOrTask() throws {
        func pay(_ id: Int64, _ source: String = "", _ task: Int64? = nil) -> WalletPayment { WalletPayment(id: id, taskId: task, source: source) }
        let shown = CardHistoryRules.merge([], WalletPayments(items: [pay(9), pay(8)], nextBeforeId: 8))
        let more = CardHistoryRules.merge(shown, WalletPayments(items: [pay(8), pay(7)]))
        #expect(more.map(\.id) == [9, 8, 7])
        #expect(CardHistoryRules.link(pay(1, "chat", 4)) == .chat)
        #expect(CardHistoryRules.link(pay(1, "schedule:3", 5)) == .task(5))
        #expect(CardHistoryRules.link(pay(1)) == PaymentLink.none)
        let parsed = try decode(WalletPayments.self, #"{"items":[{"id":3,"task_id":7,"source":"task:ab"}],"next_before_id":null}"#)
        #expect(parsed.nextBeforeId == nil && CardHistoryRules.link(parsed.items[0]) == .task(7))
        #expect(try decode(WalletPayments.self, #"{"items":[],"next_before_id":120}"#).nextBeforeId == 120)
    }
}
